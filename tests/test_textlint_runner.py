from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from support import AI_LIKE, write_executable
from textlint_runner import (
    CHAT_CONFIG,
    FILE_CONFIG,
    SKILL_ROOT,
    Config,
    Document,
    TextlintError,
    choose_file_config,
    deps_dir,
    ensure_installed,
    installed,
    lint_documents,
    load_config,
)

if TYPE_CHECKING:
    from pathlib import Path

FAKE_BUN = """
import os, pathlib, sys
args = sys.argv[1:]
if args == ["--version"]:
    print(os.environ.get("FAKE_BUN_VERSION", "1.4.2"))
    sys.exit(0)
if args[:1] == ["install"]:
    mode = os.environ.get("FAKE_INSTALL_MODE", "ok")
    if mode == "fail":
        sys.stderr.write("boom")
        sys.exit(1)
    if mode == "race":
        # 別のプロセスが先に入れ終えた状態を作る。
        target = pathlib.Path(os.environ["FAKE_INSTALL_TARGET"])
        (target / "node_modules/@textlint/kernel").mkdir(parents=True)
        (target / "node_modules/@textlint/kernel/package.json").write_text("{}")
    if mode == "occupied":
        # 依存の入っていないディレクトリが先に置かれた状態を作る。
        target = pathlib.Path(os.environ["FAKE_INSTALL_TARGET"])
        target.mkdir(parents=True)
        (target / "junk").write_text("x")
    kernel = pathlib.Path.cwd() / "node_modules/@textlint/kernel"
    kernel.mkdir(parents=True)
    (kernel / "package.json").write_text("{}")
    sys.exit(0)
sys.stdin.read()
sys.stdout.write(os.environ.get("FAKE_RUNNER_STDOUT", ""))
sys.stderr.write("runner failed")
sys.exit(int(os.environ.get("FAKE_RUNNER_CODE", "0")))
"""


def fake_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    _ = write_executable(bin_dir, "bun", FAKE_BUN)
    return {"PATH": str(bin_dir), "TEXTLINT_SKILL_DEPS_DIR": str(tmp_path / "deps"), **extra}


class TestDepsDir:
    def test_override(self, tmp_path: Path) -> None:
        assert deps_dir({"TEXTLINT_SKILL_DEPS_DIR": str(tmp_path)}) == tmp_path

    def test_xdg_cache(self, tmp_path: Path) -> None:
        directory = deps_dir({"XDG_CACHE_HOME": str(tmp_path)})
        assert directory.parent == tmp_path / "ja-writing-textlint"
        assert len(directory.name) == 12

    def test_home_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        assert deps_dir({}).parent == tmp_path / ".cache" / "ja-writing-textlint"


class TestEnsureInstalled:
    def test_already_installed(self) -> None:
        env = {"PATH": "", "TEXTLINT_SKILL_DEPS_DIR": str(SKILL_ROOT)}
        assert ensure_installed(env) == SKILL_ROOT

    def test_bun_missing(self, tmp_path: Path) -> None:
        with pytest.raises(TextlintError, match="bun が見つかりません"):
            _ = ensure_installed({"PATH": str(tmp_path), "TEXTLINT_SKILL_DEPS_DIR": str(tmp_path / "deps")})

    def test_installs(self, tmp_path: Path) -> None:
        directory = ensure_installed(fake_env(tmp_path))
        assert installed(directory)
        assert (directory / "bun.lock").is_file()
        assert {path.name for path in tmp_path.iterdir()} == {"bin", "deps"}

    def test_install_fails_and_leaves_nothing(self, tmp_path: Path) -> None:
        with pytest.raises(TextlintError, match="boom"):
            _ = ensure_installed(fake_env(tmp_path, FAKE_INSTALL_MODE="fail"))
        assert {path.name for path in tmp_path.iterdir()} == {"bin"}

    def test_other_process_finished_first(self, tmp_path: Path) -> None:
        env = fake_env(tmp_path, FAKE_INSTALL_MODE="race", FAKE_INSTALL_TARGET=str(tmp_path / "deps"))
        assert installed(ensure_installed(env))
        assert {path.name for path in tmp_path.iterdir()} == {"bin", "deps"}

    def test_target_occupied_without_deps(self, tmp_path: Path) -> None:
        env = fake_env(tmp_path, FAKE_INSTALL_MODE="occupied", FAKE_INSTALL_TARGET=str(tmp_path / "deps"))
        with pytest.raises(OSError, match="deps"):
            _ = ensure_installed(env)
        assert {path.name for path in tmp_path.iterdir()} == {"bin", "deps"}


