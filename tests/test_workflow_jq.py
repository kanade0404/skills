"""Behaviour tests for the jq programs embedded in the gate workflows.

`review-response-gate` and `gate-heartbeat` carry their whole decision in a
single-quoted jq program inside `run:`. actionlint only checks the YAML/shell
shape, so the classification itself was unpinned — the two findings fixed in
PR #115 (a bot's own follow-up counting as a response; `pr_closed` records
counted instead of distinct PRs) were both invisible to CI.

These tests lift each jq program straight out of the workflow file (so the
workflow stays the single source of truth) and run it against fixtures.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / ".github" / "workflows" / "review-response-gate.yml"
HEARTBEAT = REPO_ROOT / ".github" / "workflows" / "gate-heartbeat.yml"
SKILL_IMPROVER = REPO_ROOT / ".github" / "workflows" / "skill-improver.yml"


def extract_jq(path: pathlib.Path, start_marker: str) -> str:
    """Return the single-quoted jq program that starts on the line containing
    `start_marker`. The program ends at the first following line whose first
    non-blank character is the closing quote (a single quote cannot occur
    inside a shell single-quoted string, so this is unambiguous)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if start_marker in line]
    if len(starts) != 1:
        raise AssertionError(
            f"{path.name}: expected exactly one line containing "
            f"{start_marker!r}, found {len(starts)}")
    head = lines[starts[0]].split("'", 1)
    if len(head) != 2:
        raise AssertionError(f"{path.name}: no opening quote on start line")
    body = [head[1]] if head[1].strip() else []
    for line in lines[starts[0] + 1:]:
        if line.strip().startswith("'"):
            return "\n".join(body)
        body.append(line)
    raise AssertionError(f"{path.name}: unterminated jq program")


def run_jq(program: str, *args: str, stdin: str = "") -> str:
    proc = subprocess.run(["jq", *args, program], input=stdin,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"jq failed: {proc.stderr}")
    return proc.stdout


def jq_accepts(program: str, *args: str, stdin: str = "") -> bool:
    """Run a `jq -e` predicate the way the workflow does: the exit status is
    the whole answer, so a false result is not an error."""
    proc = subprocess.run(["jq", "-e", *args, program], input=stdin,
                          capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise AssertionError(f"jq failed: {proc.stderr}")
    return proc.returncode == 0


def bot(login: str) -> dict:
    return {"login": login, "__typename": "Bot"}


def user(login: str) -> dict:
    return {"login": login, "__typename": "User"}


def thread(url: str, resolved: bool, authors: list[dict]) -> dict:
    return {
        "id": url,
        "isResolved": resolved,
        "path": "some/file.py",
        "comments": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"databaseId": i, "url": url, "author": a}
                      for i, a in enumerate(authors)],
        },
    }


