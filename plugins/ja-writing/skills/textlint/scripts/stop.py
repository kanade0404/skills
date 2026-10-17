"""Stop フック. ターンの最後の答えを textlint にかける.

モードは TEXTLINT_STOP_MODE(Claude Code ではプラグインの stop_mode 設定)で選ぶ。

- notify(既定): 指摘の件数と内容を利用者にだけ表示する。Claude には書き直させない
- block: 指摘を返して書き直させる。書き直しのあとは stop_hook_active が真になるので、1回だけ
- off: 何もしない

答えはファイルではないので、誤検知の少ないチャット用の設定(config/chat.json)を使う。
フックの失敗でエージェントの作業を止めないよう、どの経路でも終了コード 0 で終える。
"""

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
    """Claude Code の会話記録(JSONL)から、最後の発言より後に Claude が書いた本文を取り出す.

    Returns:
        最後のターンの本文。ツールの呼び出しは含めない。
    """
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
    """フックの入力から最後の答えを取り出す.

    入力に last_assistant_message があればそれを使い、なければ transcript_path の会話記録から取り出す。

    Returns:
        最後の答えの本文。取り出せなければ空。
    """
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
    """フックの出力を組み立てる.

    Returns:
        block なら decision: block、それ以外なら利用者向けの systemMessage の JSON オブジェクト。
    """
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
    """フックを1回実行する.

    Returns:
        (標準出力, 標準エラー) に書く内容。
    """
    mode = env.get("TEXTLINT_STOP_MODE") or env.get("CLAUDE_PLUGIN_OPTION_STOP_MODE") or "notify"
    payload = as_dict(parse_json(stdin_text))
    # 書き直させたあとの Stop では何もしない(書き直しは1回だけ)。
    if mode == "off" or not payload or payload.get("stop_hook_active") is True:
        return _NOTHING
    text = answer_text(payload)
    if not KANA.search(text):
        return _NOTHING
    try:
        report = lint_documents([Document("answer.md", text)], config=load_config(CHAT_CONFIG), env=env)
    except TextlintError as error:
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
