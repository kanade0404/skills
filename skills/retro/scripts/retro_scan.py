#!/usr/bin/env python3
# retro_scan.py — retro quantitative pre-scan over Claude Code transcripts.
#
# Aggregates the countable side of retro's 8-point scan (tool stats, denials,
# dispatches, retries, skill fires, error taxonomy, interventions,
# token/blocking hotspots) across one or many sessions with DuckDB, so the
# analysis subagent spends its context on qualitative judgment (skill 不発/暴発,
# escalation narrative) instead of hand-rolling per-file jq pipelines.
# Measured motivation: a manual jq-based cross-session scan cost ~94k tokens /
# 37 tool calls / 12 min and tripped read-only command deny-hooks 5 times; the
# same aggregation here runs in ~1-2 s (including uv startup) and returns a
# bounded report.
#
# Run (no install; duckdb is fetched into an ephemeral env):
#   uv run --with duckdb python3 retro_scan.py [options]
#
# Options:
#   --transcript FILE      explicit transcript(s); repeatable; overrides discovery.
#                          Each file's <stem>/subagents/**/*.jsonl siblings are
#                          auto-included so sub_n / skill_reads stay populated
#   --project-dir DIR      project to derive the slug from (default: cwd, with a
#                          /.claude/worktrees/<name> suffix stripped so worktree
#                          sessions resolve to their parent project)
#   --projects-root DIR    default: ~/.claude/projects
#   --all-projects         scan every project under the root
#   --since YYYY-MM-DD     keep only files modified on/after this date
#   --max-rows N           row cap for the larger tables (default 15; small
#                          grouped tables — corpus, skill_fires, dispatches,
#                          errors — are always returned in full)
#   --json                 machine-readable output instead of markdown
#   --latest               print the newest main-session transcript path and exit
#   --pr N                 PR / review-loop mode: fetch PR N's review threads via
#                          gh GraphQL (cursor pagination, all pages), pre-classify
#                          each thread's terminal state from reply-body patterns,
#                          and report per-PR breakdowns plus a cross-PR
#                          dir-by-class recurrence table. Repeatable. Standalone
#                          by default: transcripts are scanned alongside only when
#                          --transcript / --all-projects / --since is also given
#   --repo OWNER/NAME      repository for --pr (default: gh repo view)
#
# Any explicitly passed discovery flag that the selected scan path would not
# consume (e.g. --project-dir next to standalone --pr or next to --transcript,
# --all-projects next to --transcript, --since next to --transcript) is
# rejected up front by validate_flag_combinations() instead of being silently
# ignored — the check is a set comparison (explicit flags vs consumed flags),
# so the whole dead-flag class fails closed, not just known pairs.
#
# Corpus discovery globs <root>/<slug>*/**/*.jsonl so worktree-session dirs
# (slug prefix + suffix) and subagent transcripts (<session>/subagents/**)
# are included; files under a /subagents/ path are tallied separately.
#
# duckdb is imported lazily inside the transcript scan so that --pr mode and
# the pure thread-classification function work without duckdb installed.
import argparse
import glob
import json
import os
import re
import sys


def project_path(path):
    """Canonical project path: absolute, with a /.claude/worktrees/<name>
    suffix stripped. Worktree cwds are still "this project's" history, so
    scans resolve to the parent project."""
    path = os.path.abspath(path)
    m = re.match(r"(.*?)/\.claude/worktrees/[^/]+$", path)
    if m:
        path = m.group(1)
    return path


def project_slug(path):
    return project_path(path).replace("/", "-").replace(".", "-")


