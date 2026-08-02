"""state-merge guard tests for skills/pr-monitor/scripts/prm.

`state-merge` is documented (SKILL.md, prm usage comment) as forbidden for
CAS-managed scalars (`known_conflict_head` / `last_head_sha` / `state`):
an unconditional shallow merge bypasses the Step 4 CAS filter preconditions
and can clobber a concurrent invocation's claim / settling transition.
These tests pin that contract into the script itself: a patch touching any
CAS-managed field must be rejected with a non-zero exit and no state change
(PR #99 review).
"""

import json
import pathlib
import subprocess
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PRM = REPO_ROOT / "skills" / "pr-monitor" / "scripts" / "prm"

INITIAL_STATE = {
    "pr": 123,
    "state": "OPEN",
    "monitor_mode": "manual",
    "schedule_id": None,
    "origin_transcript": "unused",
    "known_comment_ids": [],
    "known_failing_checks": [],
    "known_conflict_head": None,
    "last_head_sha": "aaaa111",
    "last_checked_at": "2026-01-01T00:00:00Z",
    "poll_interval_seconds": 60,
    "escalations": [],
    "cycle_ledger": [],
}


class StateMergeGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        subprocess.run(
            ["git", "init", "--quiet", str(self.workdir)],
            check=True,
            capture_output=True,
        )
        init_file = self.workdir / "init.json"
        init_file.write_text(json.dumps(INITIAL_STATE))
        self._run_prm("state-init", "123", str(init_file), check=True)
        self.state_file = self.workdir / ".claude" / ".pr-monitor" / "PR-123.json"
        self.assertTrue(self.state_file.is_file())

    def _run_prm(self, *args, check=False):
        return subprocess.run(
            ["bash", str(PRM), *args],
            cwd=self.workdir,
            check=check,
            capture_output=True,
            text=True,
        )

    def _state_merge(self, patch):
        patch_file = self.workdir / "patch.json"
        patch_file.write_text(json.dumps(patch))
        return self._run_prm("state-merge", "123", str(patch_file))

    def _read_state(self):
        return json.loads(self.state_file.read_text())

    def test_scalar_patch_without_protected_fields_is_merged(self):
        result = self._state_merge({"last_checked_at": "2026-01-02T00:00:00Z"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._read_state()["last_checked_at"], "2026-01-02T00:00:00Z"
        )

    def test_patch_with_known_conflict_head_is_rejected(self):
        before = self._read_state()
        result = self._state_merge({"known_conflict_head": "bbbb222"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("known_conflict_head", result.stderr)
        self.assertEqual(self._read_state(), before)

    def test_patch_with_last_head_sha_is_rejected(self):
        before = self._read_state()
        result = self._state_merge({"last_head_sha": "bbbb222"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._read_state(), before)

    def test_patch_with_state_is_rejected(self):
        before = self._read_state()
        result = self._state_merge({"state": "MERGED"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._read_state(), before)

    def test_mixed_patch_including_protected_field_is_rejected_atomically(self):
        # A patch mixing an allowed scalar with a protected field must be
        # rejected as a whole — no partial application of the allowed part.
        before = self._read_state()
        result = self._state_merge(
            {"last_checked_at": "2026-01-03T00:00:00Z", "state": "CLOSED"}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._read_state(), before)


if __name__ == "__main__":
    unittest.main()
