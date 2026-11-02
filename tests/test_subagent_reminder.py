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


def test_session_start_injects_context() -> None:
    raw = subagent_reminder.run({}, "session-start")
    output = as_object((parse_object(raw) or {}).get("hookSpecificOutput")) or {}
    assert output == {"hookEventName": "SessionStart", "additionalContext": subagent_reminder.SESSION_START_REMINDER}
    assert "/productivity:subagent-judgment" in subagent_reminder.SESSION_START_REMINDER
    assert raw.isascii()


@pytest.mark.parametrize("mode", ["", "session-start"])
def test_disabled(mode: str) -> None:
    assert not subagent_reminder.run({"SUBAGENT_JUDGMENT_REMINDER": "0"}, mode)


def test_hook_matches_agent_tool() -> None:
    hooks = as_object((parse_object(HOOKS.read_text(encoding="utf-8")) or {}).get("hooks")) or {}
    assert [string(entry, "matcher") for entry in objects(hooks.get("PreToolUse"))] == ["Agent|Task"]


def test_session_start_hook_uses_mode() -> None:
    hooks = as_object((parse_object(HOOKS.read_text(encoding="utf-8")) or {}).get("hooks")) or {}
    entries = objects(hooks.get("SessionStart"))
    assert len(entries) == 1
    assert "matcher" not in entries[0]
    commands = [string(hook, "command") or "" for hook in objects(entries[0].get("hooks"))]
    assert len(commands) == 1
    assert commands[0].endswith('subagent_reminder.py" session-start')


def test_main(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUBAGENT_JUDGMENT_REMINDER", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_SUBAGENT_REMINDER", raising=False)
    monkeypatch.setattr("sys.argv", ["subagent_reminder.py"])
    assert subagent_reminder.main() == 0
    assert "PreToolUse" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["subagent_reminder.py", "session-start"])
    assert subagent_reminder.main() == 0
    assert "SessionStart" in capsys.readouterr().out