class ReviewResponseGateFilterTest(unittest.TestCase):
    """Which threads the gate reports as "unanswered bot finding"."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.program = extract_jq(GATE, "failing=$(jq -c")

    def failing_urls(self, threads: list[dict]) -> set[str]:
        out = run_jq(self.program, "-c", stdin=json.dumps(threads))
        return {row["url"] for row in json.loads(out)}

    def test_unanswered_bot_thread_fails(self) -> None:
        threads = [thread("u1", False, [bot("coderabbitai")])]
        self.assertEqual({"u1"}, self.failing_urls(threads))

    def test_resolved_thread_is_ignored(self) -> None:
        threads = [thread("u1", True, [bot("coderabbitai")])]
        self.assertEqual(set(), self.failing_urls(threads))

    def test_human_originated_thread_is_ignored(self) -> None:
        threads = [thread("u1", False, [user("kanade0404")])]
        self.assertEqual(set(), self.failing_urls(threads))

    def test_human_reply_answers_the_finding(self) -> None:
        threads = [thread("u1", False, [bot("devin-ai-integration"),
                                        user("kanade0404")])]
        self.assertEqual(set(), self.failing_urls(threads))

    def test_author_agent_reply_answers_the_finding(self) -> None:
        # Replies posted by the PR author's own automation (the repo's
        # workflows run as github-actions[bot]; the Claude app as claude[bot])
        # are PR-side responses and must clear the gate — otherwise a headless
        # pr-review-respond run could never turn this check green.
        for login in ("github-actions[bot]", "claude[bot]"):
            with self.subTest(login=login):
                threads = [thread("u1", False, [bot("coderabbitai"),
                                                bot(login)])]
                self.assertEqual(set(), self.failing_urls(threads))

    def test_reviewer_own_follow_up_does_not_answer_the_finding(self) -> None:
        # PR #115 review (Devin): counting *any* second comment let a reviewer
        # bot answer its own finding, so the gate went green with the finding
        # still unanswered by the PR side.
        threads = [thread("u1", False, [bot("devin-ai-integration"),
                                        bot("devin-ai-integration")])]
        self.assertEqual({"u1"}, self.failing_urls(threads))

    def test_other_review_bot_reply_does_not_answer_the_finding(self) -> None:
        threads = [thread("u1", False, [bot("devin-ai-integration"),
                                        bot("coderabbitai")])]
        self.assertEqual({"u1"}, self.failing_urls(threads))

    def test_reports_every_unanswered_thread(self) -> None:
        threads = [
            thread("u1", False, [bot("coderabbitai")]),
            thread("u2", False, [bot("copilot-pull-request-reviewer[bot]")]),
            thread("u3", False, [bot("coderabbitai"), user("kanade0404")]),
            thread("u4", True, [bot("coderabbitai")]),
        ]
        self.assertEqual({"u1", "u2"}, self.failing_urls(threads))


class GateHeartbeatMetricsTest(unittest.TestCase):
    """How the heartbeat counts its alert denominator."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.program = extract_jq(HEARTBEAT, "RESULT=$(jq -n")

    def metrics(self, events: list[dict], repo: str = "o/r") -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "events.json"
            path.write_text("\n".join(json.dumps(e) for e in events),
                            encoding="utf-8")
            out = run_jq(self.program, "-n",
                         "--argjson", "window_days", "30",
                         "--arg", "repo", repo,
                         "--slurpfile", "events", str(path))
        return json.loads(out)

    @staticmethod
    def _ts(days_ago: float) -> str:
        moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    def closed(self, pr: int, outcome: str, days_ago: float = 1,
               repo: str = "o/r") -> dict:
        return {"v": 1, "ts": self._ts(days_ago), "event": "pr_closed",
                "repo": repo, "pr": pr, "outcome": outcome}

    def test_counts_distinct_prs_not_records(self) -> None:
        # PR #115 review (CodeRabbit): loop-metrics emits one pr_closed record
        # per close event, so a reopened-then-reclosed PR (or a re-run of the
        # workflow) inflates the denominator that gates the alert.
        events = [
            self.closed(42, "merged", days_ago=3),
            self.closed(42, "closed", days_ago=2),
            self.closed(42, "merged", days_ago=1),
            self.closed(43, "closed", days_ago=1),
        ]
        result = self.metrics(events)
        self.assertEqual(2, result["attempted_prs"])
        self.assertEqual(1, result["merged_prs"])

    def test_counts_each_pr_once_per_window(self) -> None:
        events = [self.closed(1, "merged"), self.closed(2, "merged"),
                  self.closed(3, "closed")]
        result = self.metrics(events)
        self.assertEqual(3, result["attempted_prs"])
        self.assertEqual(2, result["merged_prs"])

    def test_other_repositories_are_excluded(self) -> None:
        events = [self.closed(1, "merged"), self.closed(2, "merged",
                                                        repo="other/repo")]
        result = self.metrics(events)
        self.assertEqual(1, result["attempted_prs"])
        self.assertEqual(1, result["merged_prs"])

    def test_events_outside_the_window_are_excluded(self) -> None:
        events = [self.closed(1, "merged", days_ago=45),
                  self.closed(2, "merged", days_ago=2)]
        result = self.metrics(events)
        self.assertEqual(1, result["attempted_prs"])

    def test_gate_events_still_counted_per_record(self) -> None:
        # gate runs are per-invocation signals, not per-PR: they stay a raw
        # record count.
        events = [
            {"v": 1, "ts": self._ts(1), "event": "agent_run", "repo": "o/r",
             "phase": "test-mutation-gate", "result_subtype": "pass"},
            {"v": 1, "ts": self._ts(1), "event": "agent_run", "repo": "o/r",
             "phase": "test-mutation-gate", "result_subtype": "block"},
        ]
        result = self.metrics(events)
        self.assertEqual(2, result["gate_events"])
        self.assertEqual(1, result["gate_blocks"])


class SkillImproverRulesetPreflightTest(unittest.TestCase):
    """What the `skill-improver` preflight accepts as a protecting ruleset.

    `improve/**` is held immutable by *two* rulesets, and the preflight must
    require both (CodeRabbit finding on PR #138): A lets only the workflow App
    create such a branch, B forbids `update` and `deletion` for everyone
    (`bypass_actors: []`), so the verified `head_sha` cannot change between
    `git ls-remote` and `gh pr create`. A ruleset does not apply to a ref that
    `conditions.ref_name.exclude` matches, so checking `include` alone lets a
    ruleset that enforces nothing on the target ref pass the gate.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.default_program = extract_jq(
            SKILL_IMPROVER, '--arg ref "refs/heads/$DEFAULT_BRANCH"')
        cls.create_program = extract_jq(
            SKILL_IMPROVER, '--argjson app_id "${APP_ID:-0}"')
        cls.freeze_program = extract_jq(
            SKILL_IMPROVER, '--arg improve_ref')

    def accepts_default(self, ruleset: dict) -> bool:
        return jq_accepts(self.default_program, "--arg", "ref",
                          "refs/heads/main", stdin=json.dumps(ruleset))

    def accepts_create(self, ruleset: dict) -> bool:
        return jq_accepts(self.create_program, "--argjson", "app_id", "42",
                          stdin=json.dumps(ruleset))

    def accepts_freeze(self, ruleset: dict) -> bool:
        return jq_accepts(self.freeze_program, "--arg", "improve_ref",
                          "refs/heads/improve/**", stdin=json.dumps(ruleset))

    @staticmethod
    def default_ruleset(**overrides) -> dict:
        ruleset = {
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"],
                                        "exclude": []}},
            "rules": [{"type": "pull_request"}],
            "bypass_actors": [],
        }
        ruleset.update(overrides)
        return ruleset

    @staticmethod
    def create_ruleset(**overrides) -> dict:
        """Ruleset A: only the App may create `improve/**`."""
        ruleset = {
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/improve/**"],
                                        "exclude": []}},
            "rules": [{"type": "creation"}],
            "bypass_actors": [{"actor_type": "Integration", "actor_id": 42,
                               "bypass_mode": "always"}],
        }
        ruleset.update(overrides)
        return ruleset

    @staticmethod
    def freeze_ruleset(**overrides) -> dict:
        """Ruleset B: nobody may update or delete `improve/**`."""
        ruleset = {
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/improve/**"],
                                        "exclude": []}},
            "rules": [{"type": "update"}, {"type": "deletion"}],
            "bypass_actors": [],
        }
        ruleset.update(overrides)
        return ruleset

    def test_default_ruleset_without_exclude_is_accepted(self) -> None:
        self.assertTrue(self.accepts_default(self.default_ruleset()))

    def test_default_ruleset_with_any_exclude_is_rejected(self) -> None:
        # The exclude need not name the default branch: an entry at all means
        # the ruleset may not apply, and the preflight cannot tell which refs
        # a fnmatch pattern covers.
        for exclude in (["refs/heads/main"], ["refs/heads/unrelated/**"]):
            with self.subTest(exclude=exclude):
                conditions = {"ref_name": {"include": ["~DEFAULT_BRANCH"],
                                           "exclude": exclude}}
                self.assertFalse(self.accepts_default(
                    self.default_ruleset(conditions=conditions)))

    def test_default_ruleset_with_missing_exclude_key_is_accepted(self) -> None:
        conditions = {"ref_name": {"include": ["~DEFAULT_BRANCH"]}}
        self.assertTrue(self.accepts_default(
            self.default_ruleset(conditions=conditions)))

    def test_default_ruleset_still_needs_the_other_conditions(self) -> None:
        self.assertFalse(self.accepts_default(
            self.default_ruleset(enforcement="evaluate")))
        self.assertFalse(self.accepts_default(self.default_ruleset(rules=[])))
        self.assertFalse(self.accepts_default(self.default_ruleset(
            bypass_actors=[{"actor_type": "Integration", "actor_id": 42,
                            "bypass_mode": "always"}])))

    def test_create_ruleset_is_accepted(self) -> None:
        self.assertTrue(self.accepts_create(self.create_ruleset()))

    def test_create_ruleset_needs_the_creation_rule(self) -> None:
        # Ruleset A without `creation` restricts nothing about who may create
        # `improve/**`, so anyone with push rights could plant a branch the
        # publish job would then treat as workflow-made.
        self.assertFalse(self.accepts_create(
            self.create_ruleset(rules=[{"type": "update"}])))
        self.assertFalse(self.accepts_create(self.create_ruleset(rules=[])))

    def test_create_ruleset_still_needs_the_single_app_bypass(self) -> None:
        self.assertFalse(self.accepts_create(self.create_ruleset(
            bypass_actors=[{"actor_type": "Integration", "actor_id": 43,
                            "bypass_mode": "always"}])))
        self.assertFalse(self.accepts_create(self.create_ruleset(
            bypass_actors=[{"actor_type": "Integration", "actor_id": 42,
                            "bypass_mode": "pull_request"}])))
        self.assertFalse(self.accepts_create(self.create_ruleset(
            bypass_actors=[{"actor_type": "Integration", "actor_id": 42,
                            "bypass_mode": "always"},
                           {"actor_type": "RepositoryRole", "actor_id": 5,
                            "bypass_mode": "always"}])))
        self.assertFalse(self.accepts_create(
            self.create_ruleset(bypass_actors=[])))

    def test_create_ruleset_with_any_exclude_is_rejected(self) -> None:
        conditions = {"ref_name": {"include": ["refs/heads/improve/**"],
                                   "exclude": ["refs/heads/improve/**"]}}
        self.assertFalse(self.accepts_create(
            self.create_ruleset(conditions=conditions)))

    def test_freeze_ruleset_is_accepted(self) -> None:
        self.assertTrue(self.accepts_freeze(self.freeze_ruleset()))

    def test_freeze_ruleset_with_any_bypass_actor_is_rejected(self) -> None:
        # The whole point of B is that *nobody* — the App included, and the
        # operator holding its key with it — can move a branch after it is
        # created. One bypass actor reopens the window between `ls-remote`
        # and `gh pr create`.
        for actor in ({"actor_type": "Integration", "actor_id": 42,
                       "bypass_mode": "always"},
                      {"actor_type": "OrganizationAdmin", "actor_id": 1,
                       "bypass_mode": "always"}):
            with self.subTest(actor=actor):
                self.assertFalse(self.accepts_freeze(
                    self.freeze_ruleset(bypass_actors=[actor])))

    def test_freeze_ruleset_needs_both_update_and_deletion(self) -> None:
        self.assertFalse(self.accepts_freeze(
            self.freeze_ruleset(rules=[{"type": "update"}])))
        self.assertFalse(self.accepts_freeze(
            self.freeze_ruleset(rules=[{"type": "deletion"}])))

    def test_freeze_ruleset_with_any_exclude_is_rejected(self) -> None:
        conditions = {"ref_name": {"include": ["refs/heads/improve/**"],
                                   "exclude": ["refs/heads/improve/**"]}}
        self.assertFalse(self.accepts_freeze(
            self.freeze_ruleset(conditions=conditions)))

    def test_create_ruleset_does_not_satisfy_the_freeze_check(self) -> None:
        # A repo that only has ruleset A must fail the preflight: A carries the
        # App as a bypass actor and no update/deletion rule, so it can never be
        # mistaken for B (and vice versa).
        self.assertFalse(self.accepts_freeze(self.create_ruleset()))
        self.assertFalse(self.accepts_create(self.freeze_ruleset()))


_JOB_LINE_RE = re.compile(r"^  ([A-Za-z][A-Za-z0-9_-]*):$")
_ENV_START_RE = re.compile(r"^    env:$")
_ENV_ENTRY_RE = re.compile(r"^      ([A-Za-z_][A-Za-z0-9_]*):\s?(.*)$")
_FORBIDDEN_CONTEXT_RE = re.compile(r"\$\{\{\s*(runner|steps|env|job)\.")


def scan_job_level_env(text: str) -> dict[str, dict[str, tuple[int, str]]]:
    """Indentation-based scan of `jobs.<id>.env` (no YAML parser — see the
    class docstring below for why). Returns `{job_id: {key: (line_no,
    value)}}`; a job with no `env:` block still gets an (empty) entry.

    Only lines under a top-level `jobs:` key are considered. A job starts
    at 2-space `name:`, its env block at 4-space `env:`, and the block ends
    at the next line indented 4 spaces or less that isn't blank/comment.
    Comments are only recognised as whole lines (`#` at the start after
    stripping) — an inline `#` inside a value is treated as part of the
    value, since no job-level env value in this workflow carries one."""
    lines = text.splitlines()
    result: dict[str, dict[str, tuple[int, str]]] = {}
    in_jobs = False
    current_job: str | None = None
    in_env = False
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if line == "jobs:":
            in_jobs, current_job, in_env = True, None, False
            continue
        if not in_jobs:
            continue
        if stripped and not stripped.startswith("#") and not line[:1].isspace():
            # Back to top-level indentation on a real line: jobs: is over.
            in_jobs, current_job, in_env = False, None, False
            continue
        if in_env:
            indent = len(line) - len(line.lstrip(" "))
            if stripped and not stripped.startswith("#") and indent <= 4:
                in_env = False  # block ended; fall through, re-check this line
            else:
                entry = _ENV_ENTRY_RE.match(line)
                if entry:
                    key, value = entry.groups()
                    result[current_job][key] = (i, value)
                continue
        job = _JOB_LINE_RE.match(line)
        if job:
            current_job = job.group(1)
            result.setdefault(current_job, {})
            in_env = False
            continue
        if current_job is not None and _ENV_START_RE.match(line):
            in_env = True
    return result


class SkillImproverJobEnvContextTest(unittest.TestCase):
    """`jobs.<job_id>.env` only has github/inputs/matrix/needs/secrets/
    strategy/vars available — runner, steps, env and job are not, and
    GitHub rejects the whole workflow at load time if any job-level env
    value references one of them (as `runner.temp` did for 24 keys across
    all five jobs, before they moved into a first `Resolve scratch paths`
    step).

    PyYAML is not a declared dependency (CI runs plain `python3 -m unittest
    discover`, no pip install — see trigger-evals.yml and the verify job
    here), so this reads the workflow as text with `scan_job_level_env`
    rather than parsing it as YAML.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.jobs = scan_job_level_env(
            SKILL_IMPROVER.read_text(encoding="utf-8"))

    def test_job_level_env_never_references_unavailable_contexts(self) -> None:
        # Self-check first: a scanner that matched nothing would make the
        # loop below pass vacuously, so prove it actually saw job-level env.
        for job_id in ("improve", "stage", "verify", "collect", "publish"):
            self.assertIn(job_id, self.jobs)
        all_keys = {key for env in self.jobs.values() for key in env}
        self.assertTrue(
            {"DEFAULT_BRANCH", "GUARD", "IDX"} & all_keys,
            f"scanner found no job-level env keys at all: {self.jobs!r}")

        for job_id, env in self.jobs.items():
            for key, (line_no, value) in env.items():
                match = _FORBIDDEN_CONTEXT_RE.search(value)
                self.assertIsNone(
                    match,
                    f"jobs.{job_id}.env.{key} at line {line_no} references "
                    f"a context unavailable at job-level env: {value!r}")

    def test_scanner_detects_an_injected_violation(self) -> None:
        """Negative self-test: prove the scanner actually flags a
        `runner.` reference, so the assertion above is not vacuous."""
        snippet = ("jobs:\n"
                   "  faketask:\n"
                   "    env:\n"
                   "      X: ${{ runner.temp }}/x\n")
        jobs = scan_job_level_env(snippet)
        self.assertIn("X", jobs.get("faketask", {}))
        _, value = jobs["faketask"]["X"]
        self.assertRegex(value, _FORBIDDEN_CONTEXT_RE)


if __name__ == "__main__":
    unittest.main()
