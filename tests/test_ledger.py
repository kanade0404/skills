"""skill-improver 同梱 ledger.py の挙動テスト。

検証する不変条件:
- メタスキルは exit 2 で改善対象外と判定される (ハードコードされた除外リスト)。
  判定は表記揺れ (`Retro` / `skills/retro`) を跨ぎ、書き込み系サブコマンド全経路で効く
- 未知 skill / skill ディレクトリ解決不能は fail-closed (exit 1)
- 指標の delta 判定は指標ごとの「良い向き」に従う (trigger_f1 だけ大きいほど良い)
- after が before より悪化したエントリは revert candidate として検出される
- 同一 target_skill × 同一 finding クラスで recurrence が加算される
- 除外リストは script と SKILL.md / improvements/README.md で一致する

由来: skills/skill-improver/SKILL.md の Step 2 ゲートと Step 6 の revert 判断が
この script の分類 / delta 判定に依存しているため、純関数として固定する。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "skill-improver" / "scripts" / "ledger.py"

_spec = importlib.util.spec_from_file_location("skill_improver_ledger", SCRIPT)
ledger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ledger)


def run(argv: list[str]) -> tuple[int, str]:
    """CLI を呼び出して (exit code, stdout) を返す。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        code = ledger.main(argv)
    return code, buf.getvalue()


class TestClassifyTarget(unittest.TestCase):
    def test_meta_skills_are_excluded_regardless_of_known_skills(self) -> None:
        # メタ判定は skill ディレクトリの解決結果に依存しない (不変条件)
        for skill in sorted(ledger.META_SKILLS):
            with self.subTest(skill=skill):
                self.assertEqual(ledger.classify_target(skill, None), "excluded_meta")
                self.assertEqual(ledger.classify_target(skill, {skill}), "excluded_meta")

    def test_known_skill_match_uses_the_same_normalization_as_meta(self) -> None:
        # メタ側だけ正規化すると skills/tdd が unknown に落ち、正規化前の文字列が
        # 台帳に入って recurrence / report --skill の突き合わせが割れる
        for spelling in ("tdd", "skills/tdd", " tdd ", ".claude/skills/tdd"):
            with self.subTest(spelling=spelling):
                self.assertEqual(ledger.classify_target(spelling, {"tdd"}), "ok")
        self.assertEqual(ledger.classify_target("nope", {"tdd"}), "unknown")

    def test_exclusion_list_matches_documented_set(self) -> None:
        self.assertEqual(
            ledger.META_SKILLS,
            frozenset(
                {
                    "retro",
                    "session-retro",
                    "skill-builder",
                    "empirical-prompt-tuning",
                    "skill-improver",
                    "model-policy",
                    "harness-distribution",
                    "rulesync-sync",
                }
            ),
        )

    def test_meta_detection_survives_case_and_path_spelling(self) -> None:
        # 表記揺れで除外をすり抜けられると除外リストが飾りになる
        for spelling in ("Retro", "skills/retro", " RETRO ", "skills/retro/"):
            with self.subTest(spelling=spelling):
                self.assertEqual(
                    ledger.classify_target(spelling, {"retro"}), "excluded_meta"
                )

    def test_known_skill_is_ok(self) -> None:
        self.assertEqual(ledger.classify_target("tdd", {"tdd", "commit"}), "ok")

    def test_unknown_skill(self) -> None:
        self.assertEqual(ledger.classify_target("nope", {"tdd"}), "unknown")

    def test_unresolved_when_no_skills_dir(self) -> None:
        # ディレクトリを解決できないときは「対象外」と黙って扱わず unresolved
        self.assertEqual(ledger.classify_target("tdd", None), "unresolved")


class TestMetricDeltas(unittest.TestCase):
    def test_trigger_f1_higher_is_better(self) -> None:
        rows = ledger.metric_deltas({"trigger_f1": 0.7}, {"trigger_f1": 0.9})
        self.assertEqual(rows[0]["verdict"], "improved")

    def test_trigger_f1_drop_is_worse(self) -> None:
        rows = ledger.metric_deltas({"trigger_f1": 0.9}, {"trigger_f1": 0.7})
        self.assertEqual(rows[0]["verdict"], "worse")

    def test_count_metrics_lower_is_better(self) -> None:
        rows = ledger.metric_deltas(
            {"ci_fix_iterations": 6}, {"ci_fix_iterations": 3}
        )
        self.assertEqual(rows[0]["verdict"], "improved")
        rows = ledger.metric_deltas({"review_cycles": 2}, {"review_cycles": 5})
        self.assertEqual(rows[0]["verdict"], "worse")

    def test_unchanged(self) -> None:
        rows = ledger.metric_deltas({"escalations": 1}, {"escalations": 1})
        self.assertEqual(rows[0]["verdict"], "unchanged")

    def test_missing_phase_is_skipped_not_zero_filled(self) -> None:
        # 片側しか無い指標を 0 埋めすると偽の改善が出るため、比較対象から外す
        self.assertEqual(ledger.metric_deltas({"trigger_f1": 0.8}, {}), [])
        self.assertEqual(ledger.metric_deltas(None, {"trigger_f1": 0.8}), [])

    def test_non_numeric_values_are_ignored(self) -> None:
        self.assertEqual(
            ledger.metric_deltas({"trigger_f1": "n/a"}, {"trigger_f1": 0.8}), []
        )

    def test_bool_is_not_a_metric_value(self) -> None:
        # bool は int の派生だが指標値ではない (True - False = 1 の偽 delta を防ぐ)
        self.assertEqual(
            ledger.metric_deltas({"escalations": False}, {"escalations": True}), []
        )


