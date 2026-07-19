"""Tests for skills/test-mutation-gate/scripts/mutate_and_run.py (stdlib unittest, 依存なし).

スクリプトはパッケージではないため importlib でファイルパスからロードする
(tests/test_check_trigger_evals.py と同じ手法)。

Covers the TS/TSX generic-declaration-line false-positive fix for
comparison-flip candidates (issue #65 part 2): `function f<T>()`, `type
Box<T> = ...`, `interface Foo<T>`, `class C<T>` must not be treated as `<`/`>`
comparisons when `skip_generic_comparisons=True`, while real runtime
comparisons (`if (a < b)`) and `==`/`!=`/`<=`/`>=` on the same line must
still be discovered.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "test-mutation-gate"
    / "scripts"
    / "mutate_and_run.py"
)


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("mutate_and_run", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MOD = _load_module()


def discover(source: str, *, skip_generic_comparisons: bool = False, skip_type_alias_bools: bool = False):
    """Run discover_candidates() over `source` as if it were a TS/TSX file
    (matching the real call site's comment_prefixes/block_comment for
    .ts/.tsx: comment_prefixes_for -> ("//",), block_comment_markers_for ->
    ("/*", "*/")).
    """
    lines = source.splitlines(keepends=True)
    comment_prefixes = MOD.comment_prefixes_for("dummy.ts")
    block_comment = MOD.block_comment_markers_for("dummy.ts")
    return MOD.discover_candidates(
        lines,
        comment_prefixes,
        block_comment,
        skip_type_alias_bools=skip_type_alias_bools,
        skip_generic_comparisons=skip_generic_comparisons,
    )


def comparison_flip_candidates(candidates, before=None):
    out = [c for c in candidates if c["kind"] == "comparison-flip"]
    if before is not None:
        out = [c for c in out if c["before"] == before]
    return out


def off_by_one_candidates(candidates):
    return [c for c in candidates if c["kind"] == "off-by-one"]


class GenericDeclarationLineSkipTest(unittest.TestCase):
    """[new spec - must FAIL before the fix] Generic declaration lines must
    not yield single-char '<'/'>' comparison-flip candidates when
    skip_generic_comparisons=True."""

    def test_function_generic_no_angle_bracket_candidates(self):
        candidates = discover(
            "function identity<T>(x: T): T {\n", skip_generic_comparisons=True
        )
        angle = comparison_flip_candidates(candidates, "<") + comparison_flip_candidates(candidates, ">")
        self.assertEqual(angle, [], "expected no </> comparison-flip candidates on a generic function decl")

    def test_type_alias_generic_no_angle_bracket_candidates(self):
        candidates = discover(
            "export type Box<T> = { value: T };\n", skip_generic_comparisons=True
        )
        angle = comparison_flip_candidates(candidates, "<") + comparison_flip_candidates(candidates, ">")
        self.assertEqual(angle, [])

    def test_interface_generic_no_angle_bracket_candidates(self):
        candidates = discover("interface Repo<T> {\n", skip_generic_comparisons=True)
        angle = comparison_flip_candidates(candidates, "<") + comparison_flip_candidates(candidates, ">")
        self.assertEqual(angle, [])

    def test_default_export_class_generic_no_angle_bracket_candidates(self):
        candidates = discover(
            "export default class Container<T> {\n", skip_generic_comparisons=True
        )
        angle = comparison_flip_candidates(candidates, "<") + comparison_flip_candidates(candidates, ">")
        self.assertEqual(angle, [])

    def test_declare_function_generic_no_angle_bracket_candidates(self):
        candidates = discover(
            "export declare function make<T>(x: T): T;\n", skip_generic_comparisons=True
        )
        angle = comparison_flip_candidates(candidates, "<") + comparison_flip_candidates(candidates, ">")
        self.assertEqual(angle, [])

    def test_one_line_body_comparison_is_also_skipped_by_design(self):
        # Characterization of a deliberate trade-off, not a desired ideal:
        # the skip is line-level, so a real runtime comparison sharing the
        # declaration's line (one-line body) is swallowed too. Span-limited
        # skipping was rejected because it would re-admit annotation generics
        # (`x: Array<number>`) - the original false positives. See
        # discover_candidates' docstring and references/mutation-recipes.md;
        # if this test breaks because the heuristic got *smarter* (e.g.
        # AST-based), delete it along with those docs.
        candidates = discover(
            "function isPositive<T extends number>(x: T): boolean { return x > 0; }\n",
            skip_generic_comparisons=True,
        )
        angle = comparison_flip_candidates(candidates, "<") + comparison_flip_candidates(candidates, ">")
        self.assertEqual(angle, [], "line-level skip swallows the one-line body comparison (documented trade-off)")
        self.assertEqual(off_by_one_candidates(candidates), [])

    def test_generic_type_alias_no_off_by_one_on_type_argument(self):
        # `type Small<T> = LessThan<3>;` - the "3" sits immediately after the
        # skipped "<" match; skipping the whole comparison-flip/off-by-one
        # pair for that operator match is intended (type-position literal,
        # not a runtime comparison boundary).
        candidates = discover(
            "type Small<T> = LessThan<3>;\n", skip_generic_comparisons=True
        )
        off_by_one = off_by_one_candidates(candidates)
        self.assertEqual(
            off_by_one,
            [],
            "expected no off-by-one candidate for the type-argument literal on a generic decl line",
        )


class RegressionGuardTest(unittest.TestCase):
    """[regression guard - must PASS before AND after the fix]"""

    def test_runtime_if_comparison_still_found_even_with_skip_enabled(self):
        candidates = discover("if (a < b) {\n", skip_generic_comparisons=True)
        angle = comparison_flip_candidates(candidates, "<")
        self.assertEqual(len(angle), 1, "a real runtime '<' comparison must still be discovered")

    def test_equality_on_generic_decl_line_still_found(self):
        candidates = discover(
            "function f<T>(flag = x == y) {\n", skip_generic_comparisons=True
        )
        eq = comparison_flip_candidates(candidates, "==")
        self.assertEqual(len(eq), 1, "'==' on a generic decl line is not generic syntax and must still be a candidate")

    def test_skip_disabled_still_finds_angle_brackets_on_generic_looking_line(self):
        candidates = discover(
            "function identity<T>(x: T): T {\n", skip_generic_comparisons=False
        )
        angle = comparison_flip_candidates(candidates, "<") + comparison_flip_candidates(candidates, ">")
        self.assertEqual(
            len(angle),
            2,
            "with skip_generic_comparisons=False (non-TS caller), generic-looking lines behave as before",
        )

    def test_skip_type_alias_bools_behavior_unchanged(self):
        candidates = discover(
            "type Flag = true | false;\n", skip_type_alias_bools=True
        )
        bool_flips = [c for c in candidates if c["kind"] == "bool-flip"]
        self.assertEqual(bool_flips, [], "existing skip_type_alias_bools behavior must be unaffected by this change")


class TsHeuristicsNoteTest(unittest.TestCase):
    """main() must disclose the TS/TSX-only candidate exclusions in `notes`
    (references/mutation-recipes.md promises these limitations are never
    silently swallowed), so a SKIP with 0 candidates on a TS file is
    explainable by the reader."""

    def _run_main(self, suffix, source):
        with tempfile.TemporaryDirectory() as tmp:
            impl = Path(tmp) / f"impl{suffix}"
            impl.write_text(source, encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = MOD.main(["--impl-file", str(impl), "--test-cmd", "true"])
            return rc, json.loads(out.getvalue())

    def test_ts_file_discloses_heuristics_note(self):
        # Generic decl line + candidate-free body -> the TS-only exclusions
        # leave 0 candidates (SKIP); the note must say the exclusions were
        # active so the SKIP is attributable.
        rc, result = self._run_main(
            ".ts", "function identity<T>(x: T): T {\n  return x;\n}\n"
        )
        self.assertEqual(rc, 0, "SKIP is not an error exit")
        self.assertEqual(result["verdict"], "SKIP", "candidate-free TS file must SKIP")
        self.assertTrue(
            any("TS/TSX heuristics active" in n for n in result["notes"]),
            f"expected a TS/TSX heuristics disclosure note, got: {result['notes']}",
        )

    def test_non_ts_file_has_no_ts_heuristics_note(self):
        rc, result = self._run_main(".py", "def identity(x):\n    return x\n")
        self.assertEqual(rc, 0, "SKIP is not an error exit")
        self.assertEqual(result["verdict"], "SKIP", "candidate-free .py file must SKIP")
        self.assertFalse(
            any("TS/TSX heuristics active" in n for n in result["notes"]),
            "the TS/TSX disclosure note must not appear for non-TS files",
        )


if __name__ == "__main__":
    unittest.main()
