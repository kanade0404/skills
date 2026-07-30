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
#
# Corpus discovery globs <root>/<slug>*/**/*.jsonl so worktree-session dirs
# (slug prefix + suffix) and subagent transcripts (<session>/subagents/**)
# are included; files under a /subagents/ path are tallied separately.
import argparse
import glob
import json
import os
import re
import sys

try:
    import duckdb
except ImportError:
    sys.exit(
        "duckdb module not found. Run via: uv run --with duckdb python3 retro_scan.py\n"
        "If uv is unavailable, fall back to the jq-based per-file scan in SKILL.md."
    )


def project_slug(path):
    path = os.path.abspath(path)
    # Worktree cwds live under <project>/.claude/worktrees/<name>; sessions for
    # them are still "this project's" history, so scan from the parent project.
    m = re.match(r"(.*?)/\.claude/worktrees/[^/]+$", path)
    if m:
        path = m.group(1)
    return path.replace("/", "-").replace(".", "-")


def discover(args):
    if args.transcript:
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
    if args.since:
        import datetime

        try:
            cutoff = datetime.datetime.strptime(args.since, "%Y-%m-%d").timestamp()
        except ValueError:
            sys.exit(f"--since must be YYYY-MM-DD, got: {args.since!r}")
        files = [f for f in files if os.path.getmtime(f) >= cutoff]
    return sorted(set(files))


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


def render_table(rows):
    if not rows:
        print("(none)")
        return
    cols = list(rows[0])
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(str(r[c]) if r[c] is not None else "" for c in cols) + " |")


def main():
    ap = argparse.ArgumentParser(description="retro quantitative transcript scan")
    ap.add_argument("--transcript", action="append")
    ap.add_argument("--project-dir", default=os.getcwd())
    ap.add_argument("--projects-root", default="~/.claude/projects")
    ap.add_argument("--all-projects", action="store_true")
    ap.add_argument("--since")
    ap.add_argument("--max-rows", type=int, default=15)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument(
        "--latest", action="store_true",
        help="print the newest main-session transcript path and exit "
             "(single-session discovery without ls/find, which deny-hooks block)",
    )
    args = ap.parse_args()

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
        out[key] = [dict(zip(cols, row)) for row in cur.fetchall()]

    if args.as_json:
        json.dump({"files_scanned": len(files), **out}, sys.stdout,
                  ensure_ascii=False, default=str, indent=1)
        print()
        return

    c = out["corpus"][0]
    print(f"# retro scan — {len(files)} files "
          f"({c['main_files']} main / {c['sub_files']} subagent), "
          f"{c['first_ts']} .. {c['last_ts']}\n")
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
