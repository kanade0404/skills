from __future__ import annotations

import json
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - テストで git を固定の引数で起動する
import sys
from typing import TYPE_CHECKING

import pytest

import vendor_skill
from support import write_executable

if TYPE_CHECKING:
    from pathlib import Path

SHA = "0123456789abcdef0123456789abcdef01234567"
SPEC = vendor_skill.Spec("plugins/p/skills/s", "https://github.com/o/r", "skills/s", ("assets",))
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(repo: Path, *args: str) -> None:
    executable = shutil.which("git")
    assert executable is not None
    _ = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - テストで固定の引数の git を実行する
        [executable, "-C", str(repo), *args], check=True, capture_output=True, env={**os.environ, **GIT_ENV}
    )


def make_repo(repo: Path, files: dict[str, str]) -> Path:
    for relative, body in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(body, encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    return repo


SKILL_FILES = {
    "skills/s/SKILL.md": "---\nname: s\n---\n\n# s\n",
    "skills/s/scripts/a.py": "#!/usr/bin/env python3\nprint(1)\n",
    "skills/s/references/x.txt": "# list\nword\n",
    "skills/s/agents/openai.yaml": "interface: {}\n",
    "skills/s/assets/logo.png": "png",
    "skills/s/.claude-plugin/plugin.json": "{}",
    ".gitignore": "secret.txt\n",
}


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    repo = make_repo(tmp_path / "upstream", {**SKILL_FILES, "skills/s/LICENSE": "MIT (skill)\n"})
    _ = (repo / "skills" / "s" / "secret.txt").write_text("token", encoding="utf-8")
    return repo


def copied(root: Path) -> list[str]:
    destination = root / SPEC.destination
    return sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file())


class TestLoadSpecs:
    def test_repository_config(self) -> None:
        specs = vendor_skill.load_specs()
        assert specs["plugins/ja-writing/skills/yomiyasu"].exclude == ("assets",)
        assert specs["plugins/productivity/skills/handoff"].repository == "https://github.com/mattpocock/skills"

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            ({}, "配列ではありません"),
            (["x"], "オブジェクトではありません"),
            ([{"destination": "d", "repository": "r"}], "path がありません"),
            ([{"destination": "d", "repository": "r", "path": "p", "exclude": "x"}], "exclude が配列ではありません"),
        ],
    )
    def test_invalid(self, tmp_path: Path, content: object, message: str) -> None:
        path = tmp_path / "vendored.json"
        _ = path.write_text(json.dumps(content), encoding="utf-8")
        with pytest.raises(vendor_skill.VendorError, match=message):
            _ = vendor_skill.load_specs(path)


def test_source_note() -> None:
    note = vendor_skill.source_note(SPEC, SHA)
    assert f"- コミット: {SHA}\n" in note
    assert f"- URL: https://github.com/o/r/tree/{SHA}/skills/s\n" in note


