"""Lint のテスト."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

import lint
from support import AI_LIKE, CLEAN
from textlint_runner import DocumentResult, Message, Report

if TYPE_CHECKING:
    from pathlib import Path

FIXABLE = "# 紹介\n\nこれは test です。\n"


def write(directory: Path, name: str, text: str) -> Path:
    """ファイルを書く.

    Returns:
        書いたファイルのパス。
    """
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")
    return path


class TestExpand:
    def test_patterns(self, tmp_path: Path) -> None:
        a = write(tmp_path, "a.md", CLEAN)
        b = write(tmp_path, "docs/b.txt", CLEAN)
        c = write(tmp_path, "docs/deep/c.md", CLEAN)
        _ = write(tmp_path, "docs/d.py", CLEAN)
        patterns = ["a.md", "docs/**/*", str(tmp_path / "docs" / "*.txt"), "missing.md"]
        assert lint.expand(patterns, tmp_path) == sorted([a, b, c])


def test_format_report(tmp_path: Path) -> None:
    messages = (Message("r", "m", 1, 2, 2), Message("r", "n", 3, 4, 9))
    report = Report((DocumentResult(str(tmp_path / "a.md"), messages, None),), ("./evil",), "s")
    assert lint.format_report(report, tmp_path) == (
        "a.md:1:2  error  m  r\na.md:3:4  9  n  r\n同梱にないため無視した設定: ./evil\n"
    )


class TestRun:
    @pytest.mark.parametrize("args", [[], ["--fix"], ["--dry-run", "a.md"]])
    def test_usage(self, tmp_path: Path, args: list[str]) -> None:
        assert lint.run(args, {}, tmp_path) == 2

    def test_textlint_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = write(tmp_path, "a.md", CLEAN)
        assert lint.run(["a.md"], {"PATH": str(tmp_path)}, tmp_path) == 2
        assert "bun が見つかりません" in capsys.readouterr().err

    def test_unreadable_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (tmp_path / "a.md").write_bytes("テスト".encode("cp932"))
        assert lint.run(["a.md"], {}, tmp_path) == 2
        assert "UTF-8 として読めません" in capsys.readouterr().err

    def test_errors(self, tmp_path: Path, real_env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
        _ = write(tmp_path, "a.md", AI_LIKE)
        assert lint.run(["a.md"], real_env, tmp_path) == 1
        assert "no-ai-list-formatting" in capsys.readouterr().out

    def test_clean(self, tmp_path: Path, real_env: dict[str, str]) -> None:
        _ = write(tmp_path, "a.md", CLEAN)
        assert lint.run(["a.md"], real_env, tmp_path) == 0

    def test_fix_writes(self, tmp_path: Path, real_env: dict[str, str]) -> None:
        path = write(tmp_path, "a.md", FIXABLE)
        clean = write(tmp_path, "b.md", CLEAN)
        _ = write(tmp_path, ".textlintrc.json", '{"rules": {"preset-ja-spacing": true}}')
        assert lint.run(["--fix", "a.md", "b.md"], real_env, tmp_path) == 0
        assert path.read_text(encoding="utf-8") == "# 紹介\n\nこれはtestです。\n"
        assert clean.read_text(encoding="utf-8") == CLEAN

    def test_dry_run(self, tmp_path: Path, real_env: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
        path = write(tmp_path, "a.md", FIXABLE)
        _ = write(tmp_path, ".textlintrc.json", '{"rules": {"preset-ja-spacing": true}}')
        assert lint.run(["--fix", "--dry-run", "a.md"], real_env, tmp_path) == 0
        assert "+これはtestです。" in capsys.readouterr().out
        assert path.read_text(encoding="utf-8") == FIXABLE


def test_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["lint.py"])
    assert lint.main() == 2
