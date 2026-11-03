from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from collections.abc import Mapping

REMINDER = (
    "スキル `/productivity:subagent-judgment` は、サブエージェントに任せる条件を定めている。"
    "任せてよい場合は4つに限る。"
    "文脈の肥大で出来が落ちたと測れた場合と、独立した作業を並列に進めたい場合。"
    "ツールの選択や指示の衝突で迷う場合と、評価役を実装者の前提から切り離したい場合。"
    "どれにも当たらなければメインのループで進め、任せた場合は報告の要点を自分で確かめる。"
)
SESSION_START_REMINDER = (
    "サブエージェントに任せる前に、スキル `/productivity:subagent-judgment` を読む。"
    "既定はメインのループで進めることで、スキルが定める4つの条件のどれかに当たるときだけ任せる。"
)
_FALSY = frozenset({"0", "false", "no", "off"})


class Context(TypedDict):
    hookEventName: str
    additionalContext: str


class Output(TypedDict):
    hookSpecificOutput: Context


def enabled(env: Mapping[str, str]) -> bool:
    value = env.get("SUBAGENT_JUDGMENT_REMINDER") or env.get("CLAUDE_PLUGIN_OPTION_SUBAGENT_REMINDER") or "true"
    return value.lower() not in _FALSY


def run(env: Mapping[str, str], mode: str = "") -> str:
    if not enabled(env):
        return ""
    # SessionStart は最初の Agent 呼び出しより前に効く。
    # PreToolUse の文脈は呼び出しの結果と一緒に届くので、2 回目の呼び出しから効く。
    if mode == "session-start":
        output: Output = {
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": SESSION_START_REMINDER},
        }
    else:
        output = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": REMINDER}}
    # 標準出力の文字コードが UTF-8 でない環境(Windows の cp1252 など)でも書けるように、ASCII だけで出す。
    return json.dumps(output)


def main() -> Literal[0]:
    _ = sys.stdout.write(run(os.environ, sys.argv[1] if len(sys.argv) > 1 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
