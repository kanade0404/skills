from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

from textlint_runner import SKILL_ROOT, TextlintError, ensure_installed

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Literal

GUIDELINES = SKILL_ROOT / "config" / "guidelines.md"
_FALSY = frozenset({"0", "false", "no", "off"})


def context_enabled(env: Mapping[str, str]) -> bool:
    value = env.get("TEXTLINT_SESSION_CONTEXT") or env.get("CLAUDE_PLUGIN_OPTION_SESSION_CONTEXT") or "true"
    return value.lower() not in _FALSY


def run(env: Mapping[str, str]) -> tuple[str, str]:
    stderr = ""
    try:
        _ = ensure_installed(env)
    except (TextlintError, OSError) as error:
        stderr = f"ja-writing: {error}\n"
    if not context_enabled(env):
        return "", stderr
    try:
        guidelines = GUIDELINES.read_text(encoding="utf-8")
    except OSError as error:
        return "", f"{stderr}ja-writing: {error}\n"
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": guidelines,
        }
    }
    return json.dumps(output, ensure_ascii=False), stderr


def main() -> Literal[0]:
    stdout, stderr = run(os.environ)
    _ = sys.stdout.write(stdout)
    _ = sys.stderr.write(stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
