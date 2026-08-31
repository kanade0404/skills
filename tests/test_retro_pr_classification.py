"""retro_scan.py --pr モードの終端分類純関数 classify_thread のテスト (F4)。

classify_thread はネットワーク・gh・duckdb に依存しない純関数。本テストは
duckdb の stub を差し込まずに module を import することで、「分類関数だけを
duckdb / gh 無しで import できる」構造要件 (duckdb の遅延 import) も同時に
検証する。fixture は pr-review-respond の終端返信語彙に合わせた文字列のみで、
ネットワークは一切使わない。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "retro" / "scripts" / "retro_scan.py"

# 他テスト (test_retro_scan.py) が stub を差し込んでいても、この import 自体は
# duckdb を要求しないこと。専用の module 名で独立ロードする。
_spec = importlib.util.spec_from_file_location("retro_scan_pr_test", SCRIPT)
retro_scan = importlib.util.module_from_spec(_spec)
sys.modules["retro_scan_pr_test"] = retro_scan
_spec.loader.exec_module(retro_scan)

classify = retro_scan.classify_thread

FINDING = "この実装は cwd 検証を欠いており、slug 衝突時に別プロジェクトが混入します。"


class TestRepresentatives(unittest.TestCase):
    """6 分類それぞれの代表ケース。"""

    def test_valid_fixed_in_sha(self) -> None:
        self.assertEqual(classify([FINDING, "Fixed in cf80d59"]), "VALID")

    def test_valid_fixed_in_backticked_long_sha(self) -> None:
        self.assertEqual(
            classify([FINDING,
                      "Fixed in `f79e6a68c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6`"]),
            "VALID",
        )

    def test_invalid_push_maintainer_judgment(self) -> None:
        self.assertEqual(
            classify([FINDING,
                      ("根拠: 仕様どおりの挙動です。maintainer 判断待ちとし、"
                       "self-resolve しません")]),
            "INVALID_PUSH",
        )

    def test_invalid_push_self_resolve_alone(self) -> None:
        self.assertEqual(
            classify([FINDING, "この指摘は誤検知と考えます。self-resolve しません"]),
            "INVALID_PUSH",
        )

    def test_valid_defer_issue_url_plus_defer_wording(self) -> None:
        self.assertEqual(
            classify([FINDING,
                      "後続対応とします: https://github.com/o/r/issues/12 で追跡"]),
            "VALID_DEFER",
        )

    def test_duplicate_existing_thread_reference(self) -> None:
        self.assertEqual(
            classify([FINDING,
                      ("既存スレッド https://github.com/o/r/pull/96"
                       "#discussion_r3683948151 と同一の指摘です")]),
            "DUPLICATE",
        )

    def test_withdrawn(self) -> None:
        self.assertEqual(
            classify([FINDING, "再確認の結果、誤読でした。指摘を撤回します"]),
            "WITHDRAWN",
        )
        self.assertEqual(
            classify([FINDING, "Withdrawn after re-checking the base branch."]),
            "WITHDRAWN",
        )

    def test_unterminated_no_replies(self) -> None:
        self.assertEqual(classify([FINDING]), "UNTERMINATED")

    def test_unterminated_reply_without_terminal_marker(self) -> None:
        self.assertEqual(
            classify([FINDING, "確認します。少々お待ちください"]), "UNTERMINATED")


class TestBoundaries(unittest.TestCase):
    """境界: 空入力・finding 本文の除外・複数パターン混在時の優先順位。"""

    def test_empty_thread(self) -> None:
        self.assertEqual(classify([]), "UNTERMINATED")

    def test_empty_and_none_reply_bodies(self) -> None:
        self.assertEqual(classify([FINDING, "", None]), "UNTERMINATED")

    def test_finding_body_is_never_evidence(self) -> None:
        # 先頭コメント (finding 自身) が "Fixed in <sha>" を引用していても
        # 分類根拠にしない
        self.assertEqual(
            classify(["前回と同様に Fixed in abc1234 の形式で返信してください"]),
            "UNTERMINATED",
        )

    def test_latest_reply_wins_fix_after_pushback(self) -> None:
        self.assertEqual(
            classify([FINDING, "maintainer 判断待ちとします",
                      "再考の結果修正しました。Fixed in abc1234"]),
            "VALID",
        )

    def test_latest_reply_wins_pushback_after_fix(self) -> None:
        self.assertEqual(
            classify([FINDING, "Fixed in abc1234",
                      "revert しました。やはり self-resolve しません"]),
            "INVALID_PUSH",
        )

    def test_duplicate_beats_valid_in_same_reply(self) -> None:
        # duplicate 返信は正典スレッドの修正 SHA を引用しがち — DUPLICATE 優先
        self.assertEqual(
            classify([FINDING,
                      "重複: 既存スレッドで Fixed in abc1234 として対応済み"]),
            "DUPLICATE",
        )

    def test_known_false_positive_fix_reply_with_cross_reference(self) -> None:
        # 既知の偽陽性の固定: fix 返信が別スレッドを #discussion_r 参照すると
        # DUPLICATE 優先規則に吸われて誤分類される — 下読み (機械分類) の
        # tradeoff として許容し、確定判断は評価 subagent が上書きする
        self.assertEqual(
            classify([FINDING,
                      ("Fixed in abc1234。同根の指摘 #discussion_r999 も"
                       "この修正で解消しています")]),
            "DUPLICATE",
        )

    def test_valid_beats_defer_pair_in_same_reply(self) -> None:
        # 修正 + 残件 issue 化の返信は fix が終端 — VALID 優先
        self.assertEqual(
            classify([FINDING,
                      ("Fixed in abc1234。残件は follow-up として "
                       "https://github.com/o/r/issues/5 へ")]),
            "VALID",
        )

    def test_withdrawn_beats_everything_in_same_reply(self) -> None:
        self.assertEqual(
            classify([FINDING,
                      "撤回します — Fixed in abc1234 は誤りで、重複でもありません"]),
            "WITHDRAWN",
        )

    def test_issue_url_without_defer_wording_is_not_defer(self) -> None:
        self.assertEqual(
            classify([FINDING, "背景は https://github.com/o/r/issues/9 参照"]),
            "UNTERMINATED",
        )

    def test_defer_wording_without_issue_ref_is_not_defer(self) -> None:
        self.assertEqual(
            classify([FINDING, "後続で検討します"]), "UNTERMINATED")

    def test_tracked_in_bare_issue_number_is_defer(self) -> None:
        # F11: "Tracked in #114" は issue URL でも "issue #N" でもない —
        # 従来の _ISSUE_REF_RE は "issue" という語を要求しており、この形の defer
        # 表記を拾えず UNTERMINATED に誤判定していた
        self.assertEqual(
            classify([FINDING, "Tracked in #114"]), "VALID_DEFER")

    def test_deferred_to_bare_issue_number_is_defer(self) -> None:
        self.assertEqual(
            classify([FINDING, "Deferred to #113"]), "VALID_DEFER")

    def test_arrow_bare_issue_number_with_defer_wording_is_defer(self) -> None:
        self.assertEqual(
            classify([FINDING, "後続対応します → #120"]), "VALID_DEFER")

    def test_bare_hash_number_without_context_word_is_not_defer(self) -> None:
        # 過剰マッチガード: 返信中に defer 語 (後続) があっても、#NNN の直前に
        # 文脈語 (tracked/deferred/issue/→ 等) が無ければ issue 参照とみなさない
        # (コード中の #123 コメントや無関係な PR 参照を defer と誤認しない)
        self.assertEqual(
            classify([FINDING, "後続で検討します。関連 PR #123 を見てください"]),
            "UNTERMINATED",
        )


    def test_labeled_pr_reference_with_defer_wording_is_not_defer(self) -> None:
        # PR #115 review (Devin): "Tracked in PR #114" carries defer wording and
        # a #NNN, but names a PR — there is no follow-up issue, so the thread is
        # not terminated as VALID_DEFER.
        self.assertEqual(
            classify([FINDING, "Tracked in PR #114"]), "UNTERMINATED")
        self.assertEqual(
            classify([FINDING, "defer this; see PR #123"]), "UNTERMINATED")


class TestIssueRefRegex(unittest.TestCase):
    """F11: _ISSUE_REF_RE 単体の境界確認 (classify_thread 経由だと _DEFER_RE との
    共起判定に埋もれるため、regex 自体の挙動を直接固定する)。"""

    def test_matches_tracked_in_bare_number(self) -> None:
        self.assertIsNotNone(retro_scan._ISSUE_REF_RE.search("Tracked in #114"))

    def test_matches_deferred_to_bare_number(self) -> None:
        self.assertIsNotNone(retro_scan._ISSUE_REF_RE.search("Deferred to #113"))

    def test_matches_arrow_bare_number(self) -> None:
        self.assertIsNotNone(retro_scan._ISSUE_REF_RE.search("→ #120"))

    def test_matches_issue_word_plus_number(self) -> None:
        self.assertIsNotNone(retro_scan._ISSUE_REF_RE.search("issue #7"))

    def test_matches_issues_url(self) -> None:
        self.assertIsNotNone(
            retro_scan._ISSUE_REF_RE.search("https://github.com/o/r/issues/9"))

    def test_does_not_match_bare_number_without_context(self) -> None:
        self.assertIsNone(retro_scan._ISSUE_REF_RE.search("関連 PR #123 を見てください"))

    def test_does_not_match_hex_color_like_token(self) -> None:
        self.assertIsNone(retro_scan._ISSUE_REF_RE.search("background: #123456;"))

    def test_does_not_match_explicitly_labeled_pr_reference(self) -> None:
        # PR #115 review (Devin): a context word followed by an explicit "PR"
        # label is a pull-request cross-reference, not a follow-up issue —
        # counting it made replies claim a deferral target that never exists.
        for text in ("Tracked in PR #114",
                     "defer this; see PR #123",
                     "follow-up は pr#99 側で扱う",
                     "Deferred — see PR  #7"):
            with self.subTest(text=text):
                self.assertIsNone(retro_scan._ISSUE_REF_RE.search(text))

    def test_still_matches_bare_number_after_context_word(self) -> None:
        # The PR-label exclusion must not swallow the bare-number defer forms
        # F11 widened the regex for in the first place.
        for text in ("Tracked in #114", "see #120", "follow-up: #7"):
            with self.subTest(text=text):
                self.assertIsNotNone(retro_scan._ISSUE_REF_RE.search(text))


class TestClassInventory(unittest.TestCase):
    def test_thread_classes_cover_the_six_terminals(self) -> None:
        self.assertEqual(
            set(retro_scan.THREAD_CLASSES),
            {"VALID", "INVALID_PUSH", "VALID_DEFER", "DUPLICATE",
             "WITHDRAWN", "UNTERMINATED"},
        )


if __name__ == "__main__":
    unittest.main()
