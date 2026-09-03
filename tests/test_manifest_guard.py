"""`.github/scripts/manifest_guard.py` の挙動テスト (stdlib unittest、依存なし)。

検証する不変条件:
- manifest は許可したキーだけで組み直され、未知のキーは落ちる
- 文字列フィールドは長さ・制御文字・トークン様文字列で弾かれる (body だけでなく全部)
- title / body の再検査が `gh pr create` の直前に効く形で提供されている

由来: agent は自分の GH_TOKEN を読めるため、manifest の任意の文字列フィールド
(title 等) にトークンを載せられる。そこが PR のタイトルとして公開される経路を
塞ぐのがこのスクリプトの役割。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "manifest_guard.py"

_spec = importlib.util.spec_from_file_location("manifest_guard", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

TOKEN = "ghs_" + "a" * 20


def run(argv: list[str]) -> tuple[int, str]:
    """CLI を呼び出して (exit code, stderr) を返す。"""
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = guard.main(argv)
    return code, err.getvalue()


def entry(**overrides: object) -> dict:
    base = {
        "branch": "improve/tdd-IMP-20260903-aaaaaaaaaa",
        "head_sha": "a" * 40,
        "body_file": "body-0.md",
        "ledger_id": "IMP-20260903-aaaaaaaaaa",
        "title": "fix(tdd): 停止条件を原則で書き直す",
    }
    base.update(overrides)
    return base


class TestEntryValidation(unittest.TestCase):
    def test_valid_entry_has_no_problems(self) -> None:
        self.assertEqual(guard.entry_problems(entry()), [])

    def test_token_in_any_string_field_is_rejected(self) -> None:
        # body だけでなく title / branch 等どのフィールドでも弾く
        for field in ("title", "body_file", "ledger_id"):
            with self.subTest(field=field):
                problems = guard.entry_problems(entry(**{field: TOKEN}))
                self.assertTrue(
                    any("トークン" in p for p in problems), (field, problems)
                )

    def test_control_characters_and_newlines_are_rejected(self) -> None:
        problems = guard.entry_problems(entry(title="ok\nsecond line"))
        self.assertTrue(any("制御文字" in p for p in problems), problems)

    def test_overlong_title_is_rejected(self) -> None:
        problems = guard.entry_problems(entry(title="あ" * 201))
        self.assertTrue(any("長すぎる" in p for p in problems), problems)

    def test_empty_title_and_bad_shapes_are_rejected(self) -> None:
        self.assertTrue(any("title: 空" in p for p in guard.entry_problems(entry(title="  "))))
        self.assertTrue(
            any("branch" in p for p in guard.entry_problems(entry(branch="main")))
        )
        self.assertTrue(
            any("head_sha" in p for p in guard.entry_problems(entry(head_sha="abc")))
        )
        self.assertTrue(
            any("ledger_id" in p for p in guard.entry_problems(entry(ledger_id="IMP-1")))
        )

    def test_rebuild_drops_unknown_keys_and_normalises_body_file(self) -> None:
        rebuilt = guard.rebuild_entry(
            entry(body_file="../../etc/passwd", extra="紛れ込ませた値")
        )
        self.assertEqual(set(rebuilt), set(guard.ALLOWED_KEYS))
        self.assertNotIn("extra", rebuilt)
        self.assertEqual(rebuilt["body_file"], "passwd")


class TestSanitizeCli(unittest.TestCase):
    def test_sanitize_rewrites_and_drops_unknown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.jsonl"
            dest = Path(tmp) / "out.jsonl"
            src.write_text(
                json.dumps(entry(extra="落とされる")) + "\n", encoding="utf-8"
            )
            code, _ = run(["sanitize", "--src", str(src), "--dest", str(dest)])
            self.assertEqual(code, 0)
            written = json.loads(dest.read_text(encoding="utf-8").strip())
            self.assertEqual(set(written), set(guard.ALLOWED_KEYS))

    def test_sanitize_fails_and_writes_nothing_on_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.jsonl"
            dest = Path(tmp) / "out.jsonl"
            src.write_text(json.dumps(entry(title=TOKEN)) + "\n", encoding="utf-8")
            code, err = run(["sanitize", "--src", str(src), "--dest", str(dest)])
            self.assertEqual(code, 1)
            self.assertIn("トークン", err)
            self.assertFalse(dest.exists())


class TestCheckTextCli(unittest.TestCase):
    def test_title_and_body_are_rechecked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.md"
            body.write_text("普通の本文\n", encoding="utf-8")
            self.assertEqual(
                run(["check-text", "--title", "ok", "--body-file", str(body)])[0], 0
            )
            self.assertEqual(run(["check-text", "--title", TOKEN])[0], 1)
            body.write_text(f"漏れた: {TOKEN}\n", encoding="utf-8")
            code, err = run(["check-text", "--title", "ok", "--body-file", str(body)])
            self.assertEqual(code, 1)
            self.assertIn("body", err)


if __name__ == "__main__":
    unittest.main()
