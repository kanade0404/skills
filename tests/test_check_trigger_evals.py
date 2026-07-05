"""Tests for .github/scripts/check_trigger_evals.py (stdlib unittest, 依存なし).

スクリプトはパッケージではないため importlib でファイルパスからロードする。
統合テストは一時ディレクトリに repo レイアウトを構築し、モジュールの
ROOT / KNOWN_FAILURES_PATH を patch する (SCORER は実スクリプトのまま)。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "scripts" / "check_trigger_evals.py"
)


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("check_trigger_evals", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass のアノテーション解決が sys.modules 経由でモジュールを引くため登録が必須
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MOD = _load_module()


def write_cases(root: Path, skill: str, cases: list[dict[str, Any]]) -> Path:
    eval_dir = root / "skills" / skill / "evals"
    eval_dir.mkdir(parents=True, exist_ok=True)
    path = eval_dir / f"{skill}-trigger.json"
    path.write_text(
        json.dumps({"target_skill": skill, "version": "1", "cases": cases}),
        encoding="utf-8",
    )
    return path


def write_preds(root: Path, skill: str, preds: list[dict[str, Any]]) -> Path:
    eval_dir = root / "skills" / skill / "evals"
    eval_dir.mkdir(parents=True, exist_ok=True)
    path = eval_dir / f"{skill}-trigger-results-2026-07-05.jsonl"
    path.write_text(
        "\n".join(json.dumps(pred) for pred in preds) + "\n",
        encoding="utf-8",
    )
    return path


def write_ledger(path: Path, entries: list[dict[str, str]]) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def case(case_id: str, should_trigger: bool) -> dict[str, Any]:
    return {
        "id": case_id,
        "prompt": f"prompt-{case_id}",
        "should_trigger": should_trigger,
        "rationale": "-",
        "tags": ["explicit"],
    }


class RepoTestCase(unittest.TestCase):
    """一時 repo レイアウト + モジュール globals の patch を共通化する基底クラス。"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.ledger_path = write_ledger(self.root / "known-failures.json", [])
        for name, value in (("ROOT", self.root), ("KNOWN_FAILURES_PATH", self.ledger_path)):
            patcher = mock.patch.object(MOD, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env_patcher = mock.patch.dict(os.environ)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop("GITHUB_EVENT_NAME", None)

    def run_main(self) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = MOD.main()
        return code, out.getvalue()


class TestFindMismatches(RepoTestCase):
    def test_classifies_fn_fp_missing_and_ignores_matches(self) -> None:
        cases_path = write_cases(
            self.root,
            "foo",
            [case("t01", True), case("t02", False), case("t03", True), case("t04", True)],
        )
        preds_path = write_preds(
            self.root,
            "foo",
            [
                {"id": "t01", "predicted": False},  # FN
                {"id": "t02", "predicted": True},  # FP
                # t03 は予測なし (MISSING)
                {"id": "t04", "predicted": True},  # 一致
            ],
        )
        kinds = {m.case_id: m.kind for m in MOD.find_mismatches(cases_path, preds_path)}
        self.assertEqual(kinds, {"t01": "FN", "t02": "FP", "t03": "MISSING"})


class TestEmitMismatch(RepoTestCase):
    def _mismatch(self) -> Any:
        cases_path = write_cases(self.root, "foo", [case("t01", True)])
        preds_path = write_preds(self.root, "foo", [{"id": "t01", "predicted": False}])
        (mismatch,) = MOD.find_mismatches(cases_path, preds_path)
        return mismatch

    def test_known_mismatch_warns_and_does_not_fail(self) -> None:
        mismatch = self._mismatch()
        known = {
            ("foo", "t01"): MOD.KnownFailure(
                skill="foo", case_id="t01", reason="baseline", recorded="2026-07-05"
            )
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            failed = MOD.emit_mismatch(mismatch, known)
        self.assertFalse(failed)
        self.assertIn("::warning", out.getvalue())
        self.assertIn("Known trigger mismatch: foo/t01", out.getvalue())

    def test_new_mismatch_errors_and_fails(self) -> None:
        mismatch = self._mismatch()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            failed = MOD.emit_mismatch(mismatch, {})
        self.assertTrue(failed)
        self.assertIn("::error", out.getvalue())
        self.assertIn("New trigger mismatch: foo/t01", out.getvalue())


class TestLedgerLoading(RepoTestCase):
    def test_rejects_duplicate_entries(self) -> None:
        entry = {"skill": "foo", "case_id": "t01", "reason": "-", "recorded": "2026-07-05"}
        path = write_ledger(self.root / "ledger.json", [entry, entry])
        with self.assertRaisesRegex(ValueError, "duplicates foo/t01"):
            MOD.load_known_failures(path)

    def test_rejects_non_list(self) -> None:
        path = self.root / "ledger.json"
        path.write_text(json.dumps({}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "JSON array"):
            MOD.load_known_failures(path)


class TestPathClassification(unittest.TestCase):
    def test_skill_md_counts_as_trigger_input(self) -> None:
        self.assertTrue(MOD.is_trigger_input(Path("skills/foo/SKILL.md")))
        self.assertTrue(MOD.is_trigger_input(Path("skills/foo/evals/foo-trigger.json")))
        self.assertFalse(MOD.is_trigger_input(Path("skills/foo/references/notes.md")))

    def test_result_files_are_not_trigger_inputs(self) -> None:
        result = Path("skills/foo/evals/foo-trigger-results-2026-07-05.jsonl")
        self.assertTrue(MOD.is_trigger_result(result))
        self.assertFalse(MOD.is_trigger_input(result))

    def test_skill_md_change_requires_result_update(self) -> None:
        # 元実装で SKILL.md 変更が判定から漏れていた regression ガード
        self.assertTrue(
            MOD.input_changed_for_eval(
                {Path("skills/foo/SKILL.md")},
                "foo",
                Path("skills/foo/evals/foo-trigger.json"),
                Path("skills/foo/evals"),
            )
        )


class TestMainIntegration(RepoTestCase):
    def test_green_when_all_match(self) -> None:
        write_cases(self.root, "foo", [case("t01", True)])
        write_preds(self.root, "foo", [{"id": "t01", "predicted": True}])
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertNotIn("::error", out)

    def test_fails_on_new_mismatch_with_case_id(self) -> None:
        write_cases(self.root, "foo", [case("t01", True)])
        write_preds(self.root, "foo", [{"id": "t01", "predicted": False}])
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("New trigger mismatch: foo/t01", out)

    def test_known_mismatch_stays_green(self) -> None:
        write_cases(self.root, "foo", [case("t01", True)])
        write_preds(self.root, "foo", [{"id": "t01", "predicted": False}])
        write_ledger(
            self.ledger_path,
            [{"skill": "foo", "case_id": "t01", "reason": "baseline", "recorded": "2026-07-05"}],
        )
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("Known trigger mismatch: foo/t01", out)
        self.assertNotIn("::error", out)

    def test_stale_ledger_entry_warns_but_stays_green(self) -> None:
        write_cases(self.root, "foo", [case("t01", True)])
        write_preds(self.root, "foo", [{"id": "t01", "predicted": True}])
        write_ledger(
            self.ledger_path,
            [{"skill": "foo", "case_id": "t01", "reason": "fixed", "recorded": "2026-07-05"}],
        )
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("Stale known trigger mismatch: foo/t01", out)

    def test_missing_results_are_skipped_on_full_scan(self) -> None:
        write_cases(self.root, "foo", [case("t01", True)])
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("Skipped trigger eval", out)


if __name__ == "__main__":
    unittest.main()
