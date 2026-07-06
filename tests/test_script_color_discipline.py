"""skill 同梱スクリプトの gh 出力色規律を機械検証する sensor.

背景 (2026-07-06 retro): CLICOLOR_FORCE=1 を export する環境では `gh api` の
生 JSON 出力が pipe 先でも ANSI 色付けされ、下流の jq (--argjson / parse) を
静かに壊す。同根の故障が監視 2 回 + skill 同梱スクリプト 1 回 (pr-review-respond
の fetch_threads.sh) で再発したため、「gh の出力を jq で機械処理するスクリプトは
入口で NO_COLOR / CLICOLOR_FORCE を無効化する」を不変条件として検査する。

判定:
- 対象 = skills/*/scripts/ 配下のシェルスクリプトのうち、gh の出力を jq に
  流すパターン (`| jq`, `jq ... <<<`, `--argjson`) と `gh ` 呼び出しの両方を
  含むもの
- 合格 = そのファイル自身、または同じ scripts/ ディレクトリ内の entry point
  (NO_COLOR / CLICOLOR_FORCE を export するファイル。pr-review-respond の
  `prr` のような dispatcher を想定) が色を無効化している
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"

GH_CALL_RE = re.compile(r"\bgh (api|pr|issue|repo|run|workflow|search)\b")
JQ_CONSUME_RE = re.compile(r"\|\s*jq\b|\bjq\b[^\n]*<<<|--argjson")
NEUTRALIZE_RE = re.compile(
    r"export\s+NO_COLOR=|export\s+CLICOLOR_FORCE=0|CLICOLOR_FORCE=0\s+gh\b"
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
            scripts = shell_scripts(scripts_dir)
            dir_has_neutralizing_entry = any(
                NEUTRALIZE_RE.search(p.read_text(encoding="utf-8", errors="replace"))
                for p in scripts
            )
            for path in scripts:
                text = path.read_text(encoding="utf-8", errors="replace")
                if not (GH_CALL_RE.search(text) and JQ_CONSUME_RE.search(text)):
                    continue
                checked += 1
                rel = path.relative_to(REPO_ROOT).as_posix()
                with self.subTest(script=rel):
                    self.assertTrue(
                        NEUTRALIZE_RE.search(text) or dir_has_neutralizing_entry,
                        f"{rel}: gh の出力を jq で機械処理しているのに、入口で"
                        " NO_COLOR / CLICOLOR_FORCE を無効化していない"
                        " (CLICOLOR_FORCE=1 環境で jq が静かに壊れる —"
                        " rules/bash-and-api-discipline.md 参照)",
                    )
        # 対象パターンが 1 件も無いなら検査自体が空振りしている (リグレッション検知)
        self.assertGreater(checked, 0, "gh|jq を使う skill script が見つからない")


if __name__ == "__main__":
    unittest.main()