class TestRevertCandidates(unittest.TestCase):
    def test_any_worse_metric_flags_revert(self) -> None:
        entry = {
            "before": {"trigger_f1": 0.7, "review_cycles": 2},
            "after": {"trigger_f1": 0.9, "review_cycles": 5},
        }
        self.assertEqual(ledger.worse_metrics(entry), ["review_cycles"])
        self.assertTrue(ledger.is_revert_candidate(entry))

    def test_all_improved_is_not_revert(self) -> None:
        entry = {"before": {"trigger_f1": 0.7}, "after": {"trigger_f1": 0.9}}
        self.assertFalse(ledger.is_revert_candidate(entry))

    def test_no_after_is_not_revert(self) -> None:
        self.assertFalse(
            ledger.is_revert_candidate({"before": {"trigger_f1": 0.7}, "after": {}})
        )

    def test_only_merged_entries_are_reported_as_revert_candidates(self) -> None:
        regressed = {"before": {"trigger_f1": 0.9}, "after": {"trigger_f1": 0.7}}
        entries = [
            {"id": "IMP-20260903-aaaaaaaaaa", "status": "merged", **regressed},
            # 既に取り消した / 却下した差分を毎回 revert 候補に出し続けない
            {"id": "IMP-20260903-bbbbbbbbbb", "status": "reverted", **regressed},
            {"id": "IMP-20260903-cccccccccc", "status": "rejected", **regressed},
            {"id": "IMP-20260903-dddddddddd", "status": "pr_open", **regressed},
        ]
        report = ledger.build_report(entries)
        self.assertEqual(
            [item["id"] for item in report["revert_candidates"]], ["IMP-20260903-aaaaaaaaaa"]
        )
        # delta 自体は status を問わず出す (観測結果は隠さない)
        self.assertEqual(len(report["deltas"]), 4)


class TestRecurrence(unittest.TestCase):
    def test_same_skill_and_finding_class_increments(self) -> None:
        entries = [
            {"target_skill": "tdd", "finding": "再実行手順が曖昧"},
            {"target_skill": "commit", "finding": "再実行手順が曖昧"},
        ]
        self.assertEqual(ledger.recurrence_for(entries, "tdd", "再実行手順が曖昧!"), 2)
        self.assertEqual(ledger.recurrence_for(entries, "tdd", "別のこと"), 1)

    def test_finding_class_absorbs_spacing_case_and_punctuation(self) -> None:
        self.assertEqual(
            ledger.finding_class("3 連続失敗の停止条件。"),
            ledger.finding_class("3連続失敗の停止条件"),
        )
        self.assertEqual(
            ledger.finding_class("Retry loop UNBOUNDED."),
            ledger.finding_class("retry  loop unbounded"),
        )

    def test_finding_class_does_not_absorb_reordering(self) -> None:
        # 文書化した挙動: 吸収するのは表記揺れだけで、語順や言い回しの違いは別クラス。
        # 同じクラスとして数えたいときは add --class で明示する
        self.assertNotEqual(
            ledger.finding_class("停止条件が曖昧で 3 連続失敗した"),
            ledger.finding_class("3 連続失敗した。停止条件が曖昧"),
        )

    def test_explicit_class_key_overrides_text(self) -> None:
        entries = [
            {
                "target_skill": "ci-self-heal",
                "finding": "停止条件が曖昧",
                "finding_class": "stop-condition",
            }
        ]
        # 本文が全く違っても、明示クラスが同じなら再発として数える
        self.assertEqual(
            ledger.recurrence_for(
                entries, "ci-self-heal", "別エラーで再試行が止まらない", "stop-condition"
            ),
            2,
        )
        self.assertEqual(
            ledger.recurrence_for(entries, "ci-self-heal", "別エラーで再試行が止まらない"),
            1,
        )

    def test_derive_id_is_content_addressed(self) -> None:
        # 台帳の既存行に依存しない = 別ブランチで並行に採番しても衝突しない
        first = ledger.derive_id("tdd", "stop-condition", "2026-09-03")
        self.assertEqual(first, ledger.derive_id("tdd", "stop-condition", "2026-09-03"))
        self.assertIsNotNone(ledger.ID_RE.match(first))
        self.assertTrue(first.startswith("IMP-20260903-"))
        # 材料が 1 つでも違えば別 id
        self.assertNotEqual(first, ledger.derive_id("commit", "stop-condition", "2026-09-03"))
        self.assertNotEqual(first, ledger.derive_id("tdd", "other-class", "2026-09-03"))
        self.assertNotEqual(first, ledger.derive_id("tdd", "stop-condition", "2026-09-10"))

    def test_derive_id_rejects_malformed_created(self) -> None:
        # 形式違反も、形式は合っていて実在しない日付も通さない
        for created in ("2026/09/03", "20260903", "2026-99-99", "2026-02-30"):
            with self.subTest(created=created), self.assertRaises(ValueError):
                ledger.derive_id("tdd", "c", created)

    def test_summary_recounts_recurrence_and_uses_stored_only_as_tiebreak(self) -> None:
        entries = [
            {"target_skill": "tdd", "finding": "f", "recurrence": 1},
            {"target_skill": "tdd", "finding": "f", "recurrence": 1},  # 古い保存値
        ]
        # 保存値 (1) ではなく数え直した 2 を採る
        self.assertEqual(ledger.recurrence_summary(entries)["tdd"]["max_recurrence"], 2)
        # 台帳を剪定して 1 件しか残っていなくても、保存値が過去の再発を伝える
        pruned = [{"target_skill": "tdd", "finding": "f", "recurrence": 5}]
        self.assertEqual(ledger.recurrence_summary(pruned)["tdd"]["max_recurrence"], 5)



