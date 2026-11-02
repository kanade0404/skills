# 編集後の検査

Claude Code が Markdown やテキストのファイルを書き換えると、PostToolUse フックが textlint をかけ、指摘を Claude に返して直させる。利用者は、直された後のファイルを受け取る。

## Sub-features

- `post-edit-block` は、既定の block のモードで指摘を `decision: block` として返し、Claude に書き直させる。`warn` と `off` 以外の値も block として扱う。
- `post-edit-warn` は、warn のモードで指摘を文脈として渡すだけにする。
- `post-edit-off` は、off のモードで検査しない。
- `post-edit-skip` は、かなを含まないファイルと対象外の拡張子を検査しない。対象は `.md`・`.markdown`・`.mdown`・`.mkdn`・`.txt`・`.text` で、大文字と小文字を区別しない。
- `post-edit-project-config` は、プロジェクトの設定があれば同梱の設定の代わりに使う。

## How to get to it (user POV)

- Claude Code に、日本語の Markdown を Write で新しく書かせる。
- Claude Code に、既存の Markdown を Edit か MultiEdit で直させる。Codex の `apply_patch` も同じフックの対象である。
- プラグインの設定 `hook_mode`、または環境変数 `TEXTLINT_HOOK_MODE` でモードを選ぶ。環境変数が設定より優先する。
- 設定ファイルは `TEXTLINT_CONFIG`、プロジェクトの `.textlintrc.json`、`.textlintrc`、同梱の設定の順に選ぶ。ほかの形式の設定ファイルは読まない。プロジェクトは `CLAUDE_PROJECT_DIR`、なければ作業ディレクトリである。

## Driving it with verify.sh

前提として、launch と doctor が通り、`ws/` に `note.md` がない状態から始める。

- 書かせる操作は `.claude/skills/verify-ja-writing/scripts/verify.sh drive post-edit` で再現する。claude は `fixtures/bad.md` の中身を `ws/note.md` に Write で書く。
- 指摘は `post-edit/hooks.jsonl` の `PostToolUse:Write` に出る。`stdout` を `fromjson` で開くと、`decision` が `block` で、`reason` に指摘が 1 件入る。
- 指摘の ruleId は `ja-technical-writing/ja-no-redundant-expression` のように preset の名前が前に付く。
- 直す操作は同じ実行の中で続く。claude が Edit で直すと、`PostToolUse:Edit` は `stdout` が空で終わる。
- 結果は `post-edit/files/note.md` で確かめる。3 行目が `この機能は設定を変更できる。とても便利です！` になっている。
- warn のモードは `TEXTLINT_HOOK_MODE=warn .claude/skills/verify-ja-writing/scripts/verify.sh drive post-edit` で動かす。`stdout` が `hookSpecificOutput.additionalContext` になり、`decision` は出ない。Claude は文脈の指摘を読んで直すことがある。
- off のモードは `TEXTLINT_HOOK_MODE=off .claude/skills/verify-ja-writing/scripts/verify.sh drive post-edit` で動かす。`PostToolUse:Write` の `stdout` が空で、`files/note.md` は見本のまま残る。
- 隔離は `post-edit/init.json` で確かめる。`plugins` が `ja-writing@inline` と組み込みの `@builtin` だけになっている。
- 代替の駆動は `.claude/skills/verify-ja-writing/scripts/verify.sh hook post-edit` で、Write と同じ形の JSON を `post_edit.py` に渡す。`fallback-hook-post-edit/stdout.json` に同じ指摘が出る。

## Gotchas

- warn と off の実行のあと、同じ RUN_ID の `ws/note.md` は残る。続けて別のモードを試すときは RUN_ID を分ける。
- 指摘の行と列は 1 から数える。見本の指摘は `note.md:3:11` に出る。
- Claude が指摘を直す回数は決まっていない。直さずに理由を答えて終わることもあるので、`files/note.md` の中身で判定する。
- `ws/` に `.textlintrc.json` か `.textlintrc` を置くと設定が変わり、見本の指摘は出なくなることがある。置くのは `post-edit-project-config` を確かめるときだけにする。
- drive は Write、Edit、Read だけを渡す。MultiEdit と `apply_patch` の入口は確かめていない入口として報告する。
- textlint を動かせないとき、フックは標準エラーに書いて終了コード 0 で終わる。利用者と Claude のどちらにも何も見えないので、指摘がないことと区別できない。`stderr` と `debug.log` も見る。
- 依頼の言い回しはモデルに頼る。haiku が Write を使わずに答えると、`hooks.jsonl` に `PostToolUse:Write` は出ない。その実行は失敗として報告し、依頼を弱めない。