def _transcript_cwd(path, max_lines=100):
    """First 'cwd' recorded in the leading lines, or None. Measured over a
    real ~/.claude/projects corpus (800 files): every session transcript
    carries cwd within its first 28 lines; the only cwd-less files are
    journal.jsonl bookkeeping files that hold no message/tool data."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(max_lines):
                line = fh.readline()
                if not line:
                    return None
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("cwd"):
                    return rec["cwd"]
    except OSError:
        pass
    return None


def _cwd_in_project(cwd, project):
    # Fail closed: a file whose cwd cannot be established is not counted as
    # this project's history (slug collisions would otherwise leak another
    # project's sessions in). Explicit --transcript paths bypass this check,
    # and discovery already exits loudly when the corpus comes up empty.
    if not cwd:
        return False
    cwd = os.path.abspath(cwd)
    return cwd == project or cwd.startswith(project + os.sep)


def discover(args):
    if args.transcript:
        # Dead-flag combinations (e.g. --since or --project-dir next to an
        # explicit file list) are rejected before discovery ever runs — see
        # validate_flag_combinations().
        files = []
        for f in args.transcript:
            p = os.path.abspath(f)
            if not os.path.isfile(p):
                sys.exit(f"--transcript file not found: {p}")
            files.append(p)
            # Subagent transcripts for a session live beside it under
            # <dirname>/<session-stem>/subagents/**/*.jsonl. Auto-include them
            # so single-session scans (e.g. pr-monitor → --transcript <origin>)
            # still report sub_n / skill_reads instead of silently showing 0.
            # No such directory → no matches → nothing added (not an error).
            stem = os.path.splitext(p)[0]
            files.extend(
                glob.glob(f"{glob.escape(stem)}/subagents/**/*.jsonl",
                          recursive=True)
            )
        return sorted(set(files))
    root = os.path.expanduser(args.projects_root)
    if args.all_projects:
        files = glob.glob(f"{root}/*/**/*.jsonl", recursive=True)
    else:
        slug = project_slug(args.project_dir)
        files = glob.glob(f"{root}/{glob.escape(slug)}*/**/*.jsonl", recursive=True)
        # {slug}* also matches sibling projects whose slug merely extends this
        # one (e.g. slug -a-b vs project -a-bc). Keep only this project's own
        # dir and its worktree-session dirs (<slug>--claude-worktrees-<name>).
        keep = re.compile(re.escape(slug) + r"(--claude-worktrees-.+)?$")
        files = [
            f for f in files
            if keep.fullmatch(os.path.relpath(f, root).split(os.sep)[0])
        ]
        # The slug is lossy ('/' and '.' both map to '-'), so sibling projects
        # like acme.prod and acme-prod share one storage dir name. Keep a
        # transcript only if its own recorded cwd resolves into this project
        # (fail closed: cwd-less files are excluded — measured, those are only
        # journal.jsonl bookkeeping files, never session transcripts).
        project = project_path(args.project_dir)
        files = [f for f in files if _cwd_in_project(_transcript_cwd(f), project)]
    if args.since:
        import datetime

        try:
            cutoff = datetime.datetime.strptime(args.since, "%Y-%m-%d").timestamp()
        except ValueError:
            sys.exit(f"--since must be YYYY-MM-DD, got: {args.since!r}")
        files = [f for f in files if os.path.getmtime(f) >= cutoff]
    return sorted(set(files))


def standalone_pr(args):
    """True when --pr runs standalone (review-loop scan only, no transcript
    scan): no transcript-corpus flag was passed next to it. Shared by
    validate_flag_combinations() and main() so the two can't drift."""
    return bool(args.pr) and not (args.transcript or args.all_projects
                                  or args.since)


def validate_flag_combinations(args):
    """Reject every explicitly passed discovery flag that the selected scan
    path would not consume (fail closed: silently ignoring an explicit flag
    would let the wrong data source pass unnoticed, so this rejects loudly
    instead).

    Written as one set comparison — the set of explicitly passed discovery
    flags vs the set the selected path actually consumes — so the whole
    "explicit flag silently ignored" class is closed at once, instead of
    growing one pairwise check per rediscovered combination. The path table
    mirrors main()/discover() exactly:

      standalone --pr   (no transcript scan)  consumes nothing
      --transcript      (explicit file list)  consumes --transcript only;
                        files are always scanned as-is, so --since /
                        --project-dir / --projects-root / --all-projects
                        cannot contribute
      --all-projects    (whole projects root) consumes --all-projects,
                        --projects-root, --since; --project-dir cannot
                        contribute (no slug is derived)
      slug discovery    (default)             consumes --project-dir,
                        --projects-root, --since
    """
    explicit = set()
    if args.transcript:
        explicit.add("--transcript")
    if args.project_dir is not None:
        explicit.add("--project-dir")
    if args.projects_root is not None:
        explicit.add("--projects-root")
    if args.all_projects:
        explicit.add("--all-projects")
    if args.since is not None:
        explicit.add("--since")

    if standalone_pr(args):
        path, consumed = "standalone --pr (no transcript scan runs)", set()
    elif args.transcript:
        path, consumed = ("--transcript (explicit files are scanned as-is)",
                          {"--transcript"})
    elif args.all_projects:
        path, consumed = ("--all-projects",
                          {"--all-projects", "--projects-root", "--since"})
    else:
        path, consumed = ("project-slug discovery",
                          {"--project-dir", "--projects-root", "--since"})

    dead = sorted(explicit - consumed)
    if dead:
        # Name only what is true of the selected path — never point at
        # alternatives that would leave the flag equally dead.
        sys.exit(
            f"{', '.join(dead)}: no effect on the selected scan path "
            f"[{path}], which consumes "
            f"{', '.join(sorted(consumed)) if consumed else 'no discovery flags'}"
            " — refusing to silently ignore explicit flags; drop them or use"
            " a scan path that consumes them (see --help)"
        )


# Pattern marking a tool_result as a permission denial / hook block; shared by
# the `denials` and `errors` queries and the self-reported counting notes.
DENY_PAT = "禁止コマンド|permission|Permission|denied|doesn't want to proceed"
_DENY_SQL = DENY_PAT.replace("'", "''")

