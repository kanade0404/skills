from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from post_edit import KANA, REPORTED_SEVERITIES
from protocol import BlockOutput, StopInput, SystemMessageOutput, TranscriptEntry, parse_stop, parse_transcript_line
from textlint_runner import CHAT_CONFIG, Document, TextlintError, lint_documents, load_config

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from typing import Literal

_NOTHING = ("", "")
_SHOWN_MESSAGES = 5


def _is_prompt(entry: TranscriptEntry) -> bool:
    # 利用者の発言。ツールの結果も type: user で届くので、テキストを含むものだけを数える。
    return entry.role == "user" and bool(entry.texts)


def last_turn_text(lines: Iterable[str]) -> str:
    entries = [entry for entry in map(parse_transcript_line, lines) if entry is not None]
    start = max((index for index, entry in enumerate(entries) if _is_prompt(entry)), default=-1)
    texts = (text for entry in entries[start + 1 :] if entry.role == "assistant" for text in entry.texts)
    return "\n\n".join(text for text in texts if text)


def answer_text(stop_input: StopInput) -> str:
    if stop_input.last_assistant_message is not None:
        return stop_input.last_assistant_message
    if stop_input.transcript_path is None:
        return ""
    try:
        with Path(stop_input.transcript_path).open(encoding="utf-8") as stream:
            return last_turn_text(stream)
    except (OSError, UnicodeDecodeError):
        return ""


def build_output(lines: list[str], mode: str) -> BlockOutput | SystemMessageOutput:
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
    stop_input = parse_stop(stdin_text)
    # stop_hook_active が真なのは、block で書き直させたあとの Stop。
    if mode == "off" or stop_input is None or stop_input.stop_hook_active:
        return _NOTHING
    text = answer_text(stop_input)
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
