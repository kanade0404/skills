from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import pytest

import vendored_paths
from vendored_paths import BEGIN, END

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "vendored.json"
    entries = [{"destination": name, "repository": "r", "path": "p"} for name in ("b/s", "a/s")]
    _ = path.write_text(json.dumps(entries), encoding="utf-8")
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "root"
    directory.mkdir()
    _ = (directory / "pyproject.toml").write_text(f'x = [\n  {BEGIN}\n  "old",\n  {END}\n]\n', encoding="utf-8")
    _ = (directory / ".coderabbit.yaml").write_text(f"f:\n    {BEGIN}\n    {END}\n", encoding="utf-8")
    return directory


def test_repository_is_in_sync() -> None:
    assert vendored_paths.sync(write=False) == []


class TestRender:
    def test_replaces_between_markers_with_indent(self) -> None:
        text = f"a\n  {BEGIN}\n  old\n  {END}\nb"
        assert vendored_paths.render(text, ["x", "y"], "f") == f"a\n  {BEGIN}\n  x\n  y\n  {END}\nb"

    @pytest.mark.parametrize("text", ["a\n", f"{BEGIN}\n", f"{END}\n{BEGIN}\n"])
    def test_missing_markers(self, text: str) -> None:
        with pytest.raises(vendored_paths.SyncError, match="印がありません"):
            _ = vendored_paths.render(text, [], "f")


class TestSync:
    def test_writes_stale_files(self, root: Path, config: Path) -> None:
        assert vendored_paths.sync(root, config) == ["pyproject.toml", ".coderabbit.yaml"]
        assert '"a/s",\n  "b/s",' in (root / "pyproject.toml").read_text(encoding="utf-8")
        assert '- "!a/s/**"\n    - "!b/s/**"' in (root / ".coderabbit.yaml").read_text(encoding="utf-8")
        assert vendored_paths.sync(root, config) == []

    def test_check_does_not_write(self, root: Path, config: Path) -> None:
        before = (root / "pyproject.toml").read_text(encoding="utf-8")
        assert vendored_paths.sync(root, config, write=False) == ["pyproject.toml", ".coderabbit.yaml"]
        assert (root / "pyproject.toml").read_text(encoding="utf-8") == before


class TestRun:
    def test_sync(self, root: Path, config: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert vendored_paths.run(["sync"], root, config) == 0
        assert "pyproject.toml を書き直しました" in capsys.readouterr().out

    def test_semgrep(self, config: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert vendored_paths.run(["semgrep"], config=config) == 0
        assert capsys.readouterr().out == "--exclude\na/s\n--exclude\nb/s\n"

    def test_review(self, config: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert vendored_paths.run(["review"], config=config) == 0
        assert capsys.readouterr().out == "a/s/、b/s/\n"

    @pytest.mark.parametrize("args", [[], ["x"], ["sync", "x"]])
    def test_usage(self, args: list[str]) -> None:
        assert vendored_paths.run(args) == 2

    def test_missing_markers(self, root: Path, config: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _ = (root / "pyproject.toml").write_text("x = []\n", encoding="utf-8")
        assert vendored_paths.run(["sync"], root, config) == 1
        assert "印がありません" in capsys.readouterr().err


def test_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["vendored_paths.py"])
    assert vendored_paths.main() == 2
