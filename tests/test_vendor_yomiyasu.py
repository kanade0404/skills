"""vendor_yomiyasu のテスト."""

from __future__ import annotations

import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - テストで git を固定の引数で起動する
import sys
from typing import TYPE_CHECKING

import pytest

import vendor_yomiyasu
from support import write_executable

if TYPE_CHECKING:
    from pathlib import Path

SHA = "0123456789abcdef0123456789abcdef01234567"


def git(repo: Path, *args: str) -> None:
    """テスト用のリポジトリで git を実行する."""
    executable = shutil.which("git")
    assert executable is not None
    _ = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - テストで固定の引数の git を実行する
        [executable, "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """上流のリポジトリを模したものを作る.

    Returns:
        リポジトリのパス。
    """
    repo = tmp_path / "upstream"
    skill = repo / "skills" / "yomiyasu"
    for relative, body in {
        "SKILL.md": "---\nname: yomiyasu\n---\n\n# yomiyasu\n",
        "LICENSE": "MIT License\n",
        "scripts/a.py": "#!/usr/bin/env python3\nprint(1)\n",
        "references/lists/x.txt": "# list\nword\n",
        "references/b.md": "# B\n",
        "assets/logo.png": "png",
        ".claude-plugin/plugin.json": "{}",
    }.items():
        path = skill / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(body, encoding="utf-8")
    _ = (repo / ".gitignore").write_text("secret.txt\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    # git が無視するファイルは写さない。
    _ = (skill / "secret.txt").write_text("token", encoding="utf-8")
    return repo


class TestAnnotate:
    def test_license_is_untouched(self) -> None:
        assert vendor_yomiyasu.annotate("MIT\n", "LICENSE", SHA) == "MIT\n"
        assert vendor_yomiyasu.annotate("x\n", "UNICODE-LICENSE.txt", SHA) == "x\n"

    def test_unknown_type(self) -> None:
        assert vendor_yomiyasu.annotate("x", "a.png", SHA) == "x"

    def test_shebang_stays_first(self) -> None:
        out = vendor_yomiyasu.annotate("#!/usr/bin/env python3\nx\n", "scripts/a.py", SHA)
        assert out.startswith(f"#!/usr/bin/env python3\n# 参照元: {vendor_yomiyasu.REPO}/blob/{SHA}/skills/yomiyasu/")

    def test_frontmatter_stays_first(self) -> None:
        out = vendor_yomiyasu.annotate("---\nname: x\n---\n\n# x\n", "SKILL.md", SHA)
        assert out.startswith("---\nname: x\n---\n\n<!-- 参照元: ")

    def test_plain(self) -> None:
        assert vendor_yomiyasu.annotate("# B\n", "b.md", SHA).startswith("<!-- 参照元: ")
        assert vendor_yomiyasu.annotate("w\n", "x.txt", SHA).startswith("# 参照元: ")


class TestVendor:
    def test_copies_and_annotates(self, upstream: Path, tmp_path: Path) -> None:
        destination = tmp_path / "out"
        sha = vendor_yomiyasu.vendor(upstream, destination)
        assert len(sha) == 40
        assert sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*.*")) == [
            "SKILL.md",
            "references/b.md",
            "references/lists/x.txt",
            "scripts/a.py",
        ]
        assert (destination / "LICENSE").read_text(encoding="utf-8") == "MIT License\n"
        assert sha in (destination / "references" / "b.md").read_text(encoding="utf-8")

    def test_replaces_existing_copy(self, upstream: Path, tmp_path: Path) -> None:
        destination = tmp_path / "out"
        destination.mkdir()
        _ = (destination / "stale.md").write_text("old", encoding="utf-8")
        _ = vendor_yomiyasu.vendor(upstream, destination)
        assert not (destination / "stale.md").exists()
        assert (destination / "SKILL.md").is_file()
        assert sorted(path.name for path in tmp_path.iterdir()) == ["out", "upstream"]

    def test_symlink_is_rejected_and_existing_copy_is_kept(self, upstream: Path, tmp_path: Path) -> None:
        (upstream / "skills" / "yomiyasu" / "link").symlink_to("/etc/hosts")
        git(upstream, "add", ".")
        git(upstream, "commit", "-q", "-m", "link")
        destination = tmp_path / "out"
        destination.mkdir()
        _ = (destination / "keep.md").write_text("keep", encoding="utf-8")
        with pytest.raises(vendor_yomiyasu.VendorError, match="シンボリックリンクは写しません"):
            _ = vendor_yomiyasu.vendor(upstream, destination)
        assert (destination / "keep.md").read_text(encoding="utf-8") == "keep"
        assert sorted(path.name for path in tmp_path.iterdir()) == ["out", "upstream"]

    def test_dirty_tree(self, upstream: Path, tmp_path: Path) -> None:
        _ = (upstream / "new.txt").write_text("x", encoding="utf-8")
        with pytest.raises(vendor_yomiyasu.VendorError, match="作業ツリーに変更があります"):
            _ = vendor_yomiyasu.vendor(upstream, tmp_path / "out")

    def test_missing_skill_keeps_existing_copy(self, upstream: Path, tmp_path: Path) -> None:
        git(upstream, "rm", "-q", "-r", "skills")
        git(upstream, "commit", "-q", "-m", "rm")
        existing = tmp_path / "out"
        existing.mkdir()
        with pytest.raises(vendor_yomiyasu.VendorError, match=r"SKILL\.md がありません"):
            _ = vendor_yomiyasu.vendor(upstream, existing)
        assert existing.is_dir()

    def test_not_a_repository(self, tmp_path: Path) -> None:
        with pytest.raises(vendor_yomiyasu.VendorError, match=r"git rev-parse HEAD が失敗しました"):
            _ = vendor_yomiyasu.vendor(tmp_path, tmp_path / "out")

    def test_bad_sha(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bin_dir = tmp_path / "bin"
        _ = write_executable(bin_dir, "git", "print('not-a-sha')\n")
        monkeypatch.setenv("PATH", str(bin_dir))
        with pytest.raises(vendor_yomiyasu.VendorError, match="コミットを取得できません"):
            _ = vendor_yomiyasu.vendor(tmp_path, tmp_path / "out")

    def test_git_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(vendor_yomiyasu.VendorError, match="git が見つかりません"):
            _ = vendor_yomiyasu.vendor(tmp_path, tmp_path / "out")


class TestRun:
    def test_usage(self) -> None:
        assert vendor_yomiyasu.run([]) == 2

    def test_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert vendor_yomiyasu.run([str(tmp_path)]) == 1
        assert "失敗しました" in capsys.readouterr().err

    def test_success(
        self, upstream: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(vendor_yomiyasu, "DESTINATION", tmp_path / "out")
        monkeypatch.setattr(vendor_yomiyasu.vendor, "__defaults__", (tmp_path / "out",))
        assert vendor_yomiyasu.run([str(upstream)]) == 0
        assert "へ写しました" in capsys.readouterr().out


def test_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["vendor_yomiyasu.py"])
    assert vendor_yomiyasu.main() == 2
