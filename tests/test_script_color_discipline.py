"""skill 同梱スクリプトの gh 出力色規律を機械検証する sensor.

背景 (2026-07-06 retro): CLICOLOR_FORCE=1 を export する環境では `gh api` の
生 JSON 出力が pipe 先でも ANSI 色付けされ、下流の jq (--argjson / parse) を
静かに壊す。同根の故障が監視 2 回 + skill 同梱スクリプト 1 回 (pr-review-respond
の fetch_threads.sh) で再発したため、「gh の出力を jq で機械処理するスクリプトは
入口で色強制を無効化する」を不変条件として検査する。

判定 (レビュー指摘 skills#67 を反映して厳格化):
- 対象 = skills/*/scripts/ 配下のシェルスクリプトのうち、gh 呼び出しと jq 消費
  パターン (`| jq`, `jq ... <<<`, `--argjson`) の両方を含むもの
- 合格 = **そのファイル自身が**、最初の gh 呼び出しより前の非コメント行で
  `export CLICOLOR_FORCE=0` を行うか、gh 呼び出し自体に `CLICOLOR_FORCE=0 gh`
  の env prefix を付けていること
  - dispatcher (prr 等) による dir 単位の免除はしない: per-action スクリプトは
    直接実行されうるため、各自が無効化する (sibling が壊れた対象を隠す穴を塞ぐ)
  - `NO_COLOR=1` 単独は不合格: 実測で gh の JSON colorizer は NO_COLOR では
    止まらず、CLICOLOR_FORCE=0 だけが有効だった (2026-07-06 の検証)
  - コメントアウトされた export は不合格 (行頭アンカー + 行内 `#` 除外)
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"

GH_CALL_RE = re.compile(r"\bgh (api|pr|issue|repo|run|workflow|search)\b")
JQ_CONSUME_RE = re.compile(r"\|\s*jq\b|\bjq\b[^\n]*<<<|--argjson")
# 非コメント行の行頭 export、または gh 呼び出しへの直接の env prefix のみ認める
NEUTRALIZE_RE = re.compile(
    r"^\s*export\s+CLICOLOR_FORCE=0\b|^[^#\n]*?\bCLICOLOR_FORCE=0\s+gh\b",
    re.M,
)


def shell_scripts(directory: Path) -> list[Path]:
    scripts = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if path.suffix in (".sh", ""):
            head = path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
            if path.suffix == ".sh" or (head and "bash" in head[0]):
                scripts.append(path)
    return scripts


class TestGhColorNeutralization(unittest.TestCase):
    def test_gh_jq_scripts_neutralize_forced_color(self) -> None:
        checked = 0
        for scripts_dir in sorted(SKILLS_DIR.glob("*/scripts")):
            for path in shell_scripts(scripts_dir):
                text = path.read_text(encoding="utf-8", errors="replace")
                gh_call = GH_CALL_RE.search(text)
                if not (gh_call and JQ_CONSUME_RE.search(text)):
                    continue
                checked += 1
                rel = path.relative_to(REPO_ROOT).as_posix()
                with self.subTest(script=rel):
                    neutralize = NEUTRALIZE_RE.search(text)
                    self.assertTrue(
                        neutralize is not None and neutralize.start() < gh_call.start(),
                        f"{rel}: gh の出力を jq で機械処理しているのに、最初の gh"
                        " 呼び出しより前に `export CLICOLOR_FORCE=0` (非コメント行)"
                        " が無い。CLICOLOR_FORCE=1 環境で jq が静かに壊れる —"
                        " rules/bash-and-api-discipline.md 参照。NO_COLOR 単独は"
                        " gh の JSON colorizer を止めないため不合格",
                    )
        # 対象パターンが 1 件も無いなら検査自体が空振りしている (リグレッション検知)
        self.assertGreater(checked, 0, "gh|jq を使う skill script が見つからない")


if __name__ == "__main__":
    unittest.main()