SQL = {
    # Every query reads from the `tu` / `tr` / `msg` views defined in main().
    "corpus": """
        SELECT
          count(DISTINCT fn) AS files,
          count(DISTINCT CASE WHEN NOT is_sub THEN fn END) AS main_files,
          count(DISTINCT CASE WHEN is_sub THEN fn END) AS sub_files,
          min(ts) AS first_ts, max(ts) AS last_ts
        FROM msg
    """,
    "tools": """
        SELECT tool,
               count(*) FILTER (NOT is_sub) AS main_n,
               count(*) FILTER (is_sub) AS sub_n,
               count(*) AS total
        FROM tu GROUP BY tool ORDER BY total DESC LIMIT ?
    """,
    "skill_fires": """
        SELECT json_extract_string(input, '$.skill') AS skill,
               count(*) FILTER (NOT is_sub) AS main_n,
               count(*) FILTER (is_sub) AS sub_n
        FROM tu WHERE tool = 'Skill' GROUP BY 1 ORDER BY main_n + sub_n DESC
    """,
    "skill_reads": """
        SELECT regexp_extract(json_extract_string(input, '$.file_path'),
                              '([^/]+)/SKILL\\.md$', 1) AS skill,
               count(*) AS reads
        FROM tu
        WHERE tool = 'Read'
          AND is_sub
          AND json_extract_string(input, '$.file_path') LIKE '%/SKILL.md'
        GROUP BY 1 ORDER BY reads DESC LIMIT ?
    """,
    "dispatches": """
        SELECT coalesce(json_extract_string(input, '$.subagent_type'), '(default)') AS subagent_type,
               count(*) AS n
        FROM tu WHERE tool IN ('Task', 'Agent') GROUP BY 1 ORDER BY n DESC
    """,
    "denials": f"""
        SELECT coalesce(u.tool, '(unknown)') AS tool,
               CASE WHEN u.tool = 'Bash'
                    THEN split_part(trim(json_extract_string(u.input, '$.command')), ' ', 1)
                    ELSE '' END AS command_head,
               count(*) AS n
        FROM tr r LEFT JOIN tu u ON r.tool_use_id = u.id AND r.fn = u.fn
        WHERE r.is_error
          AND regexp_matches(r.text, '{_DENY_SQL}')
        GROUP BY 1, 2 ORDER BY n DESC LIMIT ?
    """,
    "retries": """
        SELECT session, left(regexp_replace(cmd, '\\s+', ' ', 'g'), 80) AS command, count(*) AS n
        FROM (SELECT session, json_extract_string(input, '$.command') AS cmd
              FROM tu WHERE tool = 'Bash')
        WHERE cmd IS NOT NULL
        GROUP BY session, cmd HAVING count(*) >= 3 ORDER BY n DESC LIMIT ?
    """,
    # Failure taxonomy over is_error tool_results. Categories follow the
    # community-established Claude Code error taxonomy (sniffly, Chip Huyen);
    # patterns are written against this corpus, not copied.
    "errors": f"""
        SELECT CASE
            WHEN regexp_matches(text, '{_DENY_SQL}')
              THEN 'hook/permission-block'
            WHEN regexp_matches(text, 'Request interrupted by user') THEN 'user-interruption'
            WHEN regexp_matches(text, 'has not been read yet') THEN 'file-not-read-first'
            WHEN regexp_matches(text, 'does not exist|not found|No such file|command not found')
              THEN 'not-found'
            WHEN regexp_matches(text, 'String to replace|old_string|not unique|No changes')
              THEN 'edit-mismatch'
            WHEN regexp_matches(text, 'required schema|InputValidationError') THEN 'schema/input-error'
            WHEN regexp_matches(text, 'timed out|Timeout|TimeoutExpired') THEN 'timeout'
            WHEN regexp_matches(text, 'Exit code') THEN 'nonzero-exit'
            ELSE 'other' END AS error_class,
            count(*) FILTER (NOT is_sub) AS main_n,
            count(*) FILTER (is_sub) AS sub_n,
            count(*) AS total
        FROM tr WHERE is_error
        GROUP BY 1 ORDER BY total DESC
    """,
    # Intervention rate and steps-per-prompt: how often a human had to stop or
    # redirect the agent ("interruption rate is the new build time").
    "interventions": """
        WITH p AS (
            SELECT session,
                   count(*) FILTER (is_prompt) AS user_prompts,
                   count(*) FILTER (is_interrupt) AS interruptions,
                   count(*) FILTER (is_compact) AS compactions
            FROM msg WHERE NOT is_sub GROUP BY session
        ),
        t AS (SELECT session, count(*) AS tool_uses FROM tu WHERE NOT is_sub GROUP BY session)
        SELECT p.session, p.user_prompts, p.interruptions, p.compactions,
               coalesce(t.tool_uses, 0) AS tool_uses,
               round(coalesce(t.tool_uses, 0)::DOUBLE / nullif(p.user_prompts, 0), 1) AS steps_per_prompt
        FROM p LEFT JOIN t USING (session)
        ORDER BY p.interruptions DESC, tool_uses DESC LIMIT ?
    """,
    "sessions": """
        SELECT m.session,
               min(m.ts) AS first_ts, max(m.ts) AS last_ts,
               count(*) FILTER (m.type = 'assistant') AS assistant_turns,
               sum(m.out_tok) AS output_tokens,
               sum(m.cache_create) AS cache_create_tokens,
               round(sum(m.cache_read)::DOUBLE
                     / nullif(sum(m.cache_read) + sum(m.cache_create), 0), 2) AS cache_read_ratio,
               count(*) FILTER (m.type = 'assistant'
                 AND EXISTS (SELECT 1 FROM tu WHERE tu.fn = m.fn
                             AND tu.tool = 'ScheduleWakeup')) > 0 AS has_wakeups
        FROM msg m
        WHERE NOT m.is_sub
        GROUP BY m.session ORDER BY output_tokens DESC NULLS LAST LIMIT ?
    """,
    "blocking": """
        SELECT session,
               count(*) FILTER (tool = 'ScheduleWakeup') AS wakeups,
               count(*) FILTER (tool = 'Bash' AND
                 regexp_matches(json_extract_string(input, '$.command'),
                                '(^|[;&|(]\\s*)sleep\\s+[0-9]')) AS sleep_cmds
        FROM tu GROUP BY session
        HAVING wakeups > 0 OR sleep_cmds > 0 ORDER BY wakeups + sleep_cmds DESC LIMIT ?
    """,
}


