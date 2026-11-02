from __future__ import annotations

from pathlib import Path

import pytest

import subagent_reminder
from protocol import as_object, objects, parse_object, string

HOOKS = Path(__file__).resolve().parent.parent / "plugins" / "productivity" / "hooks" / "hooks.json"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, True),
        ({"SUBAGENT_JUDGMENT_REMINDER": "off"}, False),
        ({"CLAUDE_PLUGIN_OPTION_SUBAGENT_REMINDER": "false"}, False),
        ({"SUBAGENT_JUDGMENT_REMINDER": "1", "CLAUDE_PLUGIN_OPTION_SUBAGENT_REMINDER": "false"}, True),
    ],
)
def test_enabled(env: dict[str, str], *, expected: bool) -> None:
    assert subagent_reminder.enabled(env) is expected


def test_injects_context_without_decision() -> None:
    output = as_object((parse_object(subagent_reminder.run({})) or {}).get("hookSpecificOutput")) or {}
    assert output == {"hookEventName": "PreToolUse", "additionalContext": subagent_reminder.REMINDER}
    assert "/productivity:subagent-judgment" in subagent_reminder.REMINDER


def test_disabled() -> None:
    assert not subagent_reminder.run({"SUBAGENT_JUDGMENT_REMINDER": "0"})


def test_hook_matches_agent_tool() -> None:
    hooks = as_object((parse_object(HOOKS.read_text(encoding="utf-8")) or {}).get("hooks")) or {}
    assert [string(entry, "matcher") for entry in objects(hooks.get("PreToolUse"))] == ["Agent|Task"]


def test_main(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUBAGENT_JUDGMENT_REMINDER", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_SUBAGENT_REMINDER", raising=False)
    assert subagent_reminder.main() == 0
    assert "additionalContext" in capsys.readouterr().out
