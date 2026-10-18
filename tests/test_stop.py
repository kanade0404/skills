from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

import stop
from protocol import StopInput
from support import AI_LIKE, CLEAN, set_stdin

if TYPE_CHECKING:
    from pathlib import Path


def entry(kind: str, content: object) -> str:
    return json.dumps({"type": kind, "message": {"content": content}})


TRANSCRIPT = [
    entry("user", "最初の発言"),
    entry("assistant", [{"type": "text", "text": "古い答え"}]),
    entry("user", [{"type": "text", "text": "次の発言"}]),
    entry("assistant", [{"type": "text", "text": "前半"}, {"type": "tool_use", "name": "Bash"}]),
    entry("user", [{"type": "tool_result", "content": "ok"}]),
    "",
    "not json",
    json.dumps({"type": "assistant", "message": "broken"}),
    entry("assistant", [{"type": "text", "text": "後半"}, {"type": "text", "text": ""}]),
]


class TestLastTurnText:
    def test_takes_text_after_last_prompt(self) -> None:
        assert stop.last_turn_text(TRANSCRIPT) == "前半\n\n後半"

    def test_without_prompt(self) -> None:
        assert stop.last_turn_text([entry("assistant", "だけ")]) == "だけ"


class TestAnswerText:
    def test_last_assistant_message(self) -> None:
        assert stop.answer_text(StopInput("答え", None, stop_hook_active=False)) == "答え"

    def test_transcript(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        _ = path.write_text("\n".join(TRANSCRIPT), encoding="utf-8")
        assert stop.answer_text(StopInput(None, str(path), stop_hook_active=False)) == "前半\n\n後半"

    def test_missing_transcript(self, tmp_path: Path) -> None:
        assert not stop.answer_text(StopInput(None, str(tmp_path / "none.jsonl"), stop_hook_active=False))

    def test_nothing(self) -> None:
        assert not stop.answer_text(StopInput(None, None, stop_hook_active=False))


class TestBuildOutput:
    def test_notify_short(self) -> None:
        output = stop.build_output(["a", "b"], "notify")
        assert output == {"systemMessage": "textlint: 答えに 2 件の指摘\na\nb"}

    def test_notify_long(self) -> None:
        output = stop.build_output([str(index) for index in range(7)], "notify")
        assert output == {"systemMessage": "textlint: 答えに 7 件の指摘\n0\n1\n2\n3\n4\nほか 2 件"}

    def test_block(self) -> None:
        reason = (
            "直前の答えに textlint の指摘が 1 件あります。\na\n\n指摘された箇所を直した答えを、短く書き直してください。"
        )
        assert stop.build_output(["a"], "block") == {"decision": "block", "reason": reason}


class TestRun:
    @pytest.mark.parametrize(
        ("stdin", "env"),
        [
            (json.dumps({"last_assistant_message": AI_LIKE}), {"TEXTLINT_STOP_MODE": "off"}),
            ("not json", {}),
            (json.dumps({"last_assistant_message": AI_LIKE, "stop_hook_active": True}), {}),
            (json.dumps({"last_assistant_message": "English only"}), {}),
        ],
    )
    def test_skips(self, stdin: str, env: dict[str, str]) -> None:
        assert stop.run(stdin, env) == ("", "")

    def test_reports_textlint_error(self, tmp_path: Path) -> None:
        stdout, stderr = stop.run(json.dumps({"last_assistant_message": CLEAN}), {"PATH": str(tmp_path)})
        assert not stdout
        assert "bun が見つかりません" in stderr

    def test_reports_install_error(self, tmp_path: Path, real_env: dict[str, str]) -> None:
        _ = (tmp_path / "file").write_text("", encoding="utf-8")
        env = {**real_env, "TEXTLINT_SKILL_DEPS_DIR": str(tmp_path / "file" / "deps")}
        stdout, stderr = stop.run(json.dumps({"last_assistant_message": CLEAN}), env)
        assert not stdout
        assert stderr.startswith("ja-writing: ")

    def test_clean(self, real_env: dict[str, str]) -> None:
        assert stop.run(json.dumps({"last_assistant_message": CLEAN}), real_env) == ("", "")

    def test_notify(self, real_env: dict[str, str]) -> None:
        stdout, _stderr = stop.run(json.dumps({"last_assistant_message": AI_LIKE}), real_env)
        assert "systemMessage" in stdout

    def test_block(self, real_env: dict[str, str]) -> None:
        env = {**real_env, "CLAUDE_PLUGIN_OPTION_STOP_MODE": "block"}
        stdout, _stderr = stop.run(json.dumps({"last_assistant_message": AI_LIKE}), env)
        assert '"decision": "block"' in stdout


def test_main(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    set_stdin(monkeypatch, "not json")
    assert stop.main() == 0
    assert not capsys.readouterr().out
