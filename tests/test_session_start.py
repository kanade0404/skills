"""session_start のテスト."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import session_start
from post_edit import as_dict, parse_json
from textlint_runner import SKILL_ROOT

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, True),
        ({"TEXTLINT_SESSION_CONTEXT": "0"}, False),
        ({"CLAUDE_PLUGIN_OPTION_SESSION_CONTEXT": "false"}, False),
        ({"TEXTLINT_SESSION_CONTEXT": "yes"}, True),
    ],
)
def test_context_enabled(env: dict[str, str], *, expected: bool) -> None:
    assert session_start.context_enabled(env) is expected


def test_injects_guidelines(real_env: dict[str, str]) -> None:
    stdout, stderr = session_start.run(real_env)
    context = as_dict(as_dict(parse_json(stdout)).get("hookSpecificOutput")).get("additionalContext")
    assert context == session_start.GUIDELINES.read_text(encoding="utf-8")
    assert not stderr


def test_install_failure_still_injects(tmp_path: Path) -> None:
    stdout, stderr = session_start.run({"PATH": str(tmp_path), "TEXTLINT_SKILL_DEPS_DIR": str(tmp_path / "d")})
    assert "additionalContext" in stdout
    assert "bun が見つかりません" in stderr


def test_disabled(real_env: dict[str, str]) -> None:
    assert session_start.run({**real_env, "TEXTLINT_SESSION_CONTEXT": "off"}) == ("", "")


def test_main(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXTLINT_SESSION_CONTEXT", "off")
    monkeypatch.setenv("TEXTLINT_SKILL_DEPS_DIR", str(SKILL_ROOT))
    assert session_start.main() == 0
    assert not capsys.readouterr().out