class TestParseMetric(unittest.TestCase):
    def test_int_and_float(self) -> None:
        self.assertEqual(ledger.parse_metric("review_cycles=3"), ("review_cycles", 3))
        self.assertEqual(ledger.parse_metric("trigger_f1=0.82"), ("trigger_f1", 0.82))

    def test_unknown_key_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ledger.parse_metric("bogus=1")

    def test_non_numeric_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ledger.parse_metric("review_cycles=many")

    def test_missing_value_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ledger.parse_metric("review_cycles")

    def test_non_finite_rejected(self) -> None:
        # float() は通すが、台帳に入ると delta 比較が全て False に倒れて悪化を見逃す
        for raw in ("nan", "NaN", "inf", "-inf", "Infinity"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ledger.parse_metric(f"trigger_f1={raw}")


class TestNewEntryShape(unittest.TestCase):
    def test_entry_has_documented_fields(self) -> None:
        entry = ledger.new_entry(
            entry_id="IMP-0001",
            created="2026-09-03",
            source="retro",
            evidence=["session_1"],
            target_skill="tdd",
            finding="f",
            lever="skill-edit",
            status="proposed",
            recurrence=1,
        )
        self.assertEqual(
            list(entry),
            [
                "id",
                "created",
                "source",
                "evidence",
                "target_skill",
                "finding",
                "finding_class",
                "lever",
                "status",
                "pr",
                "before",
                "after",
                "recurrence",
                "notes",
            ],
        )


class TestCliRoundTrip(unittest.TestCase):
    """CLI 経由の add → link-pr → record-metrics → report を通しで検証する。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".git").mkdir()
        (self.root / "skills" / "tdd").mkdir(parents=True)
        (self.root / "skills" / "tdd" / "SKILL.md").write_text("x", encoding="utf-8")
        (self.root / "skills" / "retro").mkdir(parents=True)
        (self.root / "skills" / "retro" / "SKILL.md").write_text("x", encoding="utf-8")
        self.ledger_path = self.root / "improvements" / "ledger.jsonl"
        self._cwd = mock.patch.object(ledger.Path, "cwd", staticmethod(lambda: self.root))
        self._cwd.start()
        self.addCleanup(self._cwd.stop)
        self.addCleanup(self._tmp.cleanup)

    def base(self, *args: str) -> list[str]:
        return ["--ledger", str(self.ledger_path), *args]

    def entries(self) -> list[dict]:
        return ledger.load_entries(self.ledger_path)

    def only_id(self) -> str:
        """台帳の先頭エントリの id (内容由来なのでテスト側で決め打ちしない)。"""
        return str(self.entries()[0]["id"])

    def test_add_link_metrics_report(self) -> None:
        code, out = run(
            self.base(
                "add",
                "--source",
                "retro",
                "--target",
                "tdd",
                "--finding",
                "再実行手順が曖昧",
                "--lever",
                "skill-edit",
                "--evidence",
                "session_1",
                "--created",
                "2026-09-03",
            )
        )
        self.assertEqual(code, 0)
        self.assertIn("classification: ok", out)
        entry = self.entries()[0]
        entry_id = entry["id"]
        self.assertEqual(
            entry_id,
            ledger.derive_id("tdd", ledger.finding_class("再実行手順が曖昧"), "2026-09-03"),
        )
        self.assertEqual(entry["status"], "proposed")
        self.assertEqual(entry["recurrence"], 1)

        self.assertEqual(
            run(self.base("link-pr", "--id", entry_id, "--pr", "https://x/1"))[0], 0
        )
        self.assertEqual(self.entries()[0]["status"], "pr_open")

        run(
            self.base(
                "record-metrics", "--id", entry_id, "--phase", "before",
                "--metric", "trigger_f1=0.70",
            )
        )
        run(
            self.base(
                "record-metrics", "--id", entry_id, "--phase", "after",
                "--metric", "trigger_f1=0.60",
            )
        )
        # revert candidate は merged のものだけ — pr_open のうちは並ばない
        code, out = run(self.base("report"))
        self.assertEqual(code, 0)
        self.assertIn(entry_id, out)
        self.assertIn("worse", out)
        self.assertEqual(run(self.base("report", "--fail-on-revert"))[0], ledger.EXIT_OK)

        run(self.base("set-status", "--id", entry_id, "--status", "merged"))

        code, _ = run(self.base("report", "--fail-on-revert"))
        self.assertEqual(code, ledger.EXIT_FAIL)

    def test_report_json_is_machine_readable(self) -> None:
        run(
            self.base(
                "add", "--source", "trigger-eval", "--target", "tdd",
                "--finding", "trigger F1 低下", "--lever", "trigger",
            )
        )
        code, out = run(self.base("report", "--json"))
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["entries"], 1)
        self.assertEqual(report["per_skill"]["tdd"]["entries"], 1)

    def test_add_meta_skill_is_recorded_as_excluded_meta(self) -> None:
        code, out = run(
            self.base(
                "add", "--source", "retro", "--target", "retro",
                "--finding", "retro を直したい", "--lever", "skill-edit",
                "--status", "proposed",
            )
        )
        # 記録はするが、呼出側が「対象として通った」と読まないよう exit は 2
        self.assertEqual(code, ledger.EXIT_META)
        self.assertIn("classification: excluded_meta", out)
        # --status proposed を渡しても excluded_meta で上書きされる
        self.assertEqual(self.entries()[0]["status"], "excluded_meta")

    def test_add_meta_skill_spelled_as_path_is_not_bypassed(self) -> None:
        # --allow-unknown-target はメタ判定より後ろで効くので除外を外せない
        code, out = run(
            self.base(
                "add", "--source", "retro", "--target", "skills/Retro",
                "--finding", "f", "--lever", "skill-edit",
                "--allow-unknown-target",
            )
        )
        self.assertEqual(code, ledger.EXIT_META)
        self.assertIn("classification: excluded_meta", out)
        self.assertEqual(self.entries()[0]["status"], "excluded_meta")

    def test_write_paths_refuse_meta_entries(self) -> None:
        run(
            self.base(
                "add", "--source", "retro", "--target", "retro",
                "--finding", "retro を直したい", "--lever", "skill-edit",
            )
        )
        # 除外は add だけでなく全書き込み経路で効く (後から台帳を進められない)
        entry_id = self.only_id()
        for argv in (
            ["set-status", "--id", entry_id, "--status", "pr_open"],
            ["link-pr", "--id", entry_id, "--pr", "https://x/1"],
            ["record-metrics", "--id", entry_id, "--phase", "before",
             "--metric", "trigger_f1=0.7"],
        ):
            with self.subTest(argv=argv[0]):
                code, out = run(self.base(*argv))
                self.assertEqual(code, ledger.EXIT_META)
                self.assertIn("classification: excluded_meta", out)
        entry = self.entries()[0]
        self.assertEqual(entry["status"], "excluded_meta")
        self.assertIsNone(entry["pr"])
        self.assertEqual(entry["before"], {})

    def test_meta_entry_can_still_be_recorded_or_rejected(self) -> None:
        run(
            self.base(
                "add", "--source", "retro", "--target", "retro",
                "--finding", "retro を直したい", "--lever", "skill-edit",
            )
        )
        for status in ("excluded_meta", "rejected"):
            with self.subTest(status=status):
                code, _ = run(
                    self.base("set-status", "--id", self.only_id(), "--status", status)
                )
                self.assertEqual(code, ledger.EXIT_OK)
                self.assertEqual(self.entries()[0]["status"], status)

    def test_link_pr_keep_status_leaves_status_untouched(self) -> None:
        run(
            self.base(
                "add", "--source", "retro", "--target", "tdd",
                "--finding", "f", "--lever", "skill-edit",
            )
        )
        code, _ = run(
            self.base(
                "link-pr", "--id", self.only_id(), "--pr", "https://x/1", "--keep-status"
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.entries()[0]["pr"], "https://x/1")
        self.assertEqual(self.entries()[0]["status"], "proposed")

    def test_report_skill_filter(self) -> None:
        for target in ("tdd", "commit"):
            run(
                self.base(
                    "add", "--source", "retro", "--target", target,
                    "--finding", "f", "--lever", "skill-edit",
                    "--allow-unknown-target",
                )
            )
        code, out = run(self.base("report", "--skill", "tdd", "--json"))
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["entries"], 1)
        self.assertEqual(list(report["per_skill"]), ["tdd"])

    def test_add_class_key_groups_differently_worded_findings(self) -> None:
        # 再発は別の実行日に起きる (同日・同クラスの再登録は重複として弾かれる)
        for created, finding in (
            ("2026-09-03", "停止条件が曖昧で再試行が続いた"),
            ("2026-09-10", "別エラーでも再試行が止まらない"),
        ):
            run(
                self.base(
                    "add", "--source", "retro", "--target", "tdd",
                    "--finding", finding, "--lever", "skill-edit",
                    "--class", "stop-condition", "--created", created,
                )
            )
        entries = self.entries()
        self.assertEqual([e["finding_class"] for e in entries], ["stop-condition"] * 2)
        self.assertEqual([e["recurrence"] for e in entries], [1, 2])

    def test_add_unknown_target_is_refused(self) -> None:
        code, _ = run(
            self.base(
                "add", "--source", "retro", "--target", "nope",
                "--finding", "f", "--lever", "skill-edit",
            )
        )
        self.assertEqual(code, ledger.EXIT_FAIL)
        self.assertEqual(self.entries(), [])

    def test_add_unknown_target_with_opt_in_is_recorded(self) -> None:
        code, _ = run(
            self.base(
                "add", "--source", "retro", "--target", "nope",
                "--finding", "f", "--lever", "skill-edit",
                "--allow-unknown-target",
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.entries()[0]["target_skill"], "nope")

    def test_recurrence_increments_across_adds(self) -> None:
        for created in ("2026-09-03", "2026-09-10"):
            run(
                self.base(
                    "add", "--source", "retro", "--target", "tdd",
                    "--finding", "再実行手順が曖昧", "--lever", "skill-edit",
                    "--created", created,
                )
            )
        self.assertEqual([e["recurrence"] for e in self.entries()], [1, 2])

    def test_same_day_same_class_readd_is_refused_as_duplicate(self) -> None:
        argv = [
            "add", "--source", "retro", "--target", "tdd",
            "--finding", "再実行手順が曖昧", "--lever", "skill-edit",
            "--created", "2026-09-03",
        ]
        self.assertEqual(run(self.base(*argv))[0], 0)
        # id は内容由来なので 2 度目は同じ id になる。重複を通すと後続の
        # set-status / link-pr が「最初に一致した行」に当たって別 finding を書き換える
        self.assertEqual(run(self.base(*argv))[0], ledger.EXIT_FAIL)
        self.assertEqual(len(self.entries()), 1)

    def test_skill_filters_accept_path_spelling(self) -> None:
        # 生の文字列比較だと、正規化して保存された行と食い違って静かに 0 件になる
        run(
            self.base(
                "add", "--source", "retro", "--target", "tdd",
                "--finding", "f", "--lever", "skill-edit",
            )
        )
        for spelling in ("tdd", "skills/tdd", " tdd "):
            with self.subTest(spelling=spelling):
                listed = json.loads(
                    run(self.base("list", "--skill", spelling, "--json"))[1]
                )
                self.assertEqual(len(listed), 1)
                report = json.loads(
                    run(self.base("report", "--skill", spelling, "--json"))[1]
                )
                self.assertEqual(report["entries"], 1)
        self.assertEqual(
            json.loads(run(self.base("list", "--skill", "commit", "--json"))[1]), []
        )

    def test_created_is_validated_even_when_id_is_given(self) -> None:
        # 検査が derive_id の中だけだと、--id を渡した経路で丸ごと飛ぶ
        code, _ = run(
            self.base(
                "add", "--source", "retro", "--target", "tdd",
                "--finding", "f", "--lever", "skill-edit",
                "--created", "2026-99-99", "--id", "IMP-20269999-abcdef0123",
            )
        )
        self.assertEqual(code, ledger.EXIT_FAIL)
        self.assertEqual(self.entries(), [])

    def test_manual_id_must_match_format_and_be_unique(self) -> None:
        base_argv = [
            "add", "--source", "retro", "--target", "tdd",
            "--finding", "f", "--lever", "skill-edit",
        ]
        self.assertEqual(
            run(self.base(*base_argv, "--id", "IMP-1"))[0], ledger.EXIT_FAIL
        )
        self.assertEqual(self.entries(), [])
        self.assertEqual(
            run(self.base(*base_argv, "--id", "IMP-20260903-abc1234567"))[0], 0
        )
        self.assertEqual(
            run(self.base(*base_argv, "--id", "IMP-20260903-abc1234567", "--class", "other"))[0],
            ledger.EXIT_FAIL,
        )
        self.assertEqual(len(self.entries()), 1)

    def test_upstream_lever_alias_is_accepted_and_normalized(self) -> None:
        # retro は ept-handoff を出す。argparse で弾くと finding がそのまま落ちる
        code, _ = run(
            self.base(
                "add", "--source", "retro", "--target", "tdd",
                "--finding", "f", "--lever", "ept-handoff",
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.entries()[0]["lever"], "ept")

    def test_target_is_stored_normalized(self) -> None:
        code, _ = run(
            self.base(
                "add", "--source", "retro", "--target", "skills/tdd",
                "--finding", "f", "--lever", "skill-edit",
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.entries()[0]["target_skill"], "tdd")

    def test_list_proposed_returns_rows_with_and_without_pr(self) -> None:
        # 補償経路は proposed のまま pr が入った行を作る。Step 0 が
        # list --status proposed で拾えないと、その行は回収不能になる
        for created in ("2026-09-03", "2026-09-10"):
            run(
                self.base(
                    "add", "--source", "retro", "--target", "tdd",
                    "--finding", f"f-{created}", "--lever", "skill-edit",
                    "--created", created,
                )
            )
        linked = self.entries()[0]["id"]
        self.assertEqual(
            run(self.base("link-pr", "--id", linked, "--pr", "https://x/1", "--keep-status"))[0],
            0,
        )
        listed = json.loads(run(self.base("list", "--status", "proposed", "--json"))[1])
        self.assertEqual(len(listed), 2)
        by_id = {e["id"]: e for e in listed}
        self.assertEqual(by_id[linked]["status"], "proposed")
        self.assertEqual(by_id[linked]["pr"], "https://x/1")
        self.assertTrue(ledger.is_pending_row(by_id[linked]))
        other = next(e for e in listed if e["id"] != linked)
        self.assertIsNone(other["pr"])
        self.assertFalse(ledger.is_pending_row(other))

    def test_list_missing_after_and_report_section(self) -> None:
        # after を取り損ねた merged は delta にも revert candidate にも出ない。
        # 名指ししないと「効果が測られないまま完了扱い」で静かに消える
        run(
            self.base(
                "add", "--source", "retro", "--target", "tdd",
                "--finding", "f", "--lever", "skill-edit", "--created", "2026-09-03",
            )
        )
        entry_id = self.only_id()
        run(self.base("set-status", "--id", entry_id, "--status", "merged"))

        code, out = run(self.base("list", "--missing-after", "--json"))
        self.assertEqual(code, 0)
        self.assertEqual([e["id"] for e in json.loads(out)], [entry_id])
        code, out = run(self.base("report"))
        self.assertIn("## Merged without after metrics (1)", out)

        run(
            self.base(
                "record-metrics", "--id", entry_id, "--phase", "after",
                "--metric", "trigger_f1=0.9",
            )
        )
        self.assertEqual(json.loads(run(self.base("list", "--missing-after", "--json"))[1]), [])
        self.assertIn("## Merged without after metrics (0)", run(self.base("report"))[1])

    def test_write_commands_take_the_ledger_lock(self) -> None:
        # read-modify-write の全体を囲えていないと、重なった実行が互いの更新を消す
        for created in ("2026-09-03", "2026-09-10"):
            run(
                self.base(
                    "add", "--source", "retro", "--target", "tdd",
                    "--finding", "f", "--lever", "skill-edit", "--created", created,
                )
            )
        self.assertEqual(len(self.entries()), 2)
        self.assertTrue(self.ledger_path.with_name("ledger.jsonl.lock").is_file())

    def test_list_filters_by_status(self) -> None:
        for target, status in (("tdd", "pr_open"), ("commit", "merged")):
            run(
                self.base(
                    "add", "--source", "retro", "--target", target,
                    "--finding", "f", "--lever", "skill-edit",
                    "--status", status, "--allow-unknown-target",
                )
            )
        code, out = run(self.base("list", "--status", "pr_open", "--json"))
        self.assertEqual(code, 0)
        listed = json.loads(out)
        self.assertEqual([e["target_skill"] for e in listed], ["tdd"])
        code, out = run(self.base("list"))
        self.assertEqual(code, 0)
        self.assertIn("pr_open", out)
        self.assertIn("merged", out)

    def test_report_lists_class_keys_with_counts(self) -> None:
        for created in ("2026-09-03", "2026-09-10"):
            run(
                self.base(
                    "add", "--source", "retro", "--target", "tdd",
                    "--finding", "f", "--lever", "skill-edit",
                    "--class", "stop-condition", "--created", created,
                )
            )
        code, out = run(self.base("report", "--skill", "tdd"))
        self.assertEqual(code, 0)
        # クラスキーが読めないと add --class に渡す既存キーを選べない
        self.assertIn("class stop-condition: 2", out)

    def test_skills_root_defaults_to_ledger_repository(self) -> None:
        # --ledger で別リポジトリを指したら、skill の実在確認もそちらで行う
        with tempfile.TemporaryDirectory() as other:
            other_ledger = Path(other) / "improvements" / "ledger.jsonl"
            code, _ = run(
                [
                    "--ledger", str(other_ledger),
                    "add", "--source", "retro", "--target", "tdd",
                    "--finding", "f", "--lever", "skill-edit",
                ]
            )
            # cwd 側 (self.root) には tdd があるが、台帳側のリポジトリには無い
            self.assertEqual(code, ledger.EXIT_FAIL)
            code, _ = run(
                [
                    "--ledger", str(other_ledger), "--skills-root", str(self.root),
                    "add", "--source", "retro", "--target", "tdd",
                    "--finding", "f", "--lever", "skill-edit",
                ]
            )
            self.assertEqual(code, ledger.EXIT_OK)

    def test_set_status_and_missing_id(self) -> None:
        run(
            self.base(
                "add", "--source", "retro", "--target", "tdd",
                "--finding", "f", "--lever", "skill-edit",
            )
        )
        self.assertEqual(
            run(self.base("set-status", "--id", self.only_id(), "--status", "merged"))[0],
            0,
        )
        self.assertEqual(self.entries()[0]["status"], "merged")
        self.assertEqual(
            run(
                self.base("set-status", "--id", "IMP-20260101-abcdef0123", "--status", "merged")
            )[0],
            ledger.EXIT_FAIL,
        )

    def test_check_target_exit_codes(self) -> None:
        self.assertEqual(run(["check-target", "tdd"])[0], ledger.EXIT_OK)
        code, out = run(["check-target", "retro"])
        self.assertEqual(code, ledger.EXIT_META)
        self.assertIn("classification: excluded_meta", out)
        self.assertEqual(run(["check-target", "nope"])[0], ledger.EXIT_FAIL)


class TestLedgerFileIO(unittest.TestCase):
    def test_blank_lines_are_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text('\n{"id":"IMP-0001"}\n\n', encoding="utf-8")
            self.assertEqual(ledger.load_entries(path), [{"id": "IMP-0001"}])

    def test_missing_file_is_empty_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ledger.load_entries(Path(tmp) / "absent.jsonl"), [])

    def test_malformed_line_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text("not json\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                ledger.load_entries(path)

    def test_line_separator_inside_finding_does_not_split_entry(self) -> None:
        # U+2028 / U+2029 / U+0085 は splitlines() では行区切りになるが、
        # JSON Lines の区切りは "\n" だけ。finding に混ざっても 1 行 1 エントリを保つ
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            finding = f"停止条件{chr(0x2028)}が曖昧"
            ledger.save_entries(path, [{"id": "IMP-0001", "finding": finding}])
            loaded = ledger.load_entries(path)
            self.assertEqual(loaded, [{"id": "IMP-0001", "finding": finding}])

    def test_roundtrip_preserves_unicode_unescaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            ledger.save_entries(path, [{"finding": "曖昧"}])
            self.assertIn("曖昧", path.read_text(encoding="utf-8"))


def backticked_names(text: str) -> set[str]:
    """`skill-name` 形式で列挙された名前を拾う。"""
    return set(re.findall(r"`([a-z][a-z0-9-]*)`", text))


class TestPendingRow(unittest.TestCase):
    """Step 2.5 が新規候補を抑止してよい行の判定 (純関数)。

    `link-pr --keep-status` を通ると `proposed` のまま `pr` が入る。補償経路が
    実際にこの形を作るので、拒否せず「PR が実在する = pending」として扱う。
    """

    def test_pr_open_is_pending(self) -> None:
        self.assertTrue(ledger.is_pending_row({"status": "pr_open", "pr": "https://x/1"}))

    def test_proposed_with_pr_is_pending(self) -> None:
        # link-pr の後 set-status の前に落ちた残骸。PR は実在するので二重起票しない
        self.assertTrue(
            ledger.is_pending_row({"status": "proposed", "pr": "https://x/1"})
        )

    def test_proposed_without_pr_is_not_pending(self) -> None:
        self.assertFalse(ledger.is_pending_row({"status": "proposed", "pr": None}))

    def test_settled_rows_are_not_pending(self) -> None:
        for status in ("merged", "rejected", "reverted", "excluded_meta"):
            with self.subTest(status=status):
                self.assertFalse(
                    ledger.is_pending_row({"status": status, "pr": "https://x/1"})
                )


class TestVerifyDiff(unittest.TestCase):
    """ブランチが台帳に対して許された変更だけをしているかの検査 (純関数)。

    パスの allow-list は improvements/ledger.jsonl 全体を許すので、行の粒度で
    見ないと「1 行足すついでに他の行を書き換える」経路が残る。
    """

    def entry(self, entry_id: str, **overrides: object) -> dict:
        base = {
            "id": entry_id,
            "created": "2026-09-03",
            "source": "retro",
            "evidence": [],
            "target_skill": "tdd",
            "finding": "f",
            "finding_class": "",
            "lever": "skill-edit",
            "status": "proposed",
            "pr": None,
            "before": {},
            "after": {},
            "recurrence": 1,
            "notes": "",
        }
        base.update(overrides)
        return base

    # --- candidate ---------------------------------------------------
    def test_candidate_single_addition_is_ok(self) -> None:
        new_id = "IMP-20260903-aaaaaaaaaa"
        base = [self.entry("IMP-20260801-bbbbbbbbbb", status="merged")]
        head = base + [self.entry(new_id)]
        self.assertEqual(ledger.check_candidate_diff(base, head, new_id), [])

    def test_candidate_rejects_touching_other_rows(self) -> None:
        other = self.entry("IMP-20260801-bbbbbbbbbb", status="merged")
        new_id = "IMP-20260903-aaaaaaaaaa"
        base = [other]
        tampered = dict(other, finding="書き換えた")
        head = [tampered, self.entry(new_id)]
        problems = ledger.check_candidate_diff(base, head, new_id)
        self.assertTrue(any("書き換わっている" in p for p in problems), problems)

    def test_candidate_rejects_removed_rows(self) -> None:
        new_id = "IMP-20260903-aaaaaaaaaa"
        base = [self.entry("IMP-20260801-bbbbbbbbbb", status="merged")]
        head = [self.entry(new_id)]
        problems = ledger.check_candidate_diff(base, head, new_id)
        self.assertTrue(any("消えている" in p for p in problems), problems)

    def test_candidate_rejects_two_additions(self) -> None:
        new_id = "IMP-20260903-aaaaaaaaaa"
        head = [self.entry(new_id), self.entry("IMP-20260903-cccccccccc")]
        problems = ledger.check_candidate_diff([], head, new_id)
        self.assertTrue(any("ちょうど 1 行" in p for p in problems), problems)

    def test_candidate_rejects_mismatched_id_or_shape(self) -> None:
        new_id = "IMP-20260903-aaaaaaaaaa"
        # manifest の ledger_id と違う行を足す
        problems = ledger.check_candidate_diff(
            [], [self.entry("IMP-20260903-cccccccccc")], new_id
        )
        self.assertTrue(any("一致しない" in p for p in problems), problems)
        # 最初から pr_open / pr 付きで足すのも通さない
        problems = ledger.check_candidate_diff(
            [], [self.entry(new_id, status="pr_open", pr="https://x/1")], new_id
        )
        self.assertTrue(any("status" in p for p in problems), problems)
        self.assertTrue(any("pr が設定" in p for p in problems), problems)

    # --- 重複 id -----------------------------------------------------
    def test_duplicate_ids_helper(self) -> None:
        self.assertEqual(ledger.duplicate_ids([]), [])
        entries = [self.entry("IMP-20260903-aaaaaaaaaa")] * 2 + [
            self.entry("IMP-20260903-cccccccccc")
        ]
        self.assertEqual(ledger.duplicate_ids(entries), ["IMP-20260903-aaaaaaaaaa"])

    def test_candidate_rejects_duplicate_id_bypass(self) -> None:
        # 同じ id を 2 行書くと index_by_id が 1 行しか採らないため、
        # 「追加は 1 行だけ」の検査を素通りできてしまう — 重複自体を弾く
        new_id = "IMP-20260903-aaaaaaaaaa"
        head = [
            self.entry(new_id),
            self.entry(new_id, target_skill="commit", finding="紛れ込ませた行"),
        ]
        problems = ledger.check_candidate_diff([], head, new_id)
        self.assertTrue(any("head に id の重複" in p for p in problems), problems)

    def test_candidate_rejects_duplicate_id_in_base(self) -> None:
        # base 側の重複は「信頼している台帳が既に壊れている」— こちらも通さない
        dup = self.entry("IMP-20260801-bbbbbbbbbb", status="merged")
        new_id = "IMP-20260903-aaaaaaaaaa"
        problems = ledger.check_candidate_diff([dup, dup], [dup, dup, self.entry(new_id)], new_id)
        self.assertTrue(any("base に id の重複" in p for p in problems), problems)

    def test_reconcile_rejects_duplicate_ids(self) -> None:
        dup = self.entry("IMP-20260801-bbbbbbbbbb", status="pr_open", pr="https://x/1")
        problems = ledger.check_reconcile_diff([dup], [dup, dict(dup, status="merged")])
        self.assertTrue(any("head に id の重複" in p for p in problems), problems)
        problems = ledger.check_reconcile_diff([dup, dup], [dup, dup])
        self.assertTrue(any("base に id の重複" in p for p in problems), problems)

    # --- reconcile ---------------------------------------------------
    def test_reconcile_allows_settling_transitions(self) -> None:
        base = [
            self.entry("IMP-20260801-bbbbbbbbbb", status="pr_open", pr="https://x/1"),
            self.entry("IMP-20260701-dddddddddd", status="merged", pr="https://x/2"),
        ]
        head = [
            dict(
                base[0],
                status="merged",
                after={"trigger_f1": 0.9},
                notes="reconciled",
            ),
            dict(base[1], status="reverted"),
        ]
        self.assertEqual(ledger.check_reconcile_diff(base, head), [])

    def test_reconcile_allows_the_repair_case(self) -> None:
        # PR 起票後に link-pr が落ちた行を pr_open へ進める
        base = [self.entry("IMP-20260903-aaaaaaaaaa")]
        head = [dict(base[0], status="pr_open", pr="https://x/1")]
        self.assertEqual(ledger.check_reconcile_diff(base, head), [])

    def test_reconcile_rejects_illegal_field_and_transition(self) -> None:
        base = [self.entry("IMP-20260801-bbbbbbbbbb", status="pr_open", pr="https://x/1")]
        head = [dict(base[0], finding="書き換えた", target_skill="commit")]
        problems = ledger.check_reconcile_diff(base, head)
        self.assertTrue(any("変更してはいけない項目" in p for p in problems), problems)

        head2 = [dict(base[0], status="reverted")]
        problems = ledger.check_reconcile_diff(base, head2)
        self.assertTrue(any("許されない status 遷移" in p for p in problems), problems)

    def test_reconcile_rejects_removals_and_bad_additions(self) -> None:
        base = [self.entry("IMP-20260801-bbbbbbbbbb", status="merged")]
        self.assertTrue(
            any("消えている" in p for p in ledger.check_reconcile_diff(base, []))
        )
        head = base + [self.entry("IMP-20260903-aaaaaaaaaa", status="merged")]
        problems = ledger.check_reconcile_diff(base, head)
        self.assertTrue(any("status が proposed でない" in p for p in problems), problems)

    # --- CLI ---------------------------------------------------------
    def test_cli_reports_violations_with_exit_code(self) -> None:
        new_id = "IMP-20260903-aaaaaaaaaa"
        with tempfile.TemporaryDirectory() as tmp:
            base_path = Path(tmp) / "base.jsonl"
            head_path = Path(tmp) / "head.jsonl"
            ledger.save_entries(base_path, [])
            ledger.save_entries(head_path, [self.entry(new_id)])
            code, out = run(
                [
                    "verify-diff", "--base", str(base_path), "--head", str(head_path),
                    "--mode", "candidate", "--ledger-id", new_id,
                ]
            )
            self.assertEqual(code, ledger.EXIT_OK)
            self.assertIn("verify-diff: ok", out)

            ledger.save_entries(head_path, [self.entry("IMP-20260903-cccccccccc")])
            code, _ = run(
                [
                    "verify-diff", "--base", str(base_path), "--head", str(head_path),
                    "--mode", "candidate", "--ledger-id", new_id,
                ]
            )
            self.assertEqual(code, ledger.EXIT_FAIL)

    def test_cli_candidate_requires_ledger_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            head_path = Path(tmp) / "head.jsonl"
            ledger.save_entries(head_path, [])
            code, _ = run(
                ["verify-diff", "--head", str(head_path), "--mode", "candidate"]
            )
            self.assertEqual(code, ledger.EXIT_FAIL)


class TestDocumentedMetaList(unittest.TestCase):
    """除外リストは script と ドキュメント 2 か所で一致していなければならない。

    ずれると「SKILL.md では除外と書いてあるのに script は編集を許す」という、
    最も気付きにくい形で Iron Law が破れる。
    """

    def test_skill_md_exclusion_list_matches(self) -> None:
        text = (REPO_ROOT / "skills" / "skill-improver" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        lines = [ln for ln in text.splitlines() if ln.startswith("除外リスト:")]
        self.assertEqual(
            len(lines), 1, "SKILL.md の「除外リスト:」行が 1 行見つからない"
        )
        self.assertEqual(backticked_names(lines[0]), set(ledger.META_SKILLS))

    def test_improvements_readme_exclusion_list_matches(self) -> None:
        text = (REPO_ROOT / "improvements" / "README.md").read_text(encoding="utf-8")
        _, heading, tail = text.partition("## メタスキルは対象外")
        self.assertTrue(heading, "improvements/README.md に除外リストの節が無い")
        listed, marker, _ = tail.partition("を対象と")
        self.assertTrue(marker, "除外リスト節の書式が変わっている")
        self.assertEqual(backticked_names(listed), set(ledger.META_SKILLS))


class TestRepoLedgerFile(unittest.TestCase):
    """リポジトリ本体の台帳が読める形であることを確認する (fail-closed)."""

    def test_repo_ledger_parses(self) -> None:
        path = REPO_ROOT / "improvements" / "ledger.jsonl"
        self.assertTrue(
            path.is_file(),
            "improvements/ledger.jsonl が無い。台帳の置き場を変えたら"
            " skill-improver の SKILL.md / references と本テストも追随させること",
        )
        for entry in ledger.load_entries(path):
            with self.subTest(entry=entry.get("id")):
                self.assertIn(entry.get("source"), ledger.SOURCES)
                self.assertIn(entry.get("lever"), ledger.LEVERS)
                self.assertIn(entry.get("status"), ledger.STATUSES)
                self.assertIsNotNone(ledger.ID_RE.match(str(entry.get("id"))))


if __name__ == "__main__":
    unittest.main()