# Transcript-derived values (commands, sessions, timestamps) are untrusted
# display input: a stray '|' breaks table cells, CR/LF splits rows, and
# ANSI/C0 control sequences can spoof terminal output for the reader.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def strip_controls(v):
    """ANSI/C0 removal only (no markdown pipe escaping). Applied where
    untrusted text feeds both the markdown and --json output paths, so JSON
    consumers never receive raw control sequences either."""
    s = _ANSI_RE.sub("", str(v))
    s = re.sub(r"[\r\n]+", " ", s)
    return _CTRL_RE.sub("", s)


def scrub_value(v):
    """Scrub one SQL result cell: string values are transcript-derived
    (commands, session names, timestamps, skill names) and feed both the
    markdown and --json output paths, so controls are stripped once here —
    JSON consumers must never receive raw ANSI/C0 either. Non-strings
    (counts, ratios, booleans, None) pass through untouched."""
    return strip_controls(v) if isinstance(v, str) else v


def sanitize_cell(v):
    if v is None:
        return ""
    return strip_controls(v).replace("|", "\\|")


def render_table(rows):
    if not rows:
        print("(none)")
        return
    cols = list(rows[0])
    print("| " + " | ".join(sanitize_cell(c) for c in cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(sanitize_cell(r[c]) for c in cols) + " |")


# --- PR / review-loop mode (--pr) -------------------------------------------
# Terminal-state pre-read of PR review threads. Class names follow the
# pr-review-respond terminal vocabulary. The classification is a mechanical
# pre-read from reply-body patterns; the retro evaluation subagent confirms or
# overrides every class before anything is proposed.

_FIXED_RE = re.compile(r"fixed\s+in\s+`?[0-9a-f]{7,40}\b", re.I)
_PUSHBACK_RE = re.compile(
    r"maintainer\s*判断|self-resolve\s*しません|pushback", re.I)
# F11: the original pattern required the literal word "issue(s)" immediately
# before "#NNN", so defer replies written as "Tracked in #114" / "Deferred to
# #113" (no "issue" word, no /issues/ URL) matched nothing and the thread fell
# through to UNTERMINATED even though it had a real terminal defer reply.
# Widened to also match "#NNN" co-occurring with a defer/reference context
# word (tracked/deferred/follow-up/see/ref/→) within a short window directly
# before it — proximity-bound so a stray "#123" elsewhere in the reply (a
# cross-referenced PR number, a code-comment ticket number) does not
# false-positive just because the reply separately contains defer wording
# (_DEFER_RE is checked independently over the whole reply by classify_thread;
# only this proximity requirement is what keeps overmatching bounded).
_ISSUE_REF_RE = re.compile(
    r"/issues/\d+"
    r"|\bissues?\s+#\d+"
    r"|\b(?:track(?:ed|ing)?|defer(?:red|ring)?|follow[- ]?up|see|ref(?:erence)?d?)\b"
    r"[^\n#]{0,20}#\d+"
    r"|→\s*#\d+",
    re.I,
)
_DEFER_RE = re.compile(r"defer|follow[- ]?up|後続|別途|追跡|track", re.I)
# Tradeoff: a fix reply that merely cross-references another thread
# (#discussion_r...) is misread as DUPLICATE — accepted for this pre-read;
# the evaluation subagent overrides (pinned in test_retro_pr_classification).
_DUPLICATE_RE = re.compile(
    r"duplicate|重複|既存スレッド|#discussion_r\d+", re.I)
_WITHDRAWN_RE = re.compile(r"withdraw|撤回", re.I)

THREAD_CLASSES = ("VALID", "INVALID_PUSH", "VALID_DEFER", "DUPLICATE",
                  "WITHDRAWN", "UNTERMINATED")


def classify_thread(comment_bodies):
    """Pure terminal-state classifier for one review thread (no I/O).

    comment_bodies: comment body strings in thread order. The first element is
    the finding itself and is never classification evidence (a finding that
    quotes "Fixed in <sha>" must not classify itself). Replies are examined
    newest-first: the latest reply carrying a recognizable terminal marker
    decides the class. Within one reply, precedence is
    WITHDRAWN > DUPLICATE > VALID > VALID_DEFER > INVALID_PUSH:
      - DUPLICATE beats VALID because duplicate replies routinely quote the
        canonical thread's fixing SHA.
      - VALID beats VALID_DEFER: a "Fixed in <sha>" that also opens a
        follow-up issue is still the fix terminal, while a pure defer reply
        (issue reference + defer wording, no SHA) stays VALID_DEFER.
    No marker in any reply -> UNTERMINATED, regardless of the thread's
    resolved flag (reported separately by the caller; "resolved yet
    UNTERMINATED" flags a reply-discipline violation worth inspecting).
    """
    for body in reversed(list(comment_bodies)[1:]):
        text = body or ""
        if _WITHDRAWN_RE.search(text):
            return "WITHDRAWN"
        if _DUPLICATE_RE.search(text):
            return "DUPLICATE"
        if _FIXED_RE.search(text):
            return "VALID"
        if _ISSUE_REF_RE.search(text) and _DEFER_RE.search(text):
            return "VALID_DEFER"
        if _PUSHBACK_RE.search(text):
            return "INVALID_PUSH"
    return "UNTERMINATED"


_GH_TIMEOUT = 60
_MAX_PAGES = 200  # hard bound on pagination loops (fail closed, never spin)

_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      headRefName
      reviewThreads(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved path
          comments(first: 100) {
            pageInfo { hasNextPage endCursor }
            nodes { body }
          }
        }
      }
    }
  }
}
"""

_MORE_COMMENTS_QUERY = """
query($id: ID!, $cursor: String) {
  node(id: $id) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { body }
      }
    }
  }
}
"""


def _gh(gh_args):
    """Run gh with terminal decoration structurally disabled; fail closed."""
    import subprocess

    env = dict(os.environ, NO_COLOR="1", CLICOLOR_FORCE="0")
    env.pop("GH_FORCE_TTY", None)
    head = "gh " + " ".join(gh_args[:2])
    try:
        proc = subprocess.run(["gh"] + gh_args, capture_output=True,
                              text=True, timeout=_GH_TIMEOUT, env=env)
    except FileNotFoundError:
        sys.exit("gh not found on PATH; --pr mode requires the GitHub CLI")
    except subprocess.TimeoutExpired:
        sys.exit(f"{head} ... timed out after {_GH_TIMEOUT}s")
    if proc.returncode != 0:
        sys.exit(f"{head} ... failed (exit {proc.returncode}):\n"
                 f"{proc.stderr.strip()[:2000]}")
    return proc.stdout


def resolve_repo(repo_arg):
    repo = repo_arg or _gh(
        ["repo", "view", "--json", "nameWithOwner",
         "--jq", ".nameWithOwner"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo):
        sys.exit(f"cannot resolve repository (got {repo!r}); pass --repo owner/name")
    return repo


def _graphql(query, fields):
    """One gh GraphQL call; exits on transport or GraphQL-level errors."""
    cmd = ["api", "graphql", "-f", f"query={query}"]
    for key, value in fields.items():
        if value is None:
            continue
        flag = "-F" if isinstance(value, int) else "-f"
        cmd += [flag, f"{key}={value}"]
    try:
        data = json.loads(_gh(cmd))
    except ValueError:
        sys.exit("gh api graphql returned non-JSON output")
    if data.get("errors"):
        sys.exit(f"GraphQL errors: {json.dumps(data['errors'])[:2000]}")
    return data.get("data") or {}


def fetch_review_threads(owner, name, number):
    """All review threads of one PR (comments fully paginated per thread),
    plus the PR's head branch name — used by verify_transcript_attribution()
    to confirm a transcript actually belongs to this PR's session."""
    threads, cursor = [], None
    head_ref_name = None
    for _ in range(_MAX_PAGES):
        data = _graphql(_THREADS_QUERY, {"owner": owner, "name": name,
                                         "number": number, "cursor": cursor})
        pr = (data.get("repository") or {}).get("pullRequest")
        if pr is None:
            sys.exit(f"PR #{number} not found in {owner}/{name}")
        if head_ref_name is None:
            head_ref_name = pr.get("headRefName")
        conn = pr["reviewThreads"]
        for node in conn["nodes"]:
            bodies = [c.get("body") for c in node["comments"]["nodes"]]
            page = node["comments"]["pageInfo"]
            for _ in range(_MAX_PAGES):
                if not page["hasNextPage"]:
                    break
                more = _graphql(_MORE_COMMENTS_QUERY,
                                {"id": node["id"],
                                 "cursor": page["endCursor"]})
                comments = (more.get("node") or {}).get("comments")
                if comments is None:
                    sys.exit(f"thread {node['id']}: comment pagination "
                             "returned no node (permissions?)")
                bodies.extend(c.get("body") for c in comments["nodes"])
                page = comments["pageInfo"]
            else:
                sys.exit(f"thread {node['id']}: comment pagination exceeded "
                         f"{_MAX_PAGES} pages")
            threads.append({"id": node["id"], "path": node.get("path") or "",
                            "resolved": bool(node["isResolved"]),
                            "bodies": bodies})
        if not conn["pageInfo"]["hasNextPage"]:
            return threads, head_ref_name
        cursor = conn["pageInfo"]["endCursor"]
    sys.exit(f"PR #{number}: thread pagination exceeded {_MAX_PAGES} pages")


