"""CI wiring for tests/session-setup-test.sh.

session-setup-test.sh is a hermetic bash suite (7 tests) covering the
SessionStart hook's build-authorization gate (scripts/session-setup.sh):
nix/direnv presence detection, SKILLS_DIRENV_AUTO_ALLOW opt-in, and the
direnv-allowed-root gate. It never touches real nix/direnv — each test
prepends a stub bin directory (fake `nix` / `direnv`) in front of a minimal
system PATH, so it is safe to run unattended on CI (ubuntu-latest, no nix or
direnv installed).

CI (.github/workflows/trigger-evals.yml) runs `python3 -m unittest discover
-s tests`, which only discovers `test_*.py` — a bare `.sh` script is invisible
to it, so the suite previously only ran when someone remembered to invoke it
by hand. This wrapper runs it as a subprocess so unittest discovery wires it
into CI automatically.
"""

from __future__ import annotations

import pathlib
import subprocess
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITE = REPO_ROOT / "tests" / "session-setup-test.sh"


class SessionSetupShTest(unittest.TestCase):
    def test_session_setup_test_sh_suite_passes(self) -> None:
        result = subprocess.run(
            ["bash", str(SUITE)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"tests/session-setup-test.sh failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
