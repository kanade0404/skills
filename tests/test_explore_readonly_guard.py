"""Tests for scripts/explore-readonly-guard.sh, the PreToolUse hook that
enforces the Explore subagent's (.claude/agents/Explore.md) read-only
contract at the tool layer instead of relying on the prompt alone
(CodeRabbit review, PR #104).

Pins the two-stage fail-closed contract: any command with a chaining /
redirection / substitution metacharacter is denied outright regardless of
its visible prefix, and only a fixed allowlist of read-only prefixes is
permitted otherwise.
"""

import json
import pathlib
import subprocess
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
GUARD = REPO_ROOT / "scripts" / "explore-readonly-guard.sh"


def run_guard(command: str | None) -> subprocess.CompletedProcess:
    payload = {"tool_input": ({"command": command} if command is not None else {})}
    return subprocess.run(
        ["bash", str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


class ExploreReadonlyGuardTest(unittest.TestCase):
    def test_allowlisted_read_only_commands_pass(self) -> None:
        for cmd in [
            "git log -5",
            "git show HEAD",
            "git blame foo.py",
            "git diff --stat",
            "git status --short",
            "git branch -a",
            "git ls-files",
            "git rev-parse --show-toplevel",
            "rg -n foo .",
            "grep -rn foo .",
            "find . -name '*.md'",
            "ls -la",
            "cat foo.txt",
            "wc -l foo.txt",
            "head -20 foo.txt",
            "tail -20 foo.txt",
            "pwd",
        ]:
            with self.subTest(cmd=cmd):
                result = run_guard(cmd)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_non_allowlisted_command_is_denied(self) -> None:
        result = run_guard("rm -rf /")
        self.assertEqual(result.returncode, 2)
        self.assertIn("read-only allowlist", result.stderr)

    def test_write_via_redirect_is_denied(self) -> None:
        result = run_guard("cat foo.txt > /etc/passwd")
        self.assertEqual(result.returncode, 2)
        self.assertIn("metacharacter", result.stderr)

    def test_chained_command_bypass_is_denied(self) -> None:
        # A read-only-looking prefix followed by a destructive command via
        # `;` must not slip through on the strength of its prefix alone.
        result = run_guard("git log; rm -rf /")
        self.assertEqual(result.returncode, 2)
        self.assertIn("metacharacter", result.stderr)

    def test_command_substitution_bypass_is_denied(self) -> None:
        result = run_guard("git log $(rm -rf /)")
        self.assertEqual(result.returncode, 2)
        self.assertIn("metacharacter", result.stderr)

    def test_pipe_bypass_is_denied(self) -> None:
        result = run_guard("rg -n foo . | xargs rm")
        self.assertEqual(result.returncode, 2)
        self.assertIn("metacharacter", result.stderr)

    def test_empty_command_is_allowed_through(self) -> None:
        result = run_guard(None)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