class TestLoadConfig:
    def test_valid(self) -> None:
        config = load_config(CHAT_CONFIG)
        assert "rules" in config.data
        assert config.source == str(CHAT_CONFIG)

    def test_missing(self, tmp_path: Path) -> None:
        with pytest.raises(TextlintError, match="読めません"):
            _ = load_config(tmp_path / "none.json")

    def test_not_json(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        _ = path.write_text("rules: {}", encoding="utf-8")
        with pytest.raises(TextlintError, match="読めません"):
            _ = load_config(path)

    def test_not_object(self, tmp_path: Path) -> None:
        path = tmp_path / "a.json"
        _ = path.write_text("[]", encoding="utf-8")
        with pytest.raises(TextlintError, match="オブジェクトではありません"):
            _ = load_config(path)


class TestChooseFileConfig:
    def test_explicit(self, tmp_path: Path) -> None:
        path = tmp_path / "mine.json"
        _ = path.write_text('{"rules": {}}', encoding="utf-8")
        _ = (tmp_path / ".textlintrc.json").write_text('{"rules": {"x": true}}', encoding="utf-8")
        assert choose_file_config(tmp_path, {"TEXTLINT_CONFIG": str(path)}) == Config({"rules": {}}, str(path))

    @pytest.mark.parametrize("name", [".textlintrc.json", ".textlintrc"])
    def test_project_json(self, tmp_path: Path, name: str) -> None:
        _ = (tmp_path / name).write_text('{"rules": {"x": true}}', encoding="utf-8")
        assert choose_file_config(tmp_path, {}).source == str(tmp_path / name)

    def test_project_yaml_is_not_used(self, tmp_path: Path) -> None:
        _ = (tmp_path / ".textlintrc.yml").write_text("rules: {}", encoding="utf-8")
        config = choose_file_config(tmp_path, {})
        assert config.data == load_config(FILE_CONFIG).data
        assert "JSON でないため使っていません" in config.source

    def test_bundled(self, tmp_path: Path) -> None:
        assert choose_file_config(tmp_path, {}) == load_config(FILE_CONFIG)


class TestLintDocumentsWithKernel:
    def test_lint(self, real_env: dict[str, str]) -> None:
        config = Config({**load_config(FILE_CONFIG).data, "plugins": {"./evil": True}}, "test")
        report = lint_documents([Document("/x/a.md", AI_LIKE)], config=config, env=real_env)
        rules = {message.rule_id for message in report.results[0].messages}
        assert "@textlint-ja/ai-writing/no-ai-list-formatting" in rules
        assert report.results[0].output is None
        assert report.source == "test"

    def test_project_bunfig_is_not_loaded(
        self, tmp_path: Path, real_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        marker = tmp_path / "marker"
        _ = (tmp_path / "bunfig.toml").write_text('preload = ["./evil.ts"]\n', encoding="utf-8")
        _ = (tmp_path / "evil.ts").write_text(f'await Bun.write("{marker}", "ran");\n', encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        report = lint_documents([Document("a.md", AI_LIKE)], config=load_config(CHAT_CONFIG), env=real_env)
        assert report.results[0].messages
        assert not marker.exists()

    def test_unknown_names_are_ignored(self, real_env: dict[str, str]) -> None:
        config = Config({"rules": {"./evil": True, "/abs/evil.js": True}, "filters": {"../x": True}}, "test")
        report = lint_documents([Document("a.md", AI_LIKE)], config=config, env=real_env)
        assert report.ignored == ("./evil", "/abs/evil.js", "../x")
        assert report.results[0].messages == ()

    def test_fix_text_file(self, real_env: dict[str, str]) -> None:
        config = Config({"rules": {"preset-ja-spacing": {"ja-space-between-half-and-full-width": True}}}, "test")
        report = lint_documents([Document("a.txt", "これは test です。\n")], config=config, env=real_env, fix=True)
        assert report.results[0].output == "これはtestです。\n"
        assert report.results[0].messages == ()


class TestLintDocumentsFailures:
    def test_bun_missing_for_lint(self, tmp_path: Path) -> None:
        with pytest.raises(TextlintError, match="bun が見つかりません"):
            _ = lint_documents([], config=Config({}, "t"), env={"PATH": str(tmp_path)})

    @pytest.mark.parametrize("version", ["1.2.9", "0.9.0", "garbage"])
    def test_bun_too_old(self, tmp_path: Path, version: str) -> None:
        with pytest.raises(TextlintError, match=r"1\.3 以上"):
            _ = lint_documents([], config=Config({}, "t"), env=fake_env(tmp_path, FAKE_BUN_VERSION=version))

    def test_runner_fails(self, tmp_path: Path) -> None:
        with pytest.raises(TextlintError, match="runner failed"):
            _ = lint_documents([], config=Config({}, "t"), env=fake_env(tmp_path, FAKE_RUNNER_CODE="1"))

    def test_runner_prints_garbage(self, tmp_path: Path) -> None:
        with pytest.raises(TextlintError, match="kernel ランナーが失敗しました"):
            _ = lint_documents([], config=Config({}, "t"), env=fake_env(tmp_path, FAKE_RUNNER_STDOUT="not json"))

    def test_runner_returns_non_object(self, tmp_path: Path) -> None:
        with pytest.raises(TextlintError, match="kernel ランナーが失敗しました"):
            _ = lint_documents([], config=Config({}, "t"), env=fake_env(tmp_path, FAKE_RUNNER_STDOUT="[]"))

    def test_malformed_messages_are_dropped(self, tmp_path: Path) -> None:
        good = {"ruleId": "r", "message": "m", "line": 1, "column": 2, "severity": 2}
        response = {
            "ignored": ["x"],
            "results": [
                {"filePath": "a.md", "messages": [good, {**good, "line": "1"}, {**good, "ruleId": 1}, "junk"]},
                "junk",
            ],
        }
        report = lint_documents(
            [Document("a.md", "x")],
            config=Config({}, "t"),
            env=fake_env(tmp_path, FAKE_RUNNER_STDOUT=json.dumps(response)),
        )
        assert [len(result.messages) for result in report.results] == [1, 0]
        assert not report.results[1].file_path
        assert report.ignored == ("x",)