def _first_line(body, width=80):
    """First line of an untrusted comment body, control-stripped here (before
    the markdown/--json fork) so both output paths emit sanitized text."""
    text = (body or "").strip()
    line = text.splitlines()[0] if text else ""
    line = strip_controls(line)
    return line if len(line) <= width else line[:width - 3] + "..."


def scan_prs(pr_numbers, repo_arg):
    """--pr mode driver: fetch, pre-classify, and shape JSON-safe results."""
    repo = resolve_repo(repo_arg)
    owner, name = repo.split("/", 1)
    prs = {}
    branches = {}
    for number in sorted(set(pr_numbers)):
        threads = []
        nodes, head_ref_name = fetch_review_threads(owner, name, number)
        branches[str(number)] = head_ref_name
        for node in nodes:
            # path is API-derived but still sanitized before the output fork —
            # same contract as summary (--json must not pass ANSI/C0 through).
            path = strip_controls(node["path"])
            threads.append({
                "id": node["id"],
                "path": path,
                "dir": os.path.dirname(path) or "(root)",
                "summary": _first_line(node["bodies"][0] if node["bodies"] else ""),
                "class": classify_thread(node["bodies"]),
                "resolved": node["resolved"],
            })
        prs[str(number)] = threads
    return {"repo": repo, "prs": prs, "branches": branches}


