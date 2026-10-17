"""書き込み後フック. Claude Code と Codex の PostToolUse のどちらでも動く.

書き換えたファイルを textlint にかけ、指摘をエージェントに返す。モードは TEXTLINT_HOOK_MODE
(Claude Code ではプラグインの hook_mode 設定)で選ぶ。

- block(既定): 指摘を返して直させる
- warn: 文脈として渡すだけ
- off: 何もしない

フックの失敗でエージェントの作業を止めないよう、どの経路でも終了コード 0 で終える。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from textlint_runner import (
    LINTABLE_EXTENSIONS,
    Document,
    Report,
    TextlintError,
    choose_file_config,
    lint_documents,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_PATCH_PATH = re.compile(r"^\*\*\* (?:Add File|Update File|Move to): (.+)$", re.MULTILINE)
KANA = re.compile(
    r"[\N{HIRAGANA LETTER SMALL A}-\N{HIRAGANA DIGRAPH YORI}\N{KATAKANA LETTER SMALL A}-\N{KATAKANA DIGRAPH KOTO}]"
)
# textlint の severity: 1 = warning, 2 = error, 3 = info。info の提案はフックでは扱わない。
REPORTED_SEVERITIES = frozenset({1, 2})
_INSTRUCTION = (
    "指摘された箇所を自然な日本語に書き直してください。指摘を消すために内容を削らないでください。"
    "誤検知と判断したものはそのまま残し、理由を伝えてください。"
)
_NOTHING = ("", "")


def as_dict(value: object) -> dict[str, object]:
    """JSON の値をオブジェクトとして扱う.

    Returns:
        オブジェクトならキーを文字列にしたもの。それ以外は空。
    """
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def parse_json(text: str) -> object | None:
    """JSON を読む.

    Returns:
        読んだ値。JSON でなければ None。
    """
    try:
        return cast("object", json.loads(text))
    except ValueError:
        return None


def touched_files(tool_input: object) -> list[str]:
    """ツールの入力から、書き換えたファイルのパスを取り出す.

    Claude Code の Write・Edit・MultiEdit は tool_input.file_path を持つ。
    Codex の apply_patch は tool_input.command にパッチ本文を持つので、そこからパスを拾う。

    Returns:
        書き換えたファイルのパス(入力に書かれたまま)。
    """
    fields = as_dict(tool_input)
    file_path = fields.get("file_path")
    if isinstance(file_path, str):
        return [file_path]
    command = fields.get("command")
    if isinstance(command, list):
        command = "\n".join(str(part) for part in cast("list[object]", command))
    if not isinstance(command, str):
        return []
    return [match.group(1).strip() for match in _PATCH_PATH.finditer(command)]


def read_lintable(path: Path) -> str | None:
    """フックで検査する対象なら本文を読む. 同梱ルールは日本語向けなので、かなのない本文は対象外.

    Returns:
        対象の拡張子で、読めて、かなを含むファイルの本文。対象外なら None。
    """
    if path.suffix.lower() not in LINTABLE_EXTENSIONS:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text if KANA.search(text) else None


def format_messages(report: Report, project_dir: Path) -> list[str]:
    """知らせる指摘を取り出す.

    Returns:
        warning と error の指摘を「パス:行:列 [ルール] メッセージ」の形にしたもの。
    """
    lines: list[str] = []
    for result in report.results:
        try:
            shown = os.path.relpath(result.file_path, project_dir)
        except ValueError:  # Windows でドライブが違うとき
            shown = result.file_path
        # プロジェクトの外のファイルは、../ をたどる相対パスより絶対パスのほうが読みやすい。
        if shown.startswith(os.pardir):
            shown = result.file_path
        lines.extend(
            f"{shown}:{message.line}:{message.column} [{message.rule_id}] {message.message}"
            for message in result.messages
            if message.severity in REPORTED_SEVERITIES
        )
    return lines


def build_output(lines: list[str], mode: str) -> dict[str, object]:
    """フックの出力を組み立てる.

    Returns:
        warn なら additionalContext、それ以外なら decision: block の JSON オブジェクト。
    """
    report = f"textlint の指摘が {len(lines)} 件あります。\n" + "\n".join(lines) + "\n\n" + _INSTRUCTION
    if mode == "warn":
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": report}}
    return {"decision": "block", "reason": report}


def _project_dir(payload: dict[str, object], env: Mapping[str, str]) -> Path:
    cwd = payload.get("cwd")
    return Path(env.get("CLAUDE_PROJECT_DIR") or (cwd if isinstance(cwd, str) else "") or Path.cwd())


def _documents(payload: dict[str, object], project_dir: Path) -> list[Document]:
    paths = (Path(name) for name in touched_files(payload.get("tool_input")))
    absolute = sorted({path if path.is_absolute() else project_dir / path for path in paths})
    documents: list[Document] = []
    for path in absolute:
        text = read_lintable(path)
        if text is not None:
            documents.append(Document(str(path), text))
    return documents


def run(stdin_text: str, env: Mapping[str, str]) -> tuple[str, str]:
    """フックを1回実行する.

    Returns:
        (標準出力, 標準エラー) に書く内容。
    """
    mode = env.get("TEXTLINT_HOOK_MODE") or env.get("CLAUDE_PLUGIN_OPTION_HOOK_MODE") or "block"
    payload = as_dict(parse_json(stdin_text))
    if mode == "off" or not payload:
        return _NOTHING
    project_dir = _project_dir(payload, env)
    documents = _documents(payload, project_dir)
    if not documents:
        return _NOTHING
    try:
        report = lint_documents(documents, config=choose_file_config(project_dir, env), env=env)
    except TextlintError as error:
        return "", f"ja-writing: {error}\n"
    lines = format_messages(report, project_dir)
    if not lines:
        return _NOTHING
    return json.dumps(build_output(lines, mode), ensure_ascii=False), ""


def main() -> int:
    """標準入力からフックの入力を読み、結果を書き出す.

    Returns:
        常に 0。
    """
    stdout, stderr = run(sys.stdin.read(), os.environ)
    _ = sys.stdout.write(stdout)
    _ = sys.stderr.write(stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
