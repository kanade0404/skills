#!/usr/bin/env python3
"""Mutation smoke for test-mutation-gate (Phase 2).

Injects a small, bounded number of "safe" textual mutations into a single
implementation file, re-runs a caller-supplied test command once per
mutation, and reports whether the test suite actually goes RED (mutation
caught) or stays GREEN (mutant survived -> the test suite has a detection
gap around that line).

This is a *smoke* check, not a full mutation-testing tool (Stryker/mutmut
equivalents run every operator against every AST node and compute a
whole-suite mutation score; this script is intentionally line/regex based
and only touches the one impl file passed on the CLI - see
references/mutation-recipes.md for the language coverage and the
seams this approach cannot mutate).

Mutation kinds (regex-based, stdlib only):
  (a) bool-flip        True<->False / true<->false
  (b) comparison-flip  ==<->!=, <->=  , >->=  , <=->< , >=->>
  (c) off-by-one       an integer literal immediately adjacent to a
                        comparison operator, N -> N+1

Safety:
  - The impl file is always restored from a tempfile backup, via try/finally
    AND a SIGTERM/SIGINT/SIGHUP handler (SIGTERM's default disposition does
    not raise a catchable Python exception, so try/finally alone is not
    enough to survive it).
  - Exactly one mutation is on disk at a time; the file is restored to the
    pristine backup before the next mutation is applied and again before
    this process exits.
  - After the last restore, the impl file's bytes are compared against the
    backup; a mismatch is a hard error (exit 2), never a silent partial
    mutation left behind.

Known limitation (documented in notes, always): string/comment detection is
line-based and regex-driven, not a real tokenizer/AST. Multi-line
constructs - Python triple-quoted strings, C-style block comments spanning
several lines - are NOT masked and could theoretically be mutated inside.
See references/mutation-recipes.md for the fallback (extract a pure
function so the seam becomes single-line/testable) and
references/waiver-fallback.md for when mutation truly cannot apply.

Exit codes: PASS=0, BLOCK=1, SKIP=0, error=2.
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

BOOL_RE = re.compile(r"\b(True|False|true|false)\b")
BOOL_FLIP = {"True": "False", "False": "True", "true": "false", "false": "true"}

# Longest-alternative-first so "==", "!=", "<=", ">=" are consumed before the
# single-char "<"/">" alternatives are tried at the same position. The
# lookarounds on the single-char forms avoid mutating Go's "<-" channel
# operator and "->" return-type arrows - a known regex-vs-AST gap, not a
# full fix (see references/mutation-recipes.md).
COMPARISON_RE = re.compile(r"==|!=|<=|>=|<(?!-)|(?<!-)>")
COMPARISON_FLIP = {"==": "!=", "!=": "==", "<=": "<", ">=": ">", "<": "<=", ">": ">="}

INT_RIGHT_RE = re.compile(r"\s*(-?\d+)")
INT_LEFT_RE = re.compile(r"(-?\d+)\s*$")

# Generic, cross-language markers that a non-zero exit was a syntax/parse
# failure caused by the mutation breaking the file, rather than a real
# assertion catching the behavioral change. Still counted as "caught" (the
# gate cares about non-zero vs. zero), but called out separately in notes
# per the spec ("ビルドエラーは caught と区別され notes に記録").
SYNTAX_ERROR_MARKERS_RE = re.compile(
    r"SyntaxError|IndentationError|TabError|unexpected token|parse error|"
    r"syntax error|cannot find symbol|expected expression",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Line masking (string/comment detection, single-line only - see module
# docstring "Known limitation")
# ---------------------------------------------------------------------------

def comment_prefixes_for(path):
    """Return the comment-start token(s) to treat as "rest of line is a
    comment" for this file's extension. Unknown extensions get both '#' and
    '//' as a conservative superset (masks more, never mutates less safely)."""
    ext = Path(path).suffix.lower()
    if ext in (".py", ".rb", ".sh", ".bash", ".yml", ".yaml"):
        return ("#",)
    if ext in (".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".c", ".cc", ".cpp", ".rs"):
        return ("//",)
    return ("#", "//")


def mask_line(line, comment_prefixes):
    """Return a same-length copy of `line` where string-literal interiors
    become 'x' and comment text becomes '#', so mutation regexes never match
    inside a string or comment. Quote/comment delimiters and all other
    characters keep their original position and value, so match spans found
    on the masked line can be applied directly to the original line.

    Single-line only: a string or comment that started on a previous line
    (Python triple-quoted strings, C-style /* */ spanning lines) is NOT
    tracked across lines - this is the documented regex-vs-AST limitation.
    """
    chars = list(line)
    n = len(chars)
    in_string = False
    quote_char = None
    i = 0
    while i < n:
        ch = chars[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                chars[i] = "x"
                chars[i + 1] = "x"
                i += 2
                continue
            if ch == quote_char:
                in_string = False
            else:
                chars[i] = "x"
            i += 1
            continue
        if any(line[i:i + len(p)] == p for p in comment_prefixes):
            for j in range(i, n):
                chars[j] = "#"
            break
        if ch in ("'", '"', "`"):
            in_string = True
            quote_char = ch
            i += 1
            continue
        i += 1
    return "".join(chars)


# ---------------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------------

def find_adjacent_int(masked_line, op_start, op_end):
    """Look for an integer literal immediately adjacent (modulo whitespace)
    to a comparison operator at masked_line[op_start:op_end]. Right side is
    preferred; falls back to the left side. Returns (start, end, digits) or
    None. Works off the masked line so a digit-looking substring inside a
    string literal never becomes a candidate."""
    m = INT_RIGHT_RE.match(masked_line, op_end)
    if m:
        return m.start(1), m.end(1), m.group(1)
    m = INT_LEFT_RE.search(masked_line[:op_start])
    if m:
        return m.start(1), m.end(1), m.group(1)
    return None


def discover_candidates(lines, comment_prefixes):
    """Scan every added-nothing (this is a whole-file scan, not a diff) line
    of `lines` (as returned by readlines(), terminators included) and return
    an ordered, deduplicated list of mutation candidate dicts:
    {"line": 1-based int, "kind": ..., "col": 0-based int, "before": ..., "after": ...}
    """
    candidates = []
    seen = set()
    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        masked = mask_line(line, comment_prefixes)

        for m in BOOL_RE.finditer(masked):
            key = (lineno, "bool-flip", m.start())
            if key in seen:
                continue
            seen.add(key)
            old = m.group(1)
            candidates.append(
                {
                    "line": lineno,
                    "kind": "bool-flip",
                    "col": m.start(),
                    "before": old,
                    "after": BOOL_FLIP[old],
                }
            )

        for m in COMPARISON_RE.finditer(masked):
            old_op = m.group(0)
            key = (lineno, "comparison-flip", m.start())
            if key not in seen:
                seen.add(key)
                candidates.append(
                    {
                        "line": lineno,
                        "kind": "comparison-flip",
                        "col": m.start(),
                        "before": old_op,
                        "after": COMPARISON_FLIP[old_op],
                    }
                )

            adjacent = find_adjacent_int(masked, m.start(), m.end())
            if adjacent:
                val_start, val_end, digits = adjacent
                key2 = (lineno, "off-by-one", val_start)
                if key2 not in seen:
                    seen.add(key2)
                    candidates.append(
                        {
                            "line": lineno,
                            "kind": "off-by-one",
                            "col": val_start,
                            "before": digits,
                            "after": str(int(digits) + 1),
                        }
                    )

    return candidates


# ---------------------------------------------------------------------------
# Mutation application
# ---------------------------------------------------------------------------

def split_line_ending(raw):
    if raw.endswith("\r\n"):
        return raw[:-2], "\r\n"
    if raw.endswith("\n"):
        return raw[:-1], "\n"
    if raw.endswith("\r"):
        return raw[:-1], "\r"
    return raw, ""


def apply_candidate(original_lines, candidate):
    """Return a new list of lines with exactly one candidate mutation
    applied to the pristine `original_lines` (never accumulates mutations)."""
    idx = candidate["line"] - 1
    body, ending = split_line_ending(original_lines[idx])
    col = candidate["col"]
    before = candidate["before"]
    after = candidate["after"]
    actual = body[col:col + len(before)]
    if actual != before:
        raise RuntimeError(
            "mutation position mismatch at line {}: expected {!r}, found {!r} "
            "(file changed between discovery and application?)".format(
                candidate["line"], before, actual
            )
        )
    new_body = body[:col] + after + body[col + len(before):]
    mutated = list(original_lines)
    mutated[idx] = new_body + ending
    return mutated


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

class Backup:
    def __init__(self, impl_path):
        self.impl_path = Path(impl_path)
        fd, self.backup_path = tempfile.mkstemp(prefix="mutate_and_run-", suffix=".bak")
        with os.fdopen(fd, "wb") as f:
            f.write(self.impl_path.read_bytes())

    def restore(self):
        shutil.copyfile(self.backup_path, self.impl_path)

    def matches_current(self):
        return self.impl_path.read_bytes() == Path(self.backup_path).read_bytes()

    def cleanup(self):
        try:
            Path(self.backup_path).unlink()
        except OSError:
            pass


def install_signal_restorer(backup):
    """Install handlers so SIGTERM/SIGINT/SIGHUP restore the impl file before
    the process dies. SIGINT already raises KeyboardInterrupt (caught by the
    surrounding try/finally), but SIGTERM's default disposition terminates
    the process immediately without running Python cleanup code, so it needs
    an explicit handler."""

    def _handler(signum, _frame):
        try:
            backup.restore()
        finally:
            sys.exit(128 + signum)

    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass  # e.g. not the main thread - best-effort only


# ---------------------------------------------------------------------------
# Test execution
# ---------------------------------------------------------------------------

def run_test_cmd(test_cmd, timeout_sec):
    """Run test_cmd via the shell. Returns (returncode, stderr_text).
    A timeout is treated as a caught mutation (the run did not cleanly
    finish GREEN) with a synthetic returncode of 124 (matches the common
    `timeout(1)` convention) and is called out in notes by the caller."""
    try:
        proc = subprocess.run(
            test_cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
        )
        return proc.returncode, proc.stderr.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        stderr = ""
        if exc.stderr:
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr)
        return 124, stderr


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Mutation smoke for test-mutation-gate")
    parser.add_argument("--impl-file", required=True, help="implementation file to mutate")
    parser.add_argument("--test-cmd", required=True, help="shell command that runs the test(s)")
    parser.add_argument("--max-mutations", type=int, default=3, help="max mutations to try (default: 3)")
    parser.add_argument("--timeout-sec", type=int, default=120, help="per-mutation test-cmd timeout (default: 120)")
    args = parser.parse_args(argv)

    impl_path = Path(args.impl_file)
    if not impl_path.is_file():
        print(
            "mutate_and_run: impl file not found: {}".format(args.impl_file),
            file=sys.stderr,
        )
        return 2

    notes = [
        "string/comment detection is line-based (regex), not a tokenizer or AST; "
        "multi-line strings (e.g. Python triple-quoted docstrings) and block "
        "comments spanning multiple lines are not masked and could theoretically "
        "be mutated inside - see references/mutation-recipes.md for the fallback."
    ]

    try:
        backup = Backup(impl_path)
    except OSError as exc:
        print("mutate_and_run: failed to back up impl file: {}".format(exc), file=sys.stderr)
        return 2

    install_signal_restorer(backup)

    try:
        with open(impl_path, "r", encoding="utf-8", newline="") as f:
            original_lines = f.readlines()

        comment_prefixes = comment_prefixes_for(impl_path)
        all_candidates = discover_candidates(original_lines, comment_prefixes)
        selected = all_candidates[: args.max_mutations]

        if not selected:
            notes.append(
                "no mutation candidates found (no bool literal, comparison operator, "
                "or comparison-adjacent integer literal outside strings/comments) - "
                "treat this as a signal to consider a waiver "
                "(references/waiver-fallback.md) if this file is a seam that should "
                "have mutable logic."
            )
            result = {
                "version": 1,
                "verdict": "SKIP",
                "mutations_total": 0,
                "caught": 0,
                "survived": [],
                "notes": notes,
            }
            print(json.dumps(result, ensure_ascii=False))
            return 0

        survived = []
        caught = 0
        for candidate in selected:
            try:
                mutated_lines = apply_candidate(original_lines, candidate)
                with open(impl_path, "w", encoding="utf-8", newline="") as f:
                    f.writelines(mutated_lines)

                returncode, stderr_text = run_test_cmd(args.test_cmd, args.timeout_sec)
            finally:
                backup.restore()

            if returncode == 0:
                survived.append(
                    {
                        "line": candidate["line"],
                        "kind": candidate["kind"],
                        "before": candidate["before"],
                        "after": candidate["after"],
                    }
                )
            else:
                caught += 1
                if returncode == 124:
                    notes.append(
                        "line {} ({}): test-cmd timed out after {}s - counted as "
                        "caught, but this may indicate the mutation caused a hang "
                        "rather than a real test failure".format(
                            candidate["line"], candidate["kind"], args.timeout_sec
                        )
                    )
                elif SYNTAX_ERROR_MARKERS_RE.search(stderr_text):
                    notes.append(
                        "line {} ({}): non-zero exit looks like a syntax/build error "
                        "(not a real assertion failure) - counted as caught, but the "
                        "regex-based mutation may have produced invalid code rather "
                        "than a legitimately mutated behavior".format(
                            candidate["line"], candidate["kind"]
                        )
                    )

        verdict = "BLOCK" if survived else "PASS"
        result = {
            "version": 1,
            "verdict": verdict,
            "mutations_total": len(selected),
            "caught": caught,
            "survived": survived,
            "notes": notes,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 1 if verdict == "BLOCK" else 0

    except Exception as exc:  # noqa: BLE001 - never leak a bare traceback; always restore first
        try:
            backup.restore()
        except OSError:
            pass
        print("mutate_and_run: unexpected error: {}".format(exc), file=sys.stderr)
        return 2

    finally:
        try:
            backup.restore()
        except OSError as exc:
            print(
                "mutate_and_run: FAILED to restore impl file from backup: {}".format(exc),
                file=sys.stderr,
            )
            sys.exit(2)
        if not backup.matches_current():
            print(
                "mutate_and_run: impl file does not match backup after restore - "
                "refusing to exit cleanly, manual recovery needed. backup kept at: "
                "{}".format(backup.backup_path),
                file=sys.stderr,
            )
            sys.exit(2)
        backup.cleanup()


if __name__ == "__main__":
    sys.exit(main())