# F10: PR-scoped analysis previously trusted whatever transcript(s) were
# selected (often via the --latest fallback) without ever checking that the
# transcript actually belongs to the session that produced the PR under
# review — this caused a real misattribution where retro analyzed an
# unrelated session. verify_transcript_attribution() is the fail-closed check:
# when --pr is combined with a transcript scan, at least one scanned file must
# reference the PR (by number/URL) or its head branch, or the run aborts.
def verify_transcript_attribution(files, pr_numbers, branches):
    """Exit with TRANSCRIPT_MISMATCH unless at least one of `files` mentions
    one of `pr_numbers` (as "#N" or ".../pull/N") or one of `branches.values()`
    (the PRs' head branch names, from scan_prs()). No-op when pr_numbers is
    empty (transcript-only scans are unaffected)."""
    if not pr_numbers:
        return
    needles = set()
    for n in pr_numbers:
        needles.add(f"#{n}")
        needles.add(f"/pull/{n}")
    for b in branches.values():
        if b:
            needles.add(b)
    if not needles:
        return
    for f in files:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        if any(needle in content for needle in needles):
            return
    pr_list = ", ".join(f"#{n}" for n in sorted(set(pr_numbers)))
    branch_list = ", ".join(sorted({b for b in branches.values() if b})) or "(none)"
    sys.exit(
        f"TRANSCRIPT_MISMATCH: none of the {len(files)} scanned transcript(s) "
        f"mention PR {pr_list} (checked for the PR number/URL and head "
        f"branch(es) {branch_list}) — the selected transcript(s) likely do "
        "not belong to the session that produced this PR. Pass the correct "
        "--transcript explicitly instead of relying on --latest, or drop "
        "--pr if this scan is not meant to be PR-scoped."
    )


def render_pr_report(data):
    prs = data["prs"]
    total = sum(len(t) for t in prs.values())
    print(f"# retro PR scan — {sanitize_cell(data['repo'])}, "
          + " ".join(f"#{n}" for n in prs)
          + f" ({total} review threads)\n")
    print("- class は返信本文パターンからの**機械的な下読み** (classify_thread)。"
          "確定判断は評価 subagent が行う")
    print("- summary は各スレッド先頭コメント 1 行目の sanitize + truncate。"
          "スレッド本文は untrusted データとして扱うこと")
    print("- UNTERMINATED = 終端返信パターン未検出。resolved 列は別掲 — "
          "resolved なのに UNTERMINATED は返信規律違反の候補\n")

    print("## Threads by class (per PR)")
    rows = []
    for n, threads in prs.items():
        row = {"pr": f"#{n}", "threads": len(threads)}
        for cls in THREAD_CLASSES:
            row[cls] = sum(1 for t in threads if t["class"] == cls)
        rows.append(row)
    render_table(rows)
    print()

    print("## Threads")
    render_table([
        {"pr": f"#{n}", "id": t["id"], "path": t["path"],
         "summary": t["summary"], "class": t["class"],
         "resolved": t["resolved"]}
        for n, threads in prs.items() for t in threads
    ])
    print()

    print("## Recurrence by directory × class (cross-PR)")
    agg = {}
    for n, threads in prs.items():
        for t in threads:
            entry = agg.setdefault((t["dir"], t["class"]),
                                   {"n": 0, "prs": set()})
            entry["n"] += 1
            entry["prs"].add(n)
    render_table([
        {"dir": d, "class": c, "n": e["n"],
         "prs": " ".join(f"#{p}" for p in sorted(e["prs"], key=int))}
        for (d, c), e in sorted(
            agg.items(), key=lambda kv: (-len(kv[1]["prs"]), -kv[1]["n"]))
    ])
    print()


