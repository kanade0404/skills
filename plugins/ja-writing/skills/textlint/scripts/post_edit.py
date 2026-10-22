from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from protocol import BlockOutput, PostToolUseContextOutput, ToolUseInput, parse_tool_use
from textlint_runner import (
    LINTABLE_EXTENSIONS,
    Document,
    Report,
    TextlintError,
    choose_file_config,
    display_path,
    lint_documents,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Literal

_PATCH_PATH = re.compile(r"^\*\*\* (?:Add File|Update File|Move to): (.+)$", re.MULTILINE)
KANA = re.compile(
    r"[\N{HIRAGANA LETTER SMALL A}-\N{HIRAGANA DIGRAPH YORI}\N{KATAKANA LETTER SMALL A}-\N{KATAKANA DIGRAPH KOTO}]"
)
REPORTED_SEVERITIES = frozenset({1, 2})
_INSTRUCTION = (
    "指摘された箇所を自然な日本語に書き直してください。指摘を消すために内容を削らないでください。"
    "誤検知と判断したものはそのまま残し、理由を伝えてください。"
)
_NOTHING = ("", "")


def touched_files(tool_input: ToolUseInput) -> list[str]:
    """ツールの入力から、書き換えたファイルのパスを取り出す.

    Claude Code の Write・Edit・MultiEdit は tool_input.file_path を持つ。
    Codex の apply_patch は tool_input.command にパッチ本文を持つので、そこからパスを拾う。

    Returns:
        書き換えたファイルのパス(入力に書かれたまま)。
    """
    if tool_input.file_path is not None:
        return [tool_input.file_path]
    if tool_input.command is None:
        return []
    return [match.group(1).strip() for match in _PATCH_PATH.finditer(tool_input.command)]


def read_lintable(path: Path) -> str | None:
    if path.suffix.lower() not in LINTABLE_EXTENSIONS:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text if KANA.search(text) else None


def format_messages(report: Report, project_dir: Path) -> list[str]:
    lines: list[str] = []
    for result in report.results:
        shown = display_path(result.file_path, project_dir)
        lines.extend(
            f"{shown}:{message.line}:{message.column} [{message.rule_id}] {message.message}"
            for message in result.messages
            if message.severity in REPORTED_SEVERITIES
        )
    return lines


def build_output(lines: list[str], mode: str) -> BlockOutput | PostToolUseContextOutput:
    report = f"textlint の指摘が {len(lines)} 件あります。\n" + "\n".join(lines) + "\n\n" + _INSTRUCTION
    if mode == "warn":
        return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": report}}
    return {"decision": "block", "reason": report}


def _project_dir(tool_use: ToolUseInput, env: Mapping[str, str]) -> Path:
    return Path(env.get("CLAUDE_PROJECT_DIR") or tool_use.cwd or Path.cwd())


def _documents(tool_use: ToolUseInput, project_dir: Path) -> list[Document]:
    paths = (Path(name) for name in touched_files(tool_use))
    absolute = sorted({path if path.is_absolute() else project_dir / path for path in paths})
    documents: list[Document] = []
    for path in absolute:
        text = read_lintable(path)
        if text is not None:
            documents.append(Document(str(path), text))
    return documents


def run(stdin_text: str, env: Mapping[str, str]) -> tuple[str, str]:
    mode = env.get("TEXTLINT_HOOK_MODE") or env.get("CLAUDE_PLUGIN_OPTION_HOOK_MODE") or "block"
    tool_use = parse_tool_use(stdin_text)
    if mode == "off" or tool_use is None:
        return _NOTHING
    project_dir = _project_dir(tool_use, env)
    documents = _documents(tool_use, project_dir)
    if not documents:
        return _NOTHING
    try:
        report = lint_documents(documents, config=choose_file_config(project_dir, env), env=env)
    except (TextlintError, OSError) as error:
        return "", f"ja-writing: {error}\n"
    lines = format_messages(report, project_dir)
    if not lines:
        return _NOTHING
    return json.dumps(build_output(lines, mode), ensure_ascii=False), ""


def main() -> Literal[0]:
    stdout, stderr = run(sys.stdin.read(), os.environ)
    _ = sys.stdout.write(stdout)
    _ = sys.stderr.write(stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