class TestVendor:
    def test_copies_skill_with_its_license(self, upstream: Path, tmp_path: Path) -> None:
        sha = vendor_skill.vendor(upstream, SPEC, tmp_path)
        assert copied(tmp_path) == [
            "LICENSE",
            "SKILL.md",
            "SOURCE.md",
            "agents/openai.yaml",
            "references/x.txt",
            "scripts/a.py",
        ]
        destination = tmp_path / SPEC.destination
        assert (destination / "LICENSE").read_text(encoding="utf-8") == "MIT (skill)\n"
        assert (destination / "SOURCE.md").read_text(encoding="utf-8") == vendor_skill.source_note(SPEC, sha)

    def test_files_are_copied_unchanged(self, upstream: Path, tmp_path: Path) -> None:
        _ = vendor_skill.vendor(upstream, SPEC, tmp_path)
        destination = tmp_path / SPEC.destination
        for relative, body in SKILL_FILES.items():
            target = destination / relative.removeprefix("skills/s/")
            if target.is_file():
                assert target.read_text(encoding="utf-8") == body

    def test_upstream_source_note_is_rejected(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "upstream", {**SKILL_FILES, "skills/s/LICENSE": "MIT\n", "skills/s/SOURCE.md": "x"})
        with pytest.raises(vendor_skill.VendorError, match=r"SOURCE\.md があり"):
            _ = vendor_skill.vendor(repo, SPEC, tmp_path)

    @pytest.mark.parametrize("name", ["LICENSE", "LICENSE.md"])
    def test_copies_root_license(self, tmp_path: Path, name: str) -> None:
        repo = make_repo(tmp_path / "upstream", {**SKILL_FILES, name: "MIT (root)\n"})
        _ = vendor_skill.vendor(repo, SPEC, tmp_path)
        assert (tmp_path / SPEC.destination / "LICENSE").read_text(encoding="utf-8") == "MIT (root)\n"

    def test_no_license(self, tmp_path: Path) -> None:
        repo = make_repo(tmp_path / "upstream", SKILL_FILES)
        with pytest.raises(vendor_skill.VendorError, match="ライセンスのファイルがありません"):
            _ = vendor_skill.vendor(repo, SPEC, tmp_path)

    def test_replaces_existing_copy(self, upstream: Path, tmp_path: Path) -> None:
        destination = tmp_path / SPEC.destination
        destination.mkdir(parents=True)
        _ = (destination / "stale.md").write_text("old", encoding="utf-8")
        _ = vendor_skill.vendor(upstream, SPEC, tmp_path)
        assert "stale.md" not in copied(tmp_path)
        assert sorted(path.name for path in destination.parent.iterdir()) == ["s"]

    def test_symlink_is_rejected_and_existing_copy_is_kept(self, upstream: Path, tmp_path: Path) -> None:
        (upstream / "skills" / "s" / "link").symlink_to("/etc/hosts")
        git(upstream, "add", ".")
        git(upstream, "commit", "-q", "-m", "link")
        destination = tmp_path / SPEC.destination
        destination.mkdir(parents=True)
        _ = (destination / "keep.md").write_text("keep", encoding="utf-8")
        with pytest.raises(vendor_skill.VendorError, match="シンボリックリンクは写しません"):
            _ = vendor_skill.vendor(upstream, SPEC, tmp_path)
        assert copied(tmp_path) == ["keep.md"]
        assert sorted(path.name for path in destination.parent.iterdir()) == ["s"]

    def test_dirty_tree(self, upstream: Path, tmp_path: Path) -> None:
        _ = (upstream / "new.txt").write_text("x", encoding="utf-8")
        with pytest.raises(vendor_skill.VendorError, match="作業ツリーに変更があります"):
            _ = vendor_skill.vendor(upstream, SPEC, tmp_path)

    def test_missing_skill(self, upstream: Path, tmp_path: Path) -> None:
        missing = vendor_skill.Spec("d", "r", "skills/none", ())
        with pytest.raises(vendor_skill.VendorError, match=r"SKILL\.md がありません"):
            _ = vendor_skill.vendor(upstream, missing, tmp_path)

    def test_not_a_repository(self, tmp_path: Path) -> None:
        with pytest.raises(vendor_skill.VendorError, match=r"git rev-parse HEAD が失敗しました"):
            _ = vendor_skill.vendor(tmp_path, SPEC, tmp_path)

    def test_bad_sha(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bin_dir = tmp_path / "bin"
        _ = write_executable(bin_dir, "git", "print('not-a-sha')\n")
        monkeypatch.setenv("PATH", str(bin_dir))
        with pytest.raises(vendor_skill.VendorError, match="コミットを取得できません"):
            _ = vendor_skill.vendor(tmp_path, SPEC, tmp_path)

    def test_git_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(vendor_skill.VendorError, match="git が見つかりません"):
            _ = vendor_skill.vendor(tmp_path, SPEC, tmp_path)


class TestRun:
    @pytest.fixture
    def config(self, tmp_path: Path) -> Path:
        path = tmp_path / "vendored.json"
        entry = {"destination": SPEC.destination, "repository": SPEC.repository, "path": SPEC.path}
        _ = path.write_text(json.dumps([entry]), encoding="utf-8")
        return path

    def test_usage(self) -> None:
        assert vendor_skill.run([]) == 2

    def test_unknown_destination(self, config: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert vendor_skill.run(["plugins/x", "."], config) == 1
        assert "tools/vendored.json に plugins/x がありません" in capsys.readouterr().err

    def test_success(self, upstream: Path, tmp_path: Path, config: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = tmp_path / "root"
        assert vendor_skill.run([f"{SPEC.destination}/", str(upstream)], config, root) == 0
        assert "へ写しました" in capsys.readouterr().out
        assert (root / SPEC.destination / "SKILL.md").is_file()


def test_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["vendor_skill.py"])
    assert vendor_skill.main() == 2