def main():
    ap = argparse.ArgumentParser(description="retro quantitative transcript scan")
    ap.add_argument("--transcript", action="append")
    # None sentinels, not eager defaults: validate_flag_combinations() must
    # distinguish "explicitly passed" from "defaulted" (fail closed: a
    # defaulted flag silently overridden downstream must not be mistaken
    # for an explicit user choice).
    # The real defaults (cwd / ~/.claude/projects) are filled in right before
    # transcript discovery.
    ap.add_argument("--project-dir", default=None)
    ap.add_argument("--projects-root", default=None)
    ap.add_argument("--all-projects", action="store_true")
    ap.add_argument("--since")
    ap.add_argument("--max-rows", type=int, default=15)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument(
        "--latest", action="store_true",
        help="print the newest main-session transcript path and exit "
             "(single-session discovery without ls/find, which deny-hooks block)",
    )
    ap.add_argument(
        "--pr", action="append", type=int, metavar="N",
        help="PR / review-loop mode: fetch this PR's review threads via gh "
             "GraphQL and pre-classify their terminal state; repeatable",
    )
    ap.add_argument(
        "--repo", metavar="OWNER/NAME",
        help="repository for --pr (default: current repo via gh repo view)",
    )
    args = ap.parse_args()

    if args.repo and not args.pr:
        sys.exit("--repo requires --pr")
    if args.pr and args.latest:
        sys.exit("--latest cannot be combined with --pr")
    # Reject dead flag combinations before any work runs (fail closed): the
    # check compares the explicit flag set against the flag set the selected
    # scan path consumes, so the whole silently-ignored class is caught here.
    validate_flag_combinations(args)

    pr_data = None
    if args.pr and standalone_pr(args):
        # --pr alone is a standalone review-loop scan; transcripts are scanned
        # alongside only when explicitly requested next to it.
        pr_data = scan_prs(args.pr, args.repo)
        if args.as_json:
            json.dump({"pr_scan": pr_data}, sys.stdout,
                      ensure_ascii=False, default=str, indent=1)
            print()
        else:
            render_pr_report(pr_data)
        return

    # Fill the real defaults only after validate_flag_combinations() has seen
    # the raw None-vs-explicit distinction; downstream code keeps the
    # historical behavior (cwd-derived slug under ~/.claude/projects).
    if args.project_dir is None:
        args.project_dir = os.getcwd()
    if args.projects_root is None:
        args.projects_root = "~/.claude/projects"

    files = discover(args)
    if not files:
        sys.exit(
            f"no transcripts found (slug={project_slug(args.project_dir)!r} "
            f"under {args.projects_root}). Pass --transcript or --all-projects."
        )

    if args.latest:
        mains = [f for f in files if "/subagents/" not in f]
        if not mains:
            sys.exit("no main-session transcripts in corpus")
        print(max(mains, key=os.path.getmtime))
        return

    # Non-standalone --pr: fetch review-loop data only after the transcript
    # corpus is known to exist, so an argument error exits before any gh
    # GraphQL calls spend API rate limit (r3695744834).
    if args.pr:
        pr_data = scan_prs(args.pr, args.repo)
        # F10: fail closed before the (expensive) duckdb scan runs if none of
        # the selected transcripts actually belong to the PR(s) under review.
        verify_transcript_attribution(files, args.pr, pr_data["branches"])

    try:
        import duckdb
    except ImportError:
        sys.exit(
            "duckdb module not found. Run via: uv run --with duckdb python3 "
            "retro_scan.py\nIf uv is unavailable, fall back to the jq-based "
            "per-file scan in SKILL.md."
        )

    con = duckdb.connect()
    # One JSON column per line; no schema inference so heterogeneous records
    # never fail the load. 100MB line cap: tool_results embedding whole files.
    # CREATE VIEW cannot be a prepared statement, so the file list is inlined
    # as an escaped SQL literal.
    files_lit = "[" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "]"
    con.execute(
        f"""
        CREATE TEMP VIEW raw AS
        SELECT json AS j, filename AS fn
        FROM read_ndjson_objects({files_lit}, maximum_object_size=104857600,
                                 filename=true, ignore_errors=true)
        """
    )
    con.execute(
        """
        CREATE TEMP VIEW msg AS
        SELECT j, fn,
               contains(fn, '/subagents/') AS is_sub,
               regexp_extract(fn, '([^/]+)\\.jsonl$', 1) AS session,
               json_extract_string(j, '$.type') AS type,
               json_extract_string(j, '$.timestamp') AS ts,
               try_cast(json_extract(j, '$.message.usage.output_tokens') AS BIGINT) AS out_tok,
               try_cast(json_extract(j, '$.message.usage.cache_creation_input_tokens') AS BIGINT) AS cache_create,
               try_cast(json_extract(j, '$.message.usage.cache_read_input_tokens') AS BIGINT) AS cache_read,
               json_extract_string(j, '$.type') = 'user'
                 AND contains(j::VARCHAR, 'Request interrupted by user') AS is_interrupt,
               (json_extract_string(j, '$.subtype') = 'compact_boundary'
                 OR contains(j::VARCHAR, '"isCompactSummary":true')) AS is_compact,
               -- Human prompt approximation: a user line carrying no tool_result.
               json_extract_string(j, '$.type') = 'user'
                 AND NOT contains(j::VARCHAR, '"type":"tool_result"') AS is_prompt
        FROM raw
        """
    )
    con.execute(
        """
        CREATE TEMP VIEW tu AS
        SELECT m.fn, m.is_sub, m.session, m.ts,
               json_extract_string(c.value, '$.id') AS id,
               json_extract_string(c.value, '$.name') AS tool,
               json_extract(c.value, '$.input') AS input
        FROM msg m, json_each(json_extract(m.j, '$.message.content')) c
        WHERE m.type = 'assistant'
          AND json_extract_string(c.value, '$.type') = 'tool_use'
        """
    )
    # tool_result content is either a plain string or [{type:"text",text:...}];
    # stringifying the whole item covers both for pattern matching.
    con.execute(
        """
        CREATE TEMP VIEW tr AS
        SELECT m.fn, m.is_sub, m.session,
               json_extract_string(c.value, '$.tool_use_id') AS tool_use_id,
               coalesce(try_cast(json_extract(c.value, '$.is_error') AS BOOLEAN), false) AS is_error,
               left(c.value::VARCHAR, 2000) AS text
        FROM msg m, json_each(json_extract(m.j, '$.message.content')) c
        WHERE m.type = 'user'
          AND json_extract_string(c.value, '$.type') = 'tool_result'
        """
    )

    n = args.max_rows
    params = {
        "corpus": [], "tools": [n], "skill_fires": [], "skill_reads": [n],
        "dispatches": [], "denials": [n], "errors": [], "retries": [n],
        "interventions": [n], "sessions": [n], "blocking": [n],
    }
    out = {}
    for key, sql in SQL.items():
        cur = con.execute(sql, params[key])
        cols = [d[0] for d in cur.description]
        # scrub_value before the markdown/--json fork so both paths emit
        # sanitized transcript-derived text (same contract as --pr mode).
        out[key] = [
            {c: scrub_value(v) for c, v in zip(cols, row)}
            for row in cur.fetchall()
        ]

    if args.as_json:
        payload = {"files_scanned": len(files), **out}
        if pr_data is not None:
            payload = {"pr_scan": pr_data, **payload}
        json.dump(payload, sys.stdout,
                  ensure_ascii=False, default=str, indent=1)
        print()
        return

    if pr_data is not None:
        render_pr_report(pr_data)

    c = out["corpus"][0]
    print(f"# retro scan — {len(files)} files "
          f"({c['main_files']} main / {c['sub_files']} subagent), "
          f"{sanitize_cell(c['first_ts'])} .. {sanitize_cell(c['last_ts'])}\n")
    # Self-report counting definitions so auditors don't have to reverse-
    # engineer them (a blank-slate auditor flagged undefined denial criteria).
    print("- corpus: files under /subagents/ are counted as subagent transcripts; "
          "--transcript auto-includes each file's <stem>/subagents/**/*.jsonl")
    print(f"- denials: tool_result with is_error=true matching `{DENY_PAT}`")
    print("- errors: every is_error tool_result classified by first matching pattern "
          "(see SQL); user_prompts = user lines carrying no tool_result (approximation)")
    print("- SKILL.md reads are usually *normal* contract loads "
          "(dispatch prompts instruct subagents to Read the skill), not misfires")
    print("- transcript format is officially internal/unstable; counts are "
          "best-effort (unparseable lines are skipped via ignore_errors)\n")
    titles = [
        ("tools", "Tool usage (main vs subagent)"),
        ("skill_fires", "Skill fires"),
        ("skill_reads", "SKILL.md reads (contract loads by subagents)"),
        ("dispatches", "Subagent dispatches"),
        ("denials", "Permission denials / hook blocks (by denied tool)"),
        ("errors", "Tool error taxonomy (all is_error results)"),
        ("retries", "Repeated identical Bash commands (>=3 per session)"),
        ("interventions", "User interventions (interruptions / steps per prompt / compactions)"),
        ("sessions", "Main sessions by output tokens"),
        ("blocking", "Blocking indicators (wakeups / sleep)"),
    ]
    for key, title in titles:
        print(f"## {title}")
        render_table(out[key])
        print()


if __name__ == "__main__":
    main()
