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
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / ".github" / "workflows" / "review-response-gate.yml"
HEARTBEAT = REPO_ROOT / ".github" / "workflows" / "gate-heartbeat.yml"


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


if __name__ == "__main__":
    unittest.main()
