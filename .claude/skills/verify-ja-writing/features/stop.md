# 答えの検査

ターンが終わると、Stop フックが Claude の最後の答えを答え用の設定 `config/chat.json` で検査する。既定では、指摘を利用者にだけ表示し、Claude には書き直させない。

## Sub-features

- `stop-notify` は、既定の notify のモードで指摘を `systemMessage` として利用者に表示する。最大 5 件を出し、残りは件数だけ出す。
- `stop-block` は、block のモードで `decision: block` を返し、答えを 1 回だけ書き直させる。
- `stop-off` は、off のモードで検査しない。
- `stop-skip-kana` は、かなを含まない答えを検査しない。漢字だけの答えと英語の答えはここで外れる。
- `stop-once` は、書き直したあとの Stop（`stop_hook_active` が真）では検査しない。notify のモードでも同じである。
- `stop-severity` は、warning と error の指摘だけを出す。

## How to get to it (user POV)

- プラグインを入れた Claude Code に、日本語で答えさせる。
- プラグインの設定 `stop_mode`、または環境変数 `TEXTLINT_STOP_MODE` でモードを選ぶ。
- 検査する文は、Claude Code が渡す最後の答えである。渡されないときは、最後の利用者の依頼より後の Claude の文をすべてつなげて検査する。

## Driving it with verify.sh

前提として、launch と doctor が通っている。

- 答えさせる操作は `.claude/skills/verify-ja-writing/scripts/verify.sh drive stop` で再現する。claude は `fixtures/stop-answer.md` の 1 行をそのまま答える。ツールは渡さない。
- 指摘は `stop/hooks.jsonl` の `Stop` に出る。`stdout` を `fromjson` で開いた `systemMessage` が `textlint: 答えに` で始まり、`@textlint-ja/ai-writing/no-ai-list-formatting` を含む。
- 2026-11-01 の実行では、見本の指摘は `no-ai-list-formatting` の 1 件だった。`革新的` と `劇的` は指摘されなかった。件数は判定に使わない。
- 答えそのものは `stop/result.json` の `result` で確かめる。見本の 1 行と同じになっている。
- block のモードは `TEXTLINT_STOP_MODE=block .claude/skills/verify-ja-writing/scripts/verify.sh drive stop` で動かす。`Stop` の `hook_response` が 2 回出る。
- block の 1 回目の `stdout` は `"decision": "block"` を持ち、`reason` が `直前の答えに textlint の指摘が` で始まる。2 回目は `stop_hook_active` が真なので空で終わり、`result` は書き直した答えになる。
- 代替の駆動は `.claude/skills/verify-ja-writing/scripts/verify.sh hook stop` で、`last_assistant_message` に見本を入れた JSON を `stop.py` に渡す。`fallback-hook-stop/stdout.json` に同じ `systemMessage` が出る。

## Gotchas

- 答え用の設定は `preset-ai-writing` だけを使う。`fixtures/bad.md` の `することができる` は答えの検査では指摘されない。
- `post-edit` の実行は最後に `done` と答えさせるので、答えにかなを含まず Stop は何も返さない。答えの検査の証拠には使えない。
- SessionStart の規約を受けた haiku は、依頼が短いと見本を直して答えることがある。依頼に「見本であり直さない」と明記してあるので、今は見本のまま答える。
- haiku が見本を少しでも書き換えると、指摘は消えることがある。`result.json` の `result` が見本と違えば、その実行は失敗として報告する。
- block のモードは書き直しの分だけ費用が増える。上限は `VERIFY_BUDGET_USD` で決まる。
- `stop.py` は `last_assistant_message` がなければセッションの記録を読む。`--no-session-persistence` を付けると記録がなくなるので、`verify.sh` は付けない。
- 検査が失敗すると、フックは標準エラーに `ja-writing: ...` を書いて終了コード 0 で終わる。`stdout` が空でも、指摘がなかったとは限らない。
