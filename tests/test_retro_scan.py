"""retro skill 同梱 retro_scan.py の挙動テスト (PR #96 レビュー起点)。

duckdb は本テスト環境に無くてもよいよう、import 前に stub を差し込む
(スクリプトは module import 時に duckdb を要求するが、ここで検証する
discover / render_table は duckdb を使わない)。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "retro" / "scripts" / "retro_scan.py"

sys.modules.setdefault("duckdb", types.ModuleType("duckdb"))
_spec = importlib.util.spec_from_file_location("retro_scan", SCRIPT)
retro_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retro_scan)


def make_args(**overrides):
    # 既定は「何も明示されていない」状態 (argparse の None sentinel と同じ) —
    # validate_flag_combinations は None-vs-explicit を判定に使う
    args = argparse.Namespace(
        transcript=None,
        project_dir=None,
        projects_root=None,
        all_projects=False,
        since=None,
        pr=None,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


class TestMarkdownSanitization(unittest.TestCase):
    """transcript 由来の値が Markdown 表・見出しを壊さない
    (r3683948193 / r3683948146)。"""

    def render(self, rows) -> list[str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            retro_scan.render_table(rows)
        return buf.getvalue().splitlines()

    def test_pipe_in_cell_is_escaped(self) -> None:
        lines = self.render([{"command": "curl a | jq .x", "n": 3}])
        self.assertEqual(lines[2], "| curl a \\| jq .x | 3 |")

    def test_newlines_and_cr_become_spaces(self) -> None:
        lines = self.render([{"command": "line1\nline2\rline3", "n": 1}])
        # 1 データ行のまま (行の分裂なし) で、改行は空白に置換される
        self.assertEqual(len(lines), 3)
        self.assertIn("line1 line2 line3", lines[2])

    def test_ansi_and_control_chars_are_stripped(self) -> None:
        lines = self.render([{"session": "\x1b[31mred\x1b[0m\x07", "n": 1}])
        self.assertEqual(lines[2], "| red | 1 |")

    def test_none_still_renders_empty(self) -> None:
        lines = self.render([{"command": None, "n": 1}])
        self.assertEqual(lines[2], "|  | 1 |")

    def test_sanitize_cell_is_used_for_heading_values(self) -> None:
        self.assertEqual(
            retro_scan.sanitize_cell("2026-01-01\x1b[2Jevil\n# fake"),
            "2026-01-01evil # fake",
        )


class TestDeadFlagValidation(unittest.TestCase):
    """明示フラグが選択された scan 経路で消費されない組合せを、実行前に
    一括で拒否する (2 巡目 code-review Important-1; rules/fail-closed.md)。

    validate_flag_combinations は「明示フラグ集合 − 経路が消費するフラグ集合」
    の差集合が非空なら exit 1 する — 組合せの個別列挙ではなく class ごと
    閉じていることをここで固定する。"""

    def assert_rejected(self, args, *dead_flags: str) -> str:
        with self.assertRaises(SystemExit) as ctx:
            retro_scan.validate_flag_combinations(args)
        msg = str(ctx.exception.code)
        for flag in dead_flags:
            self.assertIn(flag, msg)
        return msg

    def test_standalone_pr_with_project_dir_is_rejected(self) -> None:
        self.assert_rejected(
            make_args(pr=[7], project_dir="/tmp/proj"), "--project-dir")

    def test_standalone_pr_with_projects_root_is_rejected(self) -> None:
        self.assert_rejected(
            make_args(pr=[7], projects_root="/tmp/root"), "--projects-root")

    def test_pr_with_transcript_and_project_dir_is_rejected(self) -> None:
        # --transcript を足しても --project-dir は生きない — 経路単位で dead
        self.assert_rejected(
            make_args(pr=[7], transcript=["/tmp/s.jsonl"],
                      project_dir="/tmp/proj"),
            "--project-dir")

    def test_since_with_transcript_is_rejected(self) -> None:
        # 旧 discover 内の個別チェック (r3683948179) の class 統合先
        self.assert_rejected(
            make_args(transcript=["/tmp/s.jsonl"], since="2026-01-01"),
            "--since")

    def test_all_projects_with_transcript_is_rejected(self) -> None:
        self.assert_rejected(
            make_args(transcript=["/tmp/s.jsonl"], all_projects=True),
            "--all-projects")

    def test_all_projects_with_project_dir_is_rejected(self) -> None:
        self.assert_rejected(
            make_args(all_projects=True, project_dir="/tmp/proj"),
            "--project-dir")

    def test_error_does_not_suggest_dead_alternatives(self) -> None:
        # 旧メッセージは standalone --pr + --project-dir に対し --transcript /
        # --all-projects を提案していたが、どちらの経路でも --project-dir は
        # dead のまま — 生きない選択肢へ誘導しないことを固定する
        msg = self.assert_rejected(
            make_args(pr=[7], project_dir="/tmp/proj"), "--project-dir")
        self.assertNotIn("--transcript", msg)
        self.assertNotIn("--all-projects", msg)

    def test_valid_combinations_pass(self) -> None:
        for args in (
            make_args(),  # 全て default (slug discovery)
            make_args(pr=[7]),  # standalone --pr
            make_args(pr=[7], since="2026-01-01",
                      project_dir="/tmp/proj", projects_root="/tmp/root"),
            make_args(transcript=["/tmp/s.jsonl"]),
            make_args(all_projects=True, projects_root="/tmp/root",
                      since="2026-01-01"),
            make_args(project_dir="/tmp/proj", projects_root="/tmp/root"),
        ):
            retro_scan.validate_flag_combinations(args)  # raise しないこと


class TestTranscriptDiscovery(unittest.TestCase):
    def test_explicit_transcript_is_returned_as_is(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text('{"type":"user"}\n', encoding="utf-8")
            args = make_args(transcript=[str(transcript)])
            self.assertEqual(retro_scan.discover(args), [str(transcript)])


class TestFirstLineSanitization(unittest.TestCase):
    """--pr モードの summary (_first_line) が ANSI/C0 を strip する
    (2 巡目 code-review Minor-2)。"""

    def test_ansi_and_c0_are_stripped(self) -> None:
        self.assertEqual(
            retro_scan._first_line("\x1b[31mred\x1b[0m\x07 title\nsecond line"),
            "red title",
        )

    def test_truncation_applies_after_strip(self) -> None:
        self.assertEqual(
            retro_scan._first_line("\x1b[2J" + "a" * 100),
            "a" * 77 + "...",
        )

    def test_empty_body_is_empty(self) -> None:
        self.assertEqual(retro_scan._first_line(None), "")


class TestJsonPayloadScrub(unittest.TestCase):
    """transcript-scan の --json payload に載る SQL 結果値も control-strip
    される (2 巡目 code-review Minor-1; scrub_value は markdown/--json 分岐の
    手前で適用される)。"""

    def test_string_values_are_control_stripped(self) -> None:
        self.assertEqual(
            retro_scan.scrub_value("\x1b[31mgit\x1b[0m status\r\nnext\x07"),
            "git status next",
        )

    def test_non_strings_pass_through(self) -> None:
        for value in (3, 1.5, True, None):
            self.assertIs(retro_scan.scrub_value(value), value)


class TestSlugCollisionGuard(unittest.TestCase):
    """lossy slug (acme.prod / acme-prod が同一 slug) で別プロジェクトの
    transcript を混ぜない (r3683948151)。"""

    def _write(self, path: Path, record) -> str:
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        return str(path)

    def test_discovery_drops_transcripts_of_colliding_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = str(Path(tmp) / "acme.prod")
            colliding = str(Path(tmp) / "acme-prod")
            root = Path(tmp) / "projects"
            slug_dir = root / retro_scan.project_slug(project)
            slug_dir.mkdir(parents=True)
            # 両プロジェクトの slug が一致することがこのテストの前提
            self.assertEqual(retro_scan.project_slug(project),
                             retro_scan.project_slug(colliding))

            own = self._write(slug_dir / "own.jsonl", {"cwd": project})
            alien = self._write(slug_dir / "alien.jsonl", {"cwd": colliding})
            worktree = self._write(
                slug_dir / "wt.jsonl",
                {"cwd": project + "/.claude/worktrees/x"},
            )
            no_cwd = self._write(slug_dir / "nocwd.jsonl", {"type": "summary"})
            # 実測で cwd が 28 行目に初出する実 transcript が存在する —
            # 検証窓は数十行の前置き (summary 等) を越えて cwd を見つけること
            late = slug_dir / "late.jsonl"
            late.write_text(
                '{"type":"summary"}\n' * 40
                + json.dumps({"cwd": project}) + "\n",
                encoding="utf-8",
            )

            args = make_args(project_dir=project, projects_root=str(root))
            found = retro_scan.discover(args)

            self.assertIn(own, found)
            self.assertIn(worktree, found)  # worktree cwd はこの project の履歴
            self.assertIn(str(late), found)
            self.assertNotIn(alien, found)
            # fail-closed: cwd を特定できないファイルは採用しない (実測で
            # cwd 無しは journal.jsonl 等の非セッションファイルのみ)
            self.assertNotIn(no_cwd, found)


if __name__ == "__main__":
    unittest.main()
