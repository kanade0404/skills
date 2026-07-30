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
    args = argparse.Namespace(
        transcript=None,
        project_dir=str(REPO_ROOT),
        projects_root="~/.claude/projects",
        all_projects=False,
        since=None,
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


class TestSinceWithTranscript(unittest.TestCase):
    """--transcript 併用時に --since が無言で無視されない (r3683948179)。"""

    def test_since_with_transcript_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text('{"type":"user"}\n', encoding="utf-8")
            args = make_args(transcript=[str(transcript)], since="2026-01-01")
            with self.assertRaises(SystemExit) as ctx:
                retro_scan.discover(args)
            self.assertIn("--since", str(ctx.exception.code))
            self.assertIn("--transcript", str(ctx.exception.code))

    def test_transcript_without_since_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "session.jsonl"
            transcript.write_text('{"type":"user"}\n', encoding="utf-8")
            args = make_args(transcript=[str(transcript)])
            self.assertEqual(retro_scan.discover(args), [str(transcript)])


if __name__ == "__main__":
    unittest.main()
