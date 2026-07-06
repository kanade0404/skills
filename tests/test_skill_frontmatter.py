"""skills/*/SKILL.md の frontmatter 不変条件を機械検証する (stdlib unittest).

検証する不変条件:
- frontmatter と name / description が存在する
- name はディレクトリ名と一致する (カタログ配布の不変条件, CLAUDE.md)
- name は小文字英数字とハイフンのみ・64 文字以内 (Anthropic 公式制約)
- description は 1024 文字以内 (Anthropic 公式制約。超過すると Codex 等の
  他エージェントの skill loader が読み込みに失敗する — 2026-07-06 に実発生)

由来: codex exec が issue-driven-development の description 超過で
skill load に失敗した実障害からの sensor 昇格。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"

MAX_DESCRIPTION_LENGTH = 1024
MAX_NAME_LENGTH = 64
NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.S)
NAME_RE = re.compile(r"^name:\s*(\S+)\s*$", re.M)
DESCRIPTION_RE = re.compile(
    r"^description:\s*(?:(?P<style>[|>][-+]?)\s*\r?\n(?P<block>(?:[ ]+.*\r?\n?|\r?\n)*)|(?P<inline>.+))",
    re.M,
)


def extract_description(frontmatter: str) -> str | None:
    match = DESCRIPTION_RE.search(frontmatter)
    if match is None:
        return None
    if match.group("block") is None:
        return match.group("inline").strip()

    style = match.group("style")
    raw_lines = match.group("block").splitlines()
    # YAML block scalar のインデント幅は最初の内容行で決まる (固定 2 space を仮定しない)
    indent = next(
        (len(line) - len(line.lstrip(" ")) for line in raw_lines if line.strip()),
        None,
    )
    if indent is None:
        return ""
    text = "\n".join(line[indent:] if line.strip() else "" for line in raw_lines)
    # chomping: '-' (strip) は末尾改行なし、既定 (clip) と '+' (keep) は改行 1 つを数える。
    # '+' の複数改行は 1 つに正規化する (最大長チェックの用途では十分)
    text = text.rstrip("\n")
    if text and not style.endswith("-"):
        text += "\n"
    return text


def skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


class TestExtractDescription(unittest.TestCase):
    def test_inline_description(self) -> None:
        self.assertEqual(extract_description('description: "hello world"'), '"hello world"')

    def test_block_with_two_space_indent(self) -> None:
        fm = "description: |\n  line1\n  line2"
        self.assertEqual(extract_description(fm), "line1\nline2\n")

    def test_block_with_four_space_indent(self) -> None:
        # インデント幅は最初の内容行から決まる (2 space 固定を仮定しない)
        fm = "description: |\n    line1\n    line2"
        self.assertEqual(extract_description(fm), "line1\nline2\n")

    def test_default_chomping_keeps_single_trailing_newline(self) -> None:
        fm = "description: |\n  line1\n\n"
        self.assertEqual(extract_description(fm), "line1\n")

    def test_strip_chomping_removes_trailing_newline(self) -> None:
        fm = "description: |-\n  line1\n  line2\n"
        self.assertEqual(extract_description(fm), "line1\nline2")

    def test_length_counts_trailing_newline_for_default_chomping(self) -> None:
        # `|` (clip) では末尾改行 1 つが値に含まれ、長さ判定にも数えられる
        fm = "description: |\n  abc"
        desc = extract_description(fm)
        assert desc is not None
        self.assertEqual(len(desc), 4)


class TestSkillFrontmatter(unittest.TestCase):
    def test_catalog_is_not_empty(self) -> None:
        self.assertGreater(len(skill_files()), 0)

    def test_frontmatter_invariants(self) -> None:
        for path in skill_files():
            rel = path.relative_to(REPO_ROOT).as_posix()
            with self.subTest(skill=rel):
                text = path.read_text(encoding="utf-8")
                fm_match = FRONTMATTER_RE.match(text)
                self.assertIsNotNone(fm_match, f"{rel}: frontmatter がない")
                frontmatter = fm_match.group(1)

                name_match = NAME_RE.search(frontmatter)
                self.assertIsNotNone(name_match, f"{rel}: name がない")
                name = name_match.group(1)

                self.assertEqual(
                    name,
                    path.parent.name,
                    f"{rel}: name '{name}' がディレクトリ名と一致しない (配布の不変条件)",
                )
                self.assertLessEqual(
                    len(name), MAX_NAME_LENGTH, f"{rel}: name が {MAX_NAME_LENGTH} 文字超"
                )
                self.assertRegex(
                    name,
                    NAME_PATTERN,
                    f"{rel}: name は小文字英数字とハイフンのみ",
                )

                description = extract_description(frontmatter)
                self.assertIsNotNone(description, f"{rel}: description がない")
                assert description is not None
                self.assertGreater(len(description), 0, f"{rel}: description が空")
                self.assertLessEqual(
                    len(description),
                    MAX_DESCRIPTION_LENGTH,
                    (
                        f"{rel}: description が {len(description)} 文字で上限"
                        f" {MAX_DESCRIPTION_LENGTH} を超過 (他エージェントの skill loader"
                        " が読み込みに失敗する)"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
