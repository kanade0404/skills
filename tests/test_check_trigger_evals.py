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


def write_pred_file(
    root: Path, skill: str, filename: str, preds: list[dict[str, Any]]
) -> Path:
    eval_dir = root / "skills" / skill / "evals"
    eval_dir.mkdir(parents=True, exist_ok=True)
    path = eval_dir / filename
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
            ("foo", "t01", "FN"): MOD.KnownFailure(
                skill="foo",
                case_id="t01",
                kind="FN",
                reason="baseline",
                recorded="2026-07-05",
            )
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            failed = MOD.emit_mismatch(mismatch, known)
        self.assertFalse(failed)
        self.assertIn("::warning", out.getvalue())
        self.assertIn("Known trigger mismatch: foo/t01", out.getvalue())

    def test_same_case_with_different_kind_errors(self) -> None:
        mismatch = self._mismatch()
        known = {
            ("foo", "t01", "FP"): MOD.KnownFailure(
                skill="foo",
                case_id="t01",
                kind="FP",
                reason="baseline",
                recorded="2026-07-05",
            )
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            failed = MOD.emit_mismatch(mismatch, known)
        self.assertTrue(failed)
        self.assertIn("::error", out.getvalue())
        self.assertIn("recorded kind と異なる", out.getvalue())

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
        entry = {
            "skill": "foo",
            "case_id": "t01",
            "kind": "FN",
            "reason": "-",
            "recorded": "2026-07-05",
        }
        path = write_ledger(self.root / "ledger.json", [entry, entry])
        with self.assertRaisesRegex(ValueError, "duplicates foo/t01/FN"):
            MOD.load_known_failures(path)

    def test_rejects_unknown_kind(self) -> None:
        path = write_ledger(
            self.root / "ledger.json",
            [
                {
                    "skill": "foo",
                    "case_id": "t01",
                    "kind": "UNKNOWN",
                    "reason": "-",
                    "recorded": "2026-07-05",
                }
            ],
        )
        with self.assertRaisesRegex(ValueError, "kind"):
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

    def test_select_case_files_uses_full_scan_for_harness_changes(self) -> None:
        all_case_files = [
            Path("/repo/skills/bar/evals/bar-trigger.json"),
            Path("/repo/skills/foo/evals/foo-trigger.json"),
        ]
        selected = MOD.select_case_files(
            all_case_files,
            {Path("tests/test_check_trigger_evals.py")},
            "pull_request",
        )
        self.assertEqual(selected, all_case_files)

    def test_select_case_files_limits_pull_request_to_changed_skill(self) -> None:
        all_case_files = [
            Path("/repo/skills/bar/evals/bar-trigger.json"),
            Path("/repo/skills/foo/evals/foo-trigger.json"),
        ]
        selected = MOD.select_case_files(
            all_case_files,
            {Path("skills/foo/evals/foo-trigger-results-2026-07-05.jsonl")},
            "pull_request",
        )
        self.assertEqual(selected, [Path("/repo/skills/foo/evals/foo-trigger.json")])


class TestFrontmatter(unittest.TestCase):
    def test_extracts_frontmatter_block(self) -> None:
        text = "---\nname: foo\ndescription: bar\n---\n# Body\n"
        self.assertEqual(
            MOD.extract_frontmatter_block(text),
            "---\nname: foo\ndescription: bar\n---\n",
        )

    def test_frontmatter_ignores_body_only_changes(self) -> None:
        base = "---\nname: foo\n---\nold body\n"
        head = "---\nname: foo\n---\nnew body\n"
        self.assertFalse(MOD.frontmatter_changed(base, head))

    def test_frontmatter_detects_header_changes(self) -> None:
        base = "---\nname: foo\ndescription: old\n---\nbody\n"
        head = "---\nname: foo\ndescription: new\n---\nbody\n"
        self.assertTrue(MOD.frontmatter_changed(base, head))


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
            [
                {
                    "skill": "foo",
                    "case_id": "t01",
                    "kind": "FN",
                    "reason": "baseline",
                    "recorded": "2026-07-05",
                }
            ],
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
            [
                {
                    "skill": "foo",
                    "case_id": "t01",
                    "kind": "FN",
                    "reason": "fixed",
                    "recorded": "2026-07-05",
                }
            ],
        )
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("Stale known trigger mismatch: foo/t01", out)

    def test_warns_when_ledger_references_unknown_case(self) -> None:
        write_cases(self.root, "foo", [case("t01", True)])
        write_preds(self.root, "foo", [{"id": "t01", "predicted": True}])
        write_ledger(
            self.ledger_path,
            [
                {
                    "skill": "foo",
                    "case_id": "missing",
                    "kind": "FN",
                    "reason": "deleted",
                    "recorded": "2026-07-05",
                }
            ],
        )
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("unknown case を参照している。エントリを削除せよ", out)

    def test_warns_when_ledger_references_unknown_skill_in_full_scan(self) -> None:
        write_cases(self.root, "foo", [case("t01", True)])
        write_preds(self.root, "foo", [{"id": "t01", "predicted": True}])
        write_ledger(
            self.ledger_path,
            [
                {
                    "skill": "bar",
                    "case_id": "t01",
                    "kind": "FN",
                    "reason": "deleted",
                    "recorded": "2026-07-05",
                }
            ],
        )
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("unknown skill を参照している", out)

    def test_stale_check_requires_latest_result_file_to_change(self) -> None:
        write_cases(self.root, "foo", [case("t01", True)])
        write_pred_file(
            self.root,
            "foo",
            "foo-trigger-results-2026-07-04.jsonl",
            [{"id": "t01", "predicted": True}],
        )
        latest = write_preds(self.root, "foo", [{"id": "t01", "predicted": True}])
        self.assertEqual(latest.name, "foo-trigger-results-2026-07-05.jsonl")
        changed = {
            Path("skills/foo/evals/foo-trigger.json"),
            Path("skills/foo/evals/foo-trigger-results-2026-07-04.jsonl"),
        }
        with mock.patch.object(MOD, "changed_paths", return_value=changed):
            with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}):
                code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("update the latest sibling *-trigger-results-*.jsonl file", out)

    def test_skill_md_body_only_change_does_not_require_result_update(self) -> None:
        write_cases(self.root, "foo", [case("t01", True)])
        write_preds(self.root, "foo", [{"id": "t01", "predicted": True}])
        changed = {Path("skills/foo/SKILL.md")}
        with mock.patch.object(MOD, "changed_paths", return_value=changed):
            with mock.patch.object(MOD, "skill_frontmatter_changed", return_value=False):
                with mock.patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}):
                    code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertNotIn("Stale trigger predictions", out)

    def test_missing_results_are_skipped_on_full_scan(self) -> None:
        write_cases(self.root, "foo", [case("t01", True)])
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("Skipped trigger eval", out)


if __name__ == "__main__":
    unittest.main()
