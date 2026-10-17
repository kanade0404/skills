"""post_edit のテスト."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

import post_edit
from support import AI_LIKE, CLEAN, set_stdin
from textlint_runner import DocumentResult, Message, Report

if TYPE_CHECKING:
    from pathlib import Path


def payload(cwd: Path, tool_input: object) -> str:
    """フックの入力を作る.

    Returns:
        JSON の文字列。
    """
    return json.dumps({"cwd": str(cwd), "tool_input": tool_input})


class TestTouchedFiles:
    def test_claude_code(self) -> None:
        assert post_edit.touched_files({"file_path": "/a/b.md"}) == ["/a/b.md"]

    def test_codex_patch(self) -> None:
        patch = "*** Begin Patch\n*** Update File: a.md\n*** Move to: b.md\n*** Add File: c.txt\n*** End Patch"
        assert post_edit.touched_files({"command": patch}) == ["a.md", "b.md", "c.txt"]

    def test_codex_patch_as_list(self) -> None:
        assert post_edit.touched_files({"command": ["apply_patch", "*** Add File: a.md"]}) == ["a.md"]

    @pytest.mark.parametrize("tool_input", [None, "x", {}, {"command": 1}])
    def test_nothing(self, tool_input: object) -> None:
        assert post_edit.touched_files(tool_input) == []


class TestReadLintable:
    def test_kana(self, tmp_path: Path) -> None:
        path = tmp_path / "a.MD"
        _ = path.write_text(CLEAN, encoding="utf-8")
        assert post_edit.read_lintable(path) == CLEAN

    def test_wrong_extension(self, tmp_path: Path) -> None:
        path = tmp_path / "a.py"
        _ = path.write_text(CLEAN, encoding="utf-8")
        assert post_edit.read_lintable(path) is None

    def test_no_kana(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        _ = path.write_text("# English only\n", encoding="utf-8")
        assert post_edit.read_lintable(path) is None

    def test_unreadable(self, tmp_path: Path) -> None:
        assert post_edit.read_lintable(tmp_path / "missing.md") is None

    def test_not_utf8(self, tmp_path: Path) -> None:
        path = tmp_path / "a.md"
        _ = path.write_bytes("テスト".encode("cp932"))
        assert post_edit.read_lintable(path) is None


class TestFormatMessages:
    def test_filters_info_and_shows_relative_path(self, tmp_path: Path) -> None:
        messages = (Message("r", "error", 1, 2, 2), Message("r", "warn", 3, 4, 1), Message("r", "info", 5, 6, 3))
        report = Report((DocumentResult(str(tmp_path / "a.md"), messages, None),), (), "s")
        assert post_edit.format_messages(report, tmp_path) == ["a.md:1:2 [r] error", "a.md:3:4 [r] warn"]

    def test_other_drive(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def relpath(_path: str, _start: object) -> str:
            raise ValueError

        monkeypatch.setattr(os.path, "relpath", relpath)
        report = Report((DocumentResult("C:/a.md", (Message("r", "m", 1, 1, 2),), None),), (), "s")
        assert post_edit.format_messages(report, tmp_path) == ["C:/a.md:1:1 [r] m"]


@pytest.mark.parametrize(
    ("mode", "key"), [("block", "decision"), ("warn", "hookSpecificOutput"), ("other", "decision")]
)
def test_build_output(mode: str, key: str) -> None:
    assert key in post_edit.build_output(["x"], mode)


class TestRun:
    def test_off(self, tmp_path: Path) -> None:
        assert post_edit.run(payload(tmp_path, {}), {"TEXTLINT_HOOK_MODE": "off"}) == ("", "")

    @pytest.mark.parametrize("stdin", ["", "not json", "[]"])
    def test_bad_input(self, stdin: str) -> None:
        assert post_edit.run(stdin, {}) == ("", "")

    def test_nothing_to_lint(self, tmp_path: Path) -> None:
        assert post_edit.run(payload(tmp_path, {"file_path": "missing.md"}), {}) == ("", "")

    def test_reports_textlint_error(self, tmp_path: Path) -> None:
        _ = (tmp_path / "a.md").write_text(CLEAN, encoding="utf-8")
        stdout, stderr = post_edit.run(payload(tmp_path, {"file_path": "a.md"}), {"PATH": str(tmp_path)})
        assert not stdout
        assert "node が見つかりません" in stderr

    def test_clean(self, tmp_path: Path, real_env: dict[str, str]) -> None:
        _ = (tmp_path / "a.md").write_text(CLEAN, encoding="utf-8")
        assert post_edit.run(payload(tmp_path, {"file_path": "a.md"}), real_env) == ("", "")

    def test_block(self, tmp_path: Path, real_env: dict[str, str]) -> None:
        _ = (tmp_path / "a.md").write_text(AI_LIKE, encoding="utf-8")
        stdout, _stderr = post_edit.run(payload(tmp_path, {"file_path": str(tmp_path / "a.md")}), real_env)
        output = post_edit.as_dict(post_edit.parse_json(stdout))
        assert output["decision"] == "block"
        assert "a.md:3:1 [@textlint-ja/ai-writing/no-ai-list-formatting]" in str(output["reason"])

    def test_project_dir_from_env(self, tmp_path: Path, real_env: dict[str, str]) -> None:
        _ = (tmp_path / "a.md").write_text(AI_LIKE, encoding="utf-8")
        env = {**real_env, "CLAUDE_PROJECT_DIR": str(tmp_path), "CLAUDE_PLUGIN_OPTION_HOOK_MODE": "warn"}
        stdout, _stderr = post_edit.run(json.dumps({"tool_input": {"file_path": "a.md"}}), env)
        assert "additionalContext" in stdout

    def test_project_dir_from_process(
        self, tmp_path: Path, real_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _ = (tmp_path / "a.md").write_text(AI_LIKE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        stdout, _stderr = post_edit.run(json.dumps({"cwd": 1, "tool_input": {"file_path": "a.md"}}), real_env)
        assert "decision" in stdout


def test_main(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    set_stdin(monkeypatch, "not json")
    assert post_edit.main() == 0
    assert not capsys.readouterr().out
