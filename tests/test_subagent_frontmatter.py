"""subagents/*.md の frontmatter 不変条件を機械検証する (stdlib unittest).

`subagents/` は `scripts/rulesync-sync.mjs` の生成対象ではなく、consumer が
`rulesync fetch --features subagents` で直接取り込む。したがって frontmatter の
誤りは **consumer が取り込むまで誰も検出しない**。本テストがその sensor を担う。

とくに致命的なのが `model` / `effort` / `tools` をトップレベルに書く誤り。
rulesync の `fromRulesyncSubagent` は `claudecode:` セクションの下しか読まないため、
トップレベルに書いた指定は **エラーにならず、ただ転写されない** (silent failure)。
配布物としては「動くが指定が効いていない」状態になる。

加えて rulesync 側の subagent schema は `model` / `effort` を単なる
`z.optional(z.string())` としており enum 検証を持たない — `sonet` / `hgih` のような
typo もそのまま通る。**その検証は本テストが担う**、というのが `claudecode.model` /
`claudecode.effort` の値チェックの意図である。

注: description の長さ上限は検査しない — skill の 1024 制約 (tests/test_skill_frontmatter.py)
が subagent に適用されるかは未確認のため、確認が取れるまで意図的に対象外とする。

検証する不変条件:
- `subagents/*.md` (README.md を除く) に frontmatter が存在し、YAML として妥当に読める
- `name` がファイル名の stem と一致する
- `name` が `^[a-z0-9]+(-[a-z0-9]+)*$` に一致し、`:` を含まない
- `description` が存在し空でない
- `model` / `effort` / `tools` がトップレベルに存在しない (silent failure の検出)
- `claudecode` が mapping として存在し、その下の `tools` が**非空文字列の非空リスト**である
  — 欠けたり `tools:` (null) / `[]` だったりすると呼出側の既定ツールを継承し、README
  「ツール権限の方針」が構造で担保している read-only 契約がエラーも警告も無しに失効する
  (キーの存在だけを見ると素通しする)。`model` / `effort` は継承が正当な選択なので必須に
  しない (`problem-solver.md` が両方を意図的に省いている)
- `claudecode.model` は alias (sonnet / opus / haiku / fable / inherit) かフル model id
- `claudecode.effort` は low / medium / high / xhigh / max のいずれか

依存: 本 repo のテストは stdlib のみで動く (CI は `python3 -m unittest discover` を
pip install 無しで実行し、既存テストも yaml を import していない)。そのため
frontmatter の解析は下の YAML サブセットパーサで行い、PyYAML は **入っていれば**
クロスチェックに使う (無ければ該当テストのみ skip)。

PyYAML が無い CI ではそのクロスチェックが skip されるため、**サブセットパーサ自身が
fail-closed であること**が唯一の防波堤になる。未閉じ引用符・閉じられていない flow
sequence・閉じ記号の後ろの余分な文字は、素通しせず `FrontmatterError` にする
(PR #111 レビュー指摘)。YAML 全仕様への適合は本パーサの目標ではない — 読めない構文は
黙って文字列にせず落とす、という一方向の安全側倒しだけを保証する。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

try:  # PyYAML はこの repo の依存ではない (あればクロスチェックに使う)
    import yaml as _yaml
except ModuleNotFoundError:  # pragma: no cover - 環境依存
    _yaml = None

REPO_ROOT = Path(__file__).resolve().parents[1]
SUBAGENTS_DIR = REPO_ROOT / "subagents"

NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64

# rulesync schema は enum を持たないので、許容値の定義はここが唯一の砦
MODEL_ALIASES = frozenset({"sonnet", "opus", "haiku", "fable", "inherit"})
# フル model id 例: claude-opus-4-5-20251101 / us.anthropic.claude-3-5-haiku-20241022-v1:0
FULL_MODEL_ID_RE = re.compile(r"^(?:[a-z0-9][a-z0-9._-]*)?claude[a-z0-9._:-]*$")
EFFORT_VALUES = frozenset({"low", "medium", "high", "xhigh", "max"})
# claudecode: の下に置かないと rulesync が転写しないキー
CLAUDECODE_ONLY_KEYS = ("model", "effort", "tools")

FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.S)
_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_][A-Za-z0-9_.-]*):(?:\s+(?P<value>.*))?$")
_BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?$")


class FrontmatterError(ValueError):
    """frontmatter が YAML (本 repo が使うサブセット) として読めない."""


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_skippable(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _next_meaningful(lines: list[str], i: int) -> int:
    while i < len(lines) and _is_skippable(lines[i]):
        i += 1
    return i


def _require_only_comment_after(text: str, end: int, raw: str) -> None:
    """閉じ記号 (`text[end]`) の後ろに来てよいのはコメントだけ."""
    rest = text[end + 1 :].strip()
    if rest and not rest.startswith("#"):
        raise FrontmatterError(f"閉じた scalar の後ろに余分な文字がある: {raw!r}")


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("["):
        end = text.rfind("]")
        if end == -1:
            raise FrontmatterError(f"flow sequence の ']' がない: {raw!r}")
        _require_only_comment_after(text, end, raw)
        inner = text[1:end].strip()
        if not inner:
            return []
        # flow sequence の要素にカンマを含む引用文字列は本サブセットの対象外
        return [_parse_scalar(part) for part in inner.split(",")]
    if text[:1] in ('"', "'"):
        quote = text[0]
        end = text.find(quote, 1)
        if end == -1:
            raise FrontmatterError(f"引用符が閉じていない: {raw!r}")
        _require_only_comment_after(text, end, raw)
        return text[1:end]
    return re.split(r"\s+#", text, maxsplit=1)[0].strip()


def _consume_block_scalar(
    lines: list[str], i: int, parent_indent: int, style: str
) -> tuple[str, int]:
    body: list[str] = []
    while i < len(lines):
        line = lines[i]
        if line.strip() and _indent_of(line) <= parent_indent:
            break
        body.append(line)
        i += 1

    content_indent = next((_indent_of(line) for line in body if line.strip()), None)
    if content_indent is None:
        return "", i
    stripped = [line[content_indent:] if line.strip() else "" for line in body]

    if style[0] == "|":
        text = "\n".join(stripped)
    else:  # folded: 段落内は空白で連結、空行は改行
        paragraphs: list[str] = []
        current: list[str] = []
        for line in stripped:
            if line:
                current.append(line)
            else:
                paragraphs.append(" ".join(current))
                current = []
        paragraphs.append(" ".join(current))
        text = "\n".join(paragraphs)

    text = text.rstrip("\n")
    if text and not style.endswith("-"):
        text += "\n"
    return text, i


def _parse_block(lines: list[str], i: int, indent: int) -> tuple[Any, int]:
    result: Any = None
    while i < len(lines):
        if _is_skippable(lines[i]):
            i += 1
            continue
        current_indent = _indent_of(lines[i])
        if current_indent < indent:
            break
        if current_indent > indent:
            raise FrontmatterError(f"予期しないインデント: {lines[i]!r}")

        body = lines[i].strip()

        if body.startswith("- ") or body == "-":
            if result is None:
                result = []
            if not isinstance(result, list):
                raise FrontmatterError(f"mapping の中に sequence 要素: {lines[i]!r}")
            result.append(_parse_scalar(body[1:]))
            i += 1
            continue

        match = _KEY_RE.match(body)
        if match is None:
            raise FrontmatterError(f"key: value として読めない行: {lines[i]!r}")
        if result is None:
            result = {}
        if not isinstance(result, dict):
            raise FrontmatterError(f"sequence の中に mapping キー: {lines[i]!r}")

        key = match.group("key")
        if key in result:
            raise FrontmatterError(f"キーが重複している: {key!r}")
        raw_value = (match.group("value") or "").strip()

        if _BLOCK_SCALAR_RE.match(raw_value):
            result[key], i = _consume_block_scalar(lines, i + 1, indent, raw_value)
            continue
        if raw_value:
            result[key] = _parse_scalar(raw_value)
            i += 1
            continue

        # 値が空 = ネストしたブロック (または null)
        child_start = _next_meaningful(lines, i + 1)
        if child_start >= len(lines):
            result[key] = None
            i = child_start
            continue
        child_indent = _indent_of(lines[child_start])
        is_sequence_at_same_indent = (
            child_indent == indent and lines[child_start].strip().startswith("-")
        )
        if child_indent > indent or is_sequence_at_same_indent:
            result[key], i = _parse_block(lines, child_start, child_indent)
        else:
            result[key] = None
            i = child_start
    return ({} if result is None else result), i


def parse_frontmatter(text: str) -> dict[str, Any]:
    """先頭の YAML frontmatter を dict にする. 読めなければ FrontmatterError."""
    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise FrontmatterError("frontmatter がない")
    parsed, _ = _parse_block(match.group(1).splitlines(), 0, 0)
    if not isinstance(parsed, dict):
        raise FrontmatterError("frontmatter のトップレベルが mapping でない")
    return parsed


def subagent_files() -> list[Path]:
    return sorted(p for p in SUBAGENTS_DIR.glob("*.md") if p.name != "README.md")


def frontmatter_errors(stem: str, data: dict[str, Any]) -> list[str]:
    """frontmatter の不変条件違反を human-readable な文字列で列挙する."""
    errors: list[str] = []

    name = data.get("name")
    if not isinstance(name, str) or not name:
        errors.append("name がない")
    else:
        if name != stem:
            errors.append(f"name '{name}' がファイル名の stem '{stem}' と一致しない")
        if ":" in name:
            errors.append(f"name '{name}' に ':' が含まれる (agent 名として不正)")
        if len(name) > MAX_NAME_LENGTH:
            errors.append(f"name が {MAX_NAME_LENGTH} 文字超")
        if not NAME_PATTERN.match(name):
            errors.append(f"name '{name}' は小文字英数字とハイフンのみ")

    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("description がない/空")

    for key in CLAUDECODE_ONLY_KEYS:
        if key in data:
            errors.append(
                f"'{key}' がトップレベルにある — rulesync は claudecode: の下しか読まず、"
                "エラーも出さずに転写されない"
            )

    claudecode = data.get("claudecode")
    if not isinstance(claudecode, dict):
        errors.append("claudecode: がない、または mapping でない")
    else:
        # `tools` だけは必須。欠けると呼出側の既定ツールをそのまま継承し、
        # README「ツール権限の方針」が構造で担保している read-only 契約が
        # エラーも警告も無しに失効する。`model` / `effort` は継承が正当な
        # 選択なので必須化しない (`problem-solver.md` が実例)。
        tools = claudecode.get("tools")
        if not isinstance(tools, list) or not tools or not all(
            isinstance(tool, str) and tool.strip() for tool in tools
        ):
            errors.append(
                "claudecode.tools がない / 空 / 非空文字列のリストでない — "
                "呼出側の既定ツールを継承して read-only 契約が黙って失効するか、"
                "consumer 側の rulesync generate が落ちる"
            )
        model = claudecode.get("model")
        if model is not None and not (
            isinstance(model, str)
            and (model in MODEL_ALIASES or FULL_MODEL_ID_RE.match(model))
        ):
            errors.append(
                f"claudecode.model '{model}' が不正 "
                f"(alias: {sorted(MODEL_ALIASES)} かフル model id)"
            )
        effort = claudecode.get("effort")
        if effort is not None and effort not in EFFORT_VALUES:
            errors.append(
                f"claudecode.effort '{effort}' が不正 (許容: {sorted(EFFORT_VALUES)})"
            )
    return errors


class TestSubagentFrontmatter(unittest.TestCase):
    def test_catalog_is_not_empty(self) -> None:
        self.assertGreater(len(subagent_files()), 0)

    def test_frontmatter_invariants(self) -> None:
        for path in subagent_files():
            rel = path.relative_to(REPO_ROOT).as_posix()
            with self.subTest(subagent=rel):
                try:
                    data = parse_frontmatter(path.read_text(encoding="utf-8"))
                except FrontmatterError as exc:
                    self.fail(f"{rel}: frontmatter を YAML として読めない — {exc}")
                errors = frontmatter_errors(path.stem, data)
                self.assertEqual(errors, [], f"{rel}: " + " / ".join(errors))

    @unittest.skipIf(_yaml is None, "PyYAML なし (本 repo の依存ではない)")
    def test_frontmatter_parses_as_real_yaml(self) -> None:
        for path in subagent_files():
            rel = path.relative_to(REPO_ROOT).as_posix()
            with self.subTest(subagent=rel):
                match = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
                self.assertIsNotNone(match, f"{rel}: frontmatter がない")
                assert match is not None
                try:
                    loaded = _yaml.safe_load(match.group(1))
                except Exception as exc:  # noqa: BLE001 - YAMLError 以外も落とす
                    self.fail(f"{rel}: PyYAML で読めない — {exc}")
                self.assertIsInstance(loaded, dict, f"{rel}: トップレベルが mapping でない")
                self.assertEqual(
                    sorted(loaded),
                    sorted(parse_frontmatter(path.read_text(encoding="utf-8"))),
                    f"{rel}: サブセットパーサと PyYAML でトップレベルキーが食い違う",
                )


VALID_FRONTMATTER = """---
name: sample-agent
description: >
  Use this agent for the sample task.

  Second paragraph.
