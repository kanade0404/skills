# セッション開始の規約

Claude Code のセッションが始まると、SessionStart フックが textlint の依存を入れ、日本語の文章の規約を文脈に入れる。利用者が何も頼まなくても、Claude は規約に沿って書き始める。

## Sub-features

- `session-context` は、`config/guidelines.md` の全文を `additionalContext` として渡す。
- `session-install` は、依存がなければ `bun install` で入れてから規約を渡す。入れるのに失敗しても標準エラーに書くだけで、終了コード 0 のまま規約を渡す。
- `session-context-off` は、規約を渡さない。依存を入れる処理は止まらない。
- `session-events` は、起動、再開、`/clear`、compact のどれでも動く。matcher も重複の防止もないので、そのたびに規約を入れる。

## How to get to it (user POV)

- プラグインを入れた状態で Claude Code を起動するか、セッションを再開する。
- 対話の途中で `/clear` を実行する。
- プラグインの設定 `session_context`、または環境変数 `TEXTLINT_SESSION_CONTEXT` で切り替える。環境変数が空でなければ、設定より優先する。偽として扱う値は `0`・`false`・`no`・`off` である。

## Driving it with verify.sh

前提として、launch が通っている。drive が確かめるのは launch の目印だけなので、doctor は自分で先に実行する。

- 起動は `verify.sh drive session-start` で再現する。依頼は、SessionStart フックが足した文書の 1 行目をそのまま答えさせるものである。
- 渡した規約は `session-start/hooks.jsonl` の `hook_name` が `SessionStart:startup` の `hook_response` に出る。同じ名前の `hook_started` の行もある。
- `hook_response` の `exit_code` は 0 になる。`stdout` は JSON の文字列なので、`jq '.stdout | fromjson | .hookSpecificOutput.additionalContext'` で開く。中身は `# 日本語の文章の規約（ja-writing）` で始まる。
- Claude が受け取ったことは `session-start/result.json` で確かめる。`result` が `# 日本語の文章の規約（ja-writing）` になる。
- 規約を切る動きは `TEXTLINT_SESSION_CONTEXT=false verify.sh drive session-start` で動かす。`SessionStart:startup` の `stdout` が空になる。`result` は `NONE` になることが多い。
- 依存を入れる経路は launch が通る。launch は SessionStart フックと同じ `session_start.py` を空の `deps/` に向けて実行するので、初回の `bun install` がここで走る。
- 代替の駆動は `verify.sh hook session-start` で、`fallback-hook-session-start/stdout.json` に同じ JSON が出る。

## Gotchas

- `post-edit` を含むどのヘッドレスの実行でも、SessionStart は動く。規約が入った証拠だけなら、ほかの機能の `hooks.jsonl` でも足りる。
- drive の中の SessionStart は、launch で依存が入ったあとに動く。drive で `bun install` の失敗の経路は確かめられない。
- 再開、`/clear`、compact の入口は、`-p` の 1 回のターンでは再現できない。確かめていない入口として報告する。
- `result` はモデルの判断に頼る。以前の依頼の文では、haiku がスキルの名前 `ja-writing:textlint` を答えた。規約が渡ったかどうかは、`hooks.jsonl` の `stdout` で判定する。
- `stop` の Stop フックは、`result` の見出しの文字列も検査する。かなを含むので、指摘がなくても Stop フックが動く。
