"""`.github/scripts/manifest_guard.py` の挙動テスト (stdlib unittest、依存なし)。

検証する不変条件:
- manifest は許可したキーだけで組み直され、未知のキーは落ちる
- 文字列フィールドは長さ・制御文字・トークン様文字列で弾かれる (body だけでなく全部)
- title / body の再検査が `gh pr create` の直前に効く形で提供されている
- 候補ブランチの差分と PR 本文が、この job の見えるシークレットの実値
  (literal / base64 / hex / 改行を挟んだ PEM) で走査され、検出時に**値そのものを
  出力しない**

由来: agent は自分の GH_TOKEN を読めるため、manifest の任意の文字列フィールド
(title 等) にトークンを載せられる。そこが PR のタイトルとして公開される経路を
塞ぐのがこのスクリプトの役割。
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import importlib.util
import io
import json
import os
import subprocess
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


class TestDuplicateRejection(unittest.TestCase):
    """manifest 全体での一意性 (行ごとの検査では見えない重複)。

    publish は行ごとに `gh pr create` と `link-pr` を回すため、重複を通すと
    1 つの finding に PR が 2 本立ち、台帳の同じ行に 2 度 link-pr が走る。
    """

    def sanitize(self, entries: list[dict]) -> tuple[int, str, bool]:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.jsonl"
            dest = Path(tmp) / "out.jsonl"
            src.write_text(
                "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
            )
            code, err = run(["sanitize", "--src", str(src), "--dest", str(dest)])
            return code, err, dest.exists()

    def test_duplicate_branch_rejects_the_whole_manifest(self) -> None:
        code, err, wrote = self.sanitize(
            [
                entry(),
                entry(
                    ledger_id="IMP-20260903-bbbbbbbbbb",
                    body_file="body-1.md",
                    title="別の title",
                ),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("branch", err)
        self.assertFalse(wrote)

    def test_duplicate_ledger_id_rejects_the_whole_manifest(self) -> None:
        code, err, wrote = self.sanitize(
            [
                entry(),
                entry(branch="improve/tdd-second", body_file="body-1.md"),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("ledger_id", err)
        self.assertFalse(wrote)

    def test_empty_ledger_ids_are_exempt_from_uniqueness(self) -> None:
        # 突き合わせ (reconcile) 行は台帳の特定の行を指さないので複数あってよい
        code, err, wrote = self.sanitize(
            [
                entry(ledger_id="", branch="improve/reconcile-a"),
                entry(ledger_id="", branch="improve/reconcile-b", body_file="body-1.md"),
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertTrue(wrote)


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


# 実在しない形の偽トークン。テストの中でだけ使い、リポジトリの外には出さない。
FAKE_OAUTH = "sk-ant-oat01-" + "z9" * 24
FAKE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    + "\n".join("Zm9vYmFy" + str(i) + "K" * 55 for i in range(4))
    + "\n-----END RSA PRIVATE KEY-----\n"
)


def scan_env(**values: str) -> dict[str, str]:
    """走査対象のシークレットを環境変数として渡す形に組み立てる。"""
    env = dict(os.environ)
    env.update(values)
    return env


@contextlib.contextmanager
def patched_environ(**values: str):
    """os.environ を一時的に差し替える (CLI は環境変数から値を読む)。"""
    saved = dict(os.environ)
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


class TestSecretNeedles(unittest.TestCase):
    def test_literal_base64_and_hex_are_all_searched(self) -> None:
        labels = [label for label, _ in guard.secret_needles("TOK", FAKE_OAUTH)]
        self.assertIn("TOK (literal)", labels)
        self.assertIn("TOK (base64)", labels)
        self.assertIn("TOK (hex)", labels)

    def test_short_values_are_not_searched(self) -> None:
        # 短い値 (App ID のような数字列) を needle にすると誤検知にしかならない
        self.assertEqual(guard.secret_needles("APP_ID", "12345"), [])

    def test_multiline_value_also_yields_line_and_stripped_needles(self) -> None:
        labels = [label for label, _ in guard.secret_needles("KEY", FAKE_PEM)]
        self.assertTrue(any(label.startswith("KEY (line ") for label in labels))
        self.assertTrue(any("whitespace-stripped" in label for label in labels))


class TestScanBlob(unittest.TestCase):
    def needles(self) -> list:
        return guard.secret_needles("TOK", FAKE_OAUTH)

    def test_literal_hit(self) -> None:
        blob = f"notes: {FAKE_OAUTH}\n".encode()
        self.assertTrue(guard.scan_blob(blob, self.needles()))

    def test_base64_hit(self) -> None:
        blob = base64.b64encode(FAKE_OAUTH.encode())
        self.assertTrue(guard.scan_blob(blob, self.needles()))

    def test_hex_hit(self) -> None:
        blob = binascii.hexlify(FAKE_OAUTH.encode()).upper()
        self.assertTrue(guard.scan_blob(blob, self.needles()))

    def test_value_split_by_newlines_is_still_found(self) -> None:
        # 折り返して埋め込むだけで一致を外せてはいけない
        wrapped = "\n".join(FAKE_OAUTH[i : i + 8] for i in range(0, len(FAKE_OAUTH), 8))
        self.assertTrue(guard.scan_blob(wrapped.encode(), self.needles()))

    def test_pem_lines_are_found_without_the_header(self) -> None:
        needles = guard.secret_needles("KEY", FAKE_PEM)
        body = FAKE_PEM.splitlines()[1]
        self.assertTrue(guard.scan_blob(f"x = {body}\n".encode(), needles))

    def test_prefix_patterns_catch_unknown_credentials(self) -> None:
        # この job が値を知らない資格情報も接頭辞で拾う
        for sample in (
            "sk-ant-api03-" + "a" * 40,
            "ghs_" + "b" * 36,
            "github_pat_" + "c" * 40,
            "-----BEGIN OPENSSH PRIVATE KEY-----",
        ):
            with self.subTest(sample=sample[:12]):
                self.assertTrue(guard.scan_blob(sample.encode(), []))

    def test_clean_blob_has_no_reason(self) -> None:
        clean = "# tdd\n\n普通の本文\n".encode()
        self.assertEqual(guard.scan_blob(clean, self.needles()), [])


class TestNeedlesFromEnv(unittest.TestCase):
    def test_values_come_from_env_not_argv(self) -> None:
        needles, notes = guard.needles_from_env(["A"], {"A": FAKE_OAUTH})
        self.assertTrue(needles)
        self.assertEqual(notes, [])

    def test_empty_env_is_noted_not_silently_skipped(self) -> None:
        needles, notes = guard.needles_from_env(["A"], {})
        self.assertEqual(needles, [])
        self.assertTrue(any("走査しない" in note for note in notes))


class TestScanFilesCli(unittest.TestCase):
    def test_hit_reports_path_and_never_the_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.md"
            body.write_text(f"leak: {FAKE_OAUTH}\n", encoding="utf-8")
            with patched_environ(SCAN_TOK=FAKE_OAUTH):
                code, err = run(["scan-files", "--secret-env", "SCAN_TOK", str(body)])
        self.assertEqual(code, 1)
        self.assertIn("body.md", err)
        self.assertNotIn(FAKE_OAUTH, err)

    def test_clean_file_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.md"
            body.write_text("普通の本文\n", encoding="utf-8")
            with patched_environ(SCAN_TOK=FAKE_OAUTH):
                code, err = run(["scan-files", "--secret-env", "SCAN_TOK", str(body)])
        self.assertEqual(code, 0, err)

    def test_no_usable_secret_fails_closed(self) -> None:
        # 走査対象が 1 つも組み立てられないなら「走査した」ことにはできない
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.md"
            body.write_text("ok\n", encoding="utf-8")
            with patched_environ(SCAN_TOK=""):
                code, err = run(["scan-files", "--secret-env", "SCAN_TOK", str(body)])
        self.assertEqual(code, 1)
        self.assertIn("走査", err)


class TestScanDiffCli(unittest.TestCase):
    """実際の git リポジトリを作って base...head の差分を走査する。"""

    def git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-C", self.repo, *args],
            check=True,
            capture_output=True,
            env=scan_env(
                GIT_AUTHOR_NAME="t",
                GIT_AUTHOR_EMAIL="t@example.com",
                GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@example.com",
            ),
        )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self.git("init", "-q", "-b", "main")
        (Path(self.repo) / "README.md").write_text("base\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "base")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def commit_branch(self, name: str, path: str, content: bytes) -> None:
        self.git("switch", "-q", "-c", name)
        target = Path(self.repo) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self.git("add", path)
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", name)

    def scan(self, head: str) -> tuple[int, str]:
        with patched_environ(SCAN_TOK=FAKE_OAUTH, SCAN_KEY=FAKE_PEM):
            return run(
                [
                    "scan-diff",
                    "--repo",
                    self.repo,
                    "--base",
                    "main",
                    "--head",
                    head,
                    "--secret-env",
                    "SCAN_TOK",
                    "--secret-env",
                    "SCAN_KEY",
                ]
            )

    def test_clean_branch_passes(self) -> None:
        self.commit_branch("clean", "skills/tdd/SKILL.md", "普通の改善\n".encode())
        code, err = self.scan("clean")
        self.assertEqual(code, 0, err)

    def test_literal_secret_in_a_new_file_is_caught(self) -> None:
        self.commit_branch(
            "leak", "skills/tdd/notes.md", f"tok={FAKE_OAUTH}\n".encode()
        )
        code, err = self.scan("leak")
        self.assertEqual(code, 1)
        self.assertIn("skills/tdd/notes.md", err)
        self.assertNotIn(FAKE_OAUTH, err)

    def test_base64_and_hex_encodings_are_caught(self) -> None:
        for name, blob in (
            ("b64", base64.b64encode(FAKE_OAUTH.encode())),
            ("hex", binascii.hexlify(FAKE_OAUTH.encode())),
        ):
            with self.subTest(name=name):
                self.git("switch", "-q", "main")
                self.commit_branch(name, f"skills/tdd/{name}.txt", blob)
                code, err = self.scan(name)
                self.assertEqual(code, 1)
                self.assertNotIn(FAKE_OAUTH, err)

    def test_pem_key_is_caught(self) -> None:
        self.commit_branch("pem", "skills/tdd/key.pem", FAKE_PEM.encode())
        code, err = self.scan("pem")
        self.assertEqual(code, 1)
        self.assertIn("skills/tdd/key.pem", err)
        self.assertNotIn(FAKE_PEM.splitlines()[1], err)

    def test_binary_file_content_is_scanned_as_bytes(self) -> None:
        # 差分に中身が出ないバイナリでも head 側の内容を丸ごと見る
        blob = b"\x00\x01" + FAKE_OAUTH.encode() + b"\x00"
        self.commit_branch("bin", "skills/tdd/blob.bin", blob)
        code, err = self.scan("bin")
        self.assertEqual(code, 1)
        self.assertIn("skills/tdd/blob.bin", err)
        self.assertNotIn(FAKE_OAUTH, err)

    def test_unreadable_head_fails_closed(self) -> None:
        code, err = self.scan("does-not-exist")
        self.assertEqual(code, 1)
        self.assertIn("差分を読めなかった", err)


if __name__ == "__main__":
    unittest.main()
