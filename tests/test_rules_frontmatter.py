"""rules/ (配布) と rules-local/ (repo 専用) の不変条件を機械検証する.

- rules/ は consumer が `rulesync fetch --features rules` で**丸ごと**受け取るため、
  frontmatter を持つ rule ファイル以外 (README 等) が混ざると consumer 側の
  generate が壊れる (PR #53 レビューで指摘された配布事故クラス)
- repo 専用の内容 (root rule 等) は rules-local/ に置き、配布 feature に混ぜない
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.S)


def rule_files(directory: str) -> list[Path]:
    # nested rule (rules/**/*.md) も staging・fetch の対象なので再帰的に検証する
    path = REPO_ROOT / directory
    if not path.is_dir():
        return []
    return sorted(p for p in path.rglob("*") if p.is_file())


class TestDistributableRules(unittest.TestCase):
    def test_rules_dir_contains_only_frontmattered_rule_files(self) -> None:
        files = rule_files("rules")
        self.assertGreater(len(files), 0)
        for path in files:
            rel = path.relative_to(REPO_ROOT).as_posix()
            with self.subTest(file=rel):
                self.assertEqual(
                    path.suffix,
                    ".md",
                    f"{rel}: rules/ には .md の rule ファイル以外を置かない",
                )
                self.assertNotEqual(
                    path.name,
                    "README.md",
                    "rules/README.md は consumer の generate を壊す (repo 説明は rules-local の root rule へ)",
                )
                fm = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
                self.assertIsNotNone(fm, f"{rel}: rule frontmatter がない")
                self.assertIn("description:", fm.group(1), f"{rel}: description がない")

    def test_distributed_rules_are_not_repo_local(self) -> None:
        # 配布 rule に root: true を置くと consumer の root instructions を
        # このリポジトリ固有の内容で上書きしてしまう
        for path in rule_files("rules"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            with self.subTest(file=rel):
                fm = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
                self.assertIsNotNone(fm)
                self.assertNotRegex(
                    fm.group(1),
                    re.compile(r"^root:\s*true\s*$", re.M),
                    f"{rel}: root rule は配布しない (rules-local/ へ)",
                )


class TestRepoLocalRules(unittest.TestCase):
    def test_local_rules_have_frontmatter(self) -> None:
        files = [p for p in rule_files("rules-local") if p.suffix == ".md"]
        self.assertGreater(len(files), 0)
        for path in files:
            rel = path.relative_to(REPO_ROOT).as_posix()
            with self.subTest(file=rel):
                fm = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
                self.assertIsNotNone(fm, f"{rel}: rule frontmatter がない")

    def test_exactly_one_root_rule(self) -> None:
        root_rules = []
        for directory in ("rules", "rules-local"):
            for path in rule_files(directory):
                if path.suffix != ".md":
                    continue
                fm = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
                if fm and re.search(r"^root:\s*true\s*$", fm.group(1), re.M):
                    root_rules.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual(
            len(root_rules),
            1,
            f"root: true の rule はちょうど 1 つ (現在: {root_rules})",
        )


if __name__ == "__main__":
    unittest.main()