targets: ["*"]
claudecode:
  model: sonnet
  effort: medium
  tools:
    - Read
    - Grep
---

# body
"""


class TestParseFrontmatter(unittest.TestCase):
    def test_parses_nested_mapping_and_sequence(self) -> None:
        data = parse_frontmatter(VALID_FRONTMATTER)
        self.assertEqual(data["name"], "sample-agent")
        self.assertEqual(data["targets"], ["*"])
        self.assertEqual(data["claudecode"]["model"], "sonnet")
        self.assertEqual(data["claudecode"]["tools"], ["Read", "Grep"])

    def test_folded_block_scalar_joins_lines_and_keeps_paragraphs(self) -> None:
        data = parse_frontmatter(VALID_FRONTMATTER)
        self.assertEqual(
            data["description"],
            "Use this agent for the sample task.\nSecond paragraph.\n",
        )

    def test_sequence_at_same_indent_as_key(self) -> None:
        text = "---\nname: a\ntools:\n- Read\n- Glob\n---\n"
        self.assertEqual(parse_frontmatter(text)["tools"], ["Read", "Glob"])

    def test_missing_frontmatter_raises(self) -> None:
        with self.assertRaises(FrontmatterError):
            parse_frontmatter("# just a heading\n")

    def test_duplicate_key_raises(self) -> None:
        with self.assertRaises(FrontmatterError):
            parse_frontmatter("---\nname: a\nname: b\n---\n")

    def test_unparsable_line_raises(self) -> None:
        with self.assertRaises(FrontmatterError):
            parse_frontmatter("---\nname: a\nthis is not yaml\n---\n")

    def test_null_value_as_last_line_of_document(self) -> None:
        # `tools:` (空値) がドキュメントの最終行 = _next_meaningful(lines, i+1) が
        # 呼ばれる時点で i+1 == len(lines)。_next_meaningful の `i < len(lines)` が
        # `i <= len(lines)` に変異すると、ループ 1 回目で range 外の
        # lines[len(lines)] を参照して IndexError になる (この境界でしか検出できない)。
        data = parse_frontmatter("---\nname: a\ntools:\n---\n")
        self.assertIsNone(data["tools"])

    def test_block_scalar_running_to_end_of_document(self) -> None:
        # `_consume_block_scalar` の `i < len(lines)` が `<=` に変異すると、block
        # scalar がドキュメント末尾まで続く場合にだけ lines[len(lines)] を参照して
        # IndexError になる。後続キーがある VALID_FRONTMATTER では早く break する
        # ため、この境界でしか検出できない。
        text = "---\nname: a\ndescription: |\n  line one\n  line two\n---\n"
        self.assertEqual(parse_frontmatter(text)["description"], "line one\nline two\n")

    def test_two_char_quoted_scalar_is_empty_string(self) -> None:
        # `_parse_scalar` の `len(text) >= 2` は、ちょうど 2 文字 (引用符 2 つだけ =
        # 空文字列) の境界でのみ `> 2` / `>= 3` との違いが現れる。3 文字以上では
        # いずれの条件も真になり区別できない。
        self.assertEqual(_parse_scalar('""'), "")
        self.assertEqual(_parse_scalar("''"), "")

    def test_unterminated_quoted_scalar_raises(self) -> None:
        # PyYAML なしの CI では本サブセットパーサが唯一の検査。未閉じ引用符を
        # 素通しすると consumer 側で frontmatter が読めなくなるまで検出されない。
        # 原因を名指しする形 (「閉じていない」) であることまで固定する — 後段の
        # `_require_only_comment_after` も raise はするが「余分な文字」と誤診する。
        for raw in ('"unterminated', "'unterminated", '"', "'"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(FrontmatterError, "引用符が閉じていない"):
                    _parse_scalar(raw)

    def test_unterminated_quoted_scalar_in_document_raises(self) -> None:
        with self.assertRaises(FrontmatterError):
            parse_frontmatter('---\nname: a\ndescription: "unterminated\n---\n')

    def test_unmatched_flow_sequence_raises(self) -> None:
        # 引用符と同じく、原因を名指しする形であることまで固定する。
        for raw in ("[Read, Grep", "["):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(FrontmatterError, "']' がない"):
                    _parse_scalar(raw)

    def test_flow_sequence_with_trailing_garbage_raises(self) -> None:
        with self.assertRaisesRegex(FrontmatterError, "余分な文字"):
            _parse_scalar("[Read] extra")

    def test_unmatched_flow_sequence_in_document_raises(self) -> None:
        with self.assertRaises(FrontmatterError):
            parse_frontmatter('---\nname: a\ntargets: ["*"\n---\n')

    def test_quoted_scalar_with_trailing_comment_keeps_value(self) -> None:
        for raw in ('"a"  # note', "'a' # note"):
            with self.subTest(raw=raw):
                self.assertEqual(_parse_scalar(raw), "a")

    def test_quoted_scalar_with_trailing_garbage_raises(self) -> None:
        # 終端引用符の後ろに来てよいのはコメントだけ。それ以外は YAML として不正。
        with self.assertRaises(FrontmatterError):
            _parse_scalar('"a" trailing')

    def test_flow_sequence_with_trailing_comment(self) -> None:
        self.assertEqual(_parse_scalar('["*"]  # all targets'), ["*"])


class TestFrontmatterErrors(unittest.TestCase):
    """検査自体が no-op に退化していないことを確かめる (負の証拠)."""

    def setUp(self) -> None:
        self.valid = parse_frontmatter(VALID_FRONTMATTER)

    def test_valid_document_has_no_errors(self) -> None:
        self.assertEqual(frontmatter_errors("sample-agent", self.valid), [])

    def test_top_level_model_is_rejected(self) -> None:
        data = dict(self.valid, model="opus")
        self.assertTrue(
            any("トップレベル" in e for e in frontmatter_errors("sample-agent", data))
        )

    def test_top_level_effort_and_tools_are_rejected(self) -> None:
        for key, value in (("effort", "high"), ("tools", ["Read"])):
            with self.subTest(key=key):
                data = dict(self.valid, **{key: value})
                self.assertTrue(
                    any(
                        key in e and "トップレベル" in e
                        for e in frontmatter_errors("sample-agent", data)
                    )
                )

    def test_name_must_match_file_stem(self) -> None:
        self.assertTrue(
            any("stem" in e for e in frontmatter_errors("other-agent", self.valid))
        )

    def test_name_with_colon_is_rejected(self) -> None:
        data = dict(self.valid, name="plugin:agent")
        self.assertTrue(any("':'" in e for e in frontmatter_errors("plugin:agent", data)))

    def test_missing_description_is_rejected(self) -> None:
        data = {k: v for k, v in self.valid.items() if k != "description"}
        self.assertIn("description がない/空", frontmatter_errors("sample-agent", data))

    def test_model_typo_is_rejected(self) -> None:
        data = dict(self.valid, claudecode=dict(self.valid["claudecode"], model="sonet"))
        self.assertTrue(
            any("claudecode.model" in e for e in frontmatter_errors("sample-agent", data))
        )

    def test_full_model_id_is_accepted(self) -> None:
        for model in ("claude-opus-4-5-20251101", "us.anthropic.claude-3-5-haiku-v1:0"):
            with self.subTest(model=model):
                data = dict(
                    self.valid, claudecode=dict(self.valid["claudecode"], model=model)
                )
                self.assertEqual(frontmatter_errors("sample-agent", data), [])

    def test_effort_typo_is_rejected(self) -> None:
        data = dict(self.valid, claudecode=dict(self.valid["claudecode"], effort="hgih"))
        self.assertTrue(
            any("claudecode.effort" in e for e in frontmatter_errors("sample-agent", data))
        )

    def test_missing_claudecode_is_rejected(self) -> None:
        # `claudecode` ごと欠落すると tools の指定も消える = read-only 契約が
        # 構造的に崩れる (subagents/README.md「ツール権限の方針」)。
        data = {k: v for k, v in self.valid.items() if k != "claudecode"}
        self.assertTrue(
            any("claudecode" in e for e in frontmatter_errors("sample-agent", data))
        )

    def test_missing_claudecode_tools_is_rejected(self) -> None:
        data = dict(
            self.valid,
            claudecode={
                k: v for k, v in self.valid["claudecode"].items() if k != "tools"
            },
        )
        self.assertTrue(
            any("claudecode.tools" in e for e in frontmatter_errors("sample-agent", data))
        )

    def test_unusable_claudecode_tools_is_rejected(self) -> None:
        # キーの存在だけを見ると `tools:` (null) や `tools: []` を通してしまう。
        # どちらも「ツールを渡さない」ではなく「既定を継承する」or rulesync の
        # 生成失敗になるので、read-only 契約の担保にならない。
        for tools in (None, [], "Read", ["Read", ""], ["Read", None]):
            with self.subTest(tools=tools):
                data = dict(self.valid, claudecode={"tools": tools})
                self.assertTrue(
                    any(
                        "claudecode.tools" in e
                        for e in frontmatter_errors("sample-agent", data)
                    )
                )

    def test_omitted_model_and_effort_are_allowed(self) -> None:
        # `problem-solver.md` は model / effort を意図的に省いて呼出側から継承する。
        # tools 必須化がこの正当なケースを巻き込まないことを固定する。
        data = dict(self.valid, claudecode={"tools": ["Read"]})
        self.assertEqual(frontmatter_errors("sample-agent", data), [])


if __name__ == "__main__":
    unittest.main()
