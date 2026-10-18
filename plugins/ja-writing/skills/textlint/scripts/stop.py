from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from post_edit import KANA, REPORTED_SEVERITIES, as_dict, parse_json
from textlint_runner import CHAT_CONFIG, Document, TextlintError, lint_documents, load_config

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from typing import Literal

_NOTHING = ("", "")
_SHOWN_MESSAGES = 5


def _blocks(entry: dict[str, object]) -> list[dict[str, object]]:
    content = as_dict(entry.get("message")).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [as_dict(block) for block in cast("list[object]", content)]
    return []


def _is_prompt(entry: dict[str, object]) -> bool:
    # 利用者の発言。ツールの結果も type: user で届くので、テキストを含むものだけを数える。
    return entry.get("type") == "user" and any(block.get("type") == "text" for block in _blocks(entry))


def last_turn_text(lines: Iterable[str]) -> str:
    entries = [as_dict(parse_json(line)) for line in lines if line.strip()]
    start = max((index for index, entry in enumerate(entries) if _is_prompt(entry)), default=-1)
    texts = [
        str(block.get("text", ""))
        for entry in entries[start + 1 :]
        if entry.get("type") == "assistant"
        for block in _blocks(entry)
        if block.get("type") == "text"
    ]
    return "\n\n".join(text for text in texts if text)


def answer_text(payload: dict[str, object]) -> str:
    message = payload.get("last_assistant_message")
    if isinstance(message, str):
        return message
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str):
        return ""
    try:
        with Path(transcript).open(encoding="utf-8") as stream:
            return last_turn_text(stream)
    except (OSError, UnicodeDecodeError):
        return ""


def build_output(lines: list[str], mode: str) -> dict[str, object]:
    if mode == "block":
        reason = (
            f"直前の答えに textlint の指摘が {len(lines)} 件あります。\n"
            + "\n".join(lines)
            + "\n\n指摘された箇所を直した答えを、短く書き直してください。"
        )
        return {"decision": "block", "reason": reason}
    shown = lines[:_SHOWN_MESSAGES]
    rest = len(lines) - len(shown)
    summary = "\n".join(shown) + (f"\nほか {rest} 件" if rest else "")
    return {"systemMessage": f"textlint: 答えに {len(lines)} 件の指摘\n{summary}"}


def run(stdin_text: str, env: Mapping[str, str]) -> tuple[str, str]:
    mode = env.get("TEXTLINT_STOP_MODE") or env.get("CLAUDE_PLUGIN_OPTION_STOP_MODE") or "notify"
    payload = as_dict(parse_json(stdin_text))
    # stop_hook_active が真なのは、block で書き直させたあとの Stop。
    if mode == "off" or not payload or payload.get("stop_hook_active") is True:
        return _NOTHING
    text = answer_text(payload)
    if not KANA.search(text):
        return _NOTHING
    try:
        report = lint_documents([Document("answer.md", text)], config=load_config(CHAT_CONFIG), env=env)
    except (TextlintError, OSError) as error:
        return "", f"ja-writing: {error}\n"
    lines = [
        f"{message.line}:{message.column} [{message.rule_id}] {message.message}"
        for result in report.results
        for message in result.messages
        if message.severity in REPORTED_SEVERITIES
    ]
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
