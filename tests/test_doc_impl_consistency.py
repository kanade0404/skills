"""SKILL.md の数値記述と同梱スクリプト実装の整合を機械検証する sensor.

背景 (PR #97 retro): 「doc と実装の不整合」クラスが 1 PR で 3 件再発した
(30 分 vs 32 分の累計値、返信/resolve の実行順序、診断表示の文言)。このうち
数値型の不整合は grep で機械検査できるため、ここで不変条件として固定する。

現在の検査対象: skills/ci-self-heal/scripts/wait_gate.sh の default deadline
(`deadline="${2:-480}"` の 480) と、SKILL.md 本文の「deadline 480 秒」表記
および「累計 32 分 (480 秒 × 4 回)」表記。

判定は fail-closed: 対象ファイルが無い・期待パターンが抽出できない場合は
skip にせず fail する (rules/fail-closed.md — 抽出の空振りを「検査済み」と
誤認させない)。将来の同型検査は下の抽出ヘルパを再利用して追加する。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WAIT_GATE = REPO_ROOT / "skills" / "ci-self-heal" / "scripts" / "wait_gate.sh"
CI_SELF_HEAL_SKILL = REPO_ROOT / "skills" / "ci-self-heal" / "SKILL.md"


def read_required(path: Path) -> str:
    """対象ファイルを読む。存在しなければ fail (skip にしない — fail-closed)."""
    if not path.is_file():
        raise AssertionError(
            f"{path.relative_to(REPO_ROOT)} が存在しない。検査対象の削除 /"
            " rename はこのテストの対象パスも同時に更新すること (silent skip"
            " にすると doc-impl 検査そのものが黙って消える)"
        )
    return path.read_text(encoding="utf-8")


def shell_positional_default(script_path: Path, var_name: str, position: int) -> int:
    """`var="${N:-default}"` 形式の数値 default を抽出する。無ければ fail."""
    script_text = read_required(script_path)
    pattern = rf'^{re.escape(var_name)}="\$\{{{position}:-(\d+)\}}"'
    m = re.search(pattern, script_text, re.M)
    if m is None:
        raise AssertionError(
            f"{script_path.relative_to(REPO_ROOT)} に"
            f" `{var_name}=\"${{{position}:-<数値>}}\"` が"
            " 見つからない。default の持ち方を変えた場合はこの抽出ヘルパを"
            " 実装に追随させること"
        )
    return int(m.group(1))


def doc_deadline_mentions(skill_text: str) -> list[int]:
    """SKILL.md 本文の `deadline <N> 秒` 表記を全て抽出する。無ければ fail."""
    values = [int(v) for v in re.findall(r"deadline\s+(\d+)\s*秒", skill_text)]
    if not values:
        raise AssertionError(
            "SKILL.md に `deadline <N> 秒` 表記が見つからない — 表記を変えた"
            " 場合はこの抽出ヘルパを追随させること (空振りを pass にしない)"
        )
    return values


def doc_cumulative_budget(skill_text: str) -> tuple[int, int, int]:
    """`累計 <M> 分 (<S> 秒 × <K> 回)` 表記を (分, 秒, 回) で抽出する。無ければ fail."""
    m = re.search(r"累計\s*(\d+)\s*分\s*\((\d+)\s*秒\s*×\s*(\d+)\s*回\)", skill_text)
    if m is None:
        raise AssertionError(
            "SKILL.md に `累計 <M> 分 (<S> 秒 × <K> 回)` 表記が見つからない —"
            " 表記を変えた場合はこの抽出ヘルパを追随させること"
        )
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


class TestWaitGateDocImplConsistency(unittest.TestCase):
    def test_default_deadline_matches_doc(self) -> None:
        """(i) スクリプトの default deadline と SKILL.md の数値記述が一致する."""
        script_default = shell_positional_default(
            WAIT_GATE, "deadline", position=2
        )
        skill_text = read_required(CI_SELF_HEAL_SKILL)
        for mention in doc_deadline_mentions(skill_text):
            self.assertEqual(
                mention,
                script_default,
                f"SKILL.md の `deadline {mention} 秒` が wait_gate.sh の default"
                f" ({script_default}) と不一致。片方だけ変えると呼出側が実装と"
                " 違う契約で動く — 両方を同時に更新すること",
            )
        _, cumulative_secs, _ = doc_cumulative_budget(skill_text)
        self.assertEqual(
            cumulative_secs,
            script_default,
            f"SKILL.md の累計表記の単価 ({cumulative_secs} 秒) が wait_gate.sh の"
            f" default deadline ({script_default}) と不一致",
        )

    def test_cumulative_budget_arithmetic(self) -> None:
        """(ii) 累計 = default × 回数 が「<M> 分」表記と一致する (秒換算)."""
        minutes, secs, reps = doc_cumulative_budget(read_required(CI_SELF_HEAL_SKILL))
        self.assertEqual(
            minutes * 60,
            secs * reps,
            f"SKILL.md の累計表記が自己矛盾: {minutes} 分 ({minutes * 60} 秒) ≠"
            f" {secs} 秒 × {reps} 回 ({secs * reps} 秒)。PR #97 で「30 分」と"
            " 書いて実際は 32 分だった同型の再発",
        )


if __name__ == "__main__":
    unittest.main()
