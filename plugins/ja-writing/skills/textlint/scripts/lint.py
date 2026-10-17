"""スキルから使う CLI.

使い方: python3 lint.py [--fix [--dry-run]] <ファイルかglob>...

- 引数だけ: 検査して指摘を表示する
- --fix: 自動で直せる指摘を直してファイルに書き戻し、残った指摘を表示する
- --fix --dry-run: 書き戻さずに差分を表示する

error の指摘が残れば終了コード 1 を返す。
"""

from __future__ import annotations

import difflib
import glob
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from textlint_runner import (
    LINTABLE_EXTENSIONS,
    SEVERITY_ERROR,
    Document,
    Report,
    TextlintError,
    choose_file_config,
    display_path,
    lint_documents,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_USAGE = "使い方: lint.py [--fix [--dry-run]] <ファイルかglob>...\n"
_SEVERITY_NAMES = {1: "warning", 2: "error", 3: "info"}


def expand(patterns: Sequence[str], cwd: Path) -> list[Path]:
    """ファイルと glob を、検査するファイルの一覧にする.

    Returns:
        対象の拡張子のファイルを重複なく並べたもの。
    """
    found: set[Path] = set()
    for pattern in patterns:
        # glob.glob の root_dir は 3.10 からなので、相対の glob は Path.glob で展開する。
        if not glob.has_magic(pattern):
            matches = [Path(pattern)]
        elif Path(pattern).is_absolute():
            matches = [Path(match) for match in glob.glob(pattern, recursive=True)]  # ruff: ignore[glob] - 絶対パスの glob
        else:
            matches = list(cwd.glob(pattern))
        found.update(match if match.is_absolute() else cwd / match for match in matches)
    return sorted(path for path in found if path.is_file() and path.suffix.lower() in LINTABLE_EXTENSIONS)


def format_report(report: Report, cwd: Path) -> str:
    """指摘を人が読む形にする.

    Returns:
        1行に1件の指摘と、同梱にないため無視した設定の名前を並べた文字列。
    """
    lines: list[str] = []
    for result in report.results:
        shown = display_path(result.file_path, cwd)
        lines.extend(
            f"{shown}:{m.line}:{m.column}  {_SEVERITY_NAMES.get(m.severity, m.severity)}  {m.message}  {m.rule_id}"
            for m in result.messages
        )
    lines.extend(f"同梱にないため無視した設定: {name}" for name in report.ignored)
    return "".join(f"{line}\n" for line in lines)


def _diff(before: str, after: str, name: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), name, name))


def _read(path: Path) -> Document | None:
    try:
        return Document(str(path), path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        _ = sys.stderr.write(f"ja-writing: {path} を UTF-8 として読めません: {error}\n")
        return None


def run(args: Sequence[str], env: Mapping[str, str], cwd: Path) -> int:
    """引数に従って検査か修正をする.

    Returns:
        error の指摘が残れば 1、なければ 0。引数の誤りや準備の失敗は 2。
    """
    fix = "--fix" in args
    dry_run = "--dry-run" in args
    patterns = [arg for arg in args if arg not in {"--fix", "--dry-run"}]
    if not patterns or (dry_run and not fix):
        _ = sys.stderr.write(_USAGE)
        return 2
    documents = [_read(path) for path in expand(patterns, cwd)]
    if None in documents:
        return 2
    readable = [document for document in documents if document is not None]
    try:
        report = lint_documents(readable, config=choose_file_config(cwd, env), env=env, fix=fix)
    except (TextlintError, OSError) as error:
        _ = sys.stderr.write(f"ja-writing: {error}\n")
        return 2
    for document, result in zip(readable, report.results):
        if result.output is None or result.output == document.text:
            continue
        if dry_run:
            _ = sys.stdout.write(_diff(document.text, result.output, display_path(document.file_path, cwd)))
        else:
            _ = Path(document.file_path).write_text(result.output, encoding="utf-8")
    _ = sys.stdout.write(format_report(report, cwd))
    _ = sys.stderr.write(f"(textlint の設定: {report.source})\n")
    errors = [m for result in report.results for m in result.messages if m.severity == SEVERITY_ERROR]
    return 1 if errors else 0


def main() -> int:
    """sys.argv・os.environ・カレントディレクトリで run を呼ぶ. テストしやすいよう、処理は run に置いている.

    Returns:
        run の終了コード。
    """
    return run(sys.argv[1:], os.environ, Path.cwd())


if __name__ == "__main__":
    sys.exit(main())
