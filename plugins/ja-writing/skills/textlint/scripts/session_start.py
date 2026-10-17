"""SessionStart フック. 依存を先に入れ、文章の規約を文脈に渡す.

規約は config/guidelines.md の本文で、セッションの開始・再開・/clear・compact のたびに1回だけ入る。
TEXTLINT_SESSION_CONTEXT(Claude Code ではプラグインの session_context 設定)を偽にすると、依存だけを入れる。

フックの失敗でセッションを止めないよう、どの経路でも終了コード 0 で終える。
"""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

from textlint_runner import SKILL_ROOT, TextlintError, ensure_installed

if TYPE_CHECKING:
    from collections.abc import Mapping

GUIDELINES = SKILL_ROOT / "config" / "guidelines.md"
_FALSY = frozenset({"0", "false", "no", "off"})


def context_enabled(env: Mapping[str, str]) -> bool:
    """規約を文脈に渡すかを判定する.

    Returns:
        設定が偽を表す値でなければ True。
    """
    value = env.get("TEXTLINT_SESSION_CONTEXT") or env.get("CLAUDE_PLUGIN_OPTION_SESSION_CONTEXT") or "true"
    return value.lower() not in _FALSY


def run(env: Mapping[str, str]) -> tuple[str, str]:
    """依存を入れ(失敗は標準エラーに回す)、環境変数に従って規約を出力する内容を決める.

    テストから直接呼べるよう、出力を戻り値にしている。

    Returns:
        (標準出力, 標準エラー) に書く内容。
    """
    stderr = ""
    try:
        _ = ensure_installed(env)
    except (TextlintError, OSError) as error:
        stderr = f"ja-writing: {error}\n"
    if not context_enabled(env):
        return "", stderr
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": GUIDELINES.read_text(encoding="utf-8"),
        }
    }
    return json.dumps(output, ensure_ascii=False), stderr


def main() -> int:
    """os.environ で run を呼び、結果を書き出す. フックの失敗でセッションを止めないよう、常に 0 を返す.

    Returns:
        常に 0。
    """
    stdout, stderr = run(os.environ)
    _ = sys.stdout.write(stdout)
    _ = sys.stderr.write(stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
