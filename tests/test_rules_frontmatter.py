"""rulesync の `rules` feature が生成物・配布枠のどちらにも再登場しないことを検証する.

ADR 0018 (rule を廃止し、指示は skill か決定論的ハーネスに限定する) 条件 2:
「廃止を規約ではなく生成物の不変条件として検証する」。`rules/` / `rules-local/` が
存在しないこと、生成物に root rule 由来の出力 (`.claude/rules` および生成
`CLAUDE.md` / `AGENTS.md`) が現れないことを機械的に検証し、現れたら CI を落とす。

`.codex/rules/rulesync.rules` は対象外 (ADR 0018 条件 4) — これは `permissions`
feature が `permissions.json` から生成する Codex の exec-policy であって、
`rules` feature の生成物ではない。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestRulesFeatureIsAbolished(unittest.TestCase):
    def test_no_rules_source_dir(self) -> None:
        self.assertFalse(
            (REPO_ROOT / "rules").exists(),
            "rules/ は ADR 0018 で廃止された配布枠 — 再登場させない",
        )

    def test_no_rules_local_source_dir(self) -> None:
        self.assertFalse(
            (REPO_ROOT / "rules-local").exists(),
            "rules-local/ は ADR 0018 で廃止された repo 専用 rule 置き場 — 再登場させない",
        )

    def test_no_claude_rules_generated_dir(self) -> None:
        self.assertFalse(
            (REPO_ROOT / ".claude" / "rules").exists(),
            ".claude/rules は root rule 由来の生成物 — rules feature 廃止後は生成されない",
        )

    def test_no_generated_root_claude_md(self) -> None:
        self.assertFalse(
            (REPO_ROOT / "CLAUDE.md").exists(),
            "root CLAUDE.md は root rule 由来の生成物 — rules feature 廃止後は生成されない",
        )

    def test_no_generated_root_agents_md(self) -> None:
        self.assertFalse(
            (REPO_ROOT / "AGENTS.md").exists(),
            "root AGENTS.md は root rule 由来の生成物 — rules feature 廃止後は生成されない",
        )


if __name__ == "__main__":
    unittest.main()
