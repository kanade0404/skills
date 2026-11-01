# textlint スキル

利用者は `/ja-writing:textlint` か「textlint をかけて」の依頼で、指定したファイルを検査させる。Claude はスキルの `lint.py` を実行し、指摘を直して、直す前と後の件数を伝える。

## Sub-features

- `skill-lint` は、ファイルか glob を検査して指摘を表示する。error が残れば終了コード 1 になる。
- `skill-fix` は、`--fix` で自動で直せる指摘を直してファイルに書き戻す。
- `skill-dry-run` は、`--fix --dry-run` で書き戻さずに差分を表示する。
- `skill-config` は、`TEXTLINT_CONFIG`、プロジェクトの `.textlintrc.json`、同梱の設定の順に設定を選ぶ。プロジェクトの設定は同梱の設定と混ぜずに置き換える。
- `skill-rewrite` は、自動で直せない指摘を Claude が文章を書き直して直す。
- `skill-usage-error` は、使い方や環境の誤りで終了コード 2 を返す。

## How to get to it (user POV)

- Claude Code で `/ja-writing:textlint <ファイル>` を実行する。
- 「この文章を校正して」「textlint をかけて」と頼む。
- 引数なしで呼び、作業ツリーで変更した `.md`・`.txt` を対象にさせる。

## Driving it with verify.sh

前提として、launch と doctor が通っている。`verify.sh` が `fixtures/bad.md` を `ws/draft.md` に写す。

- 呼び出しは `verify.sh drive textlint-skill` で再現する。依頼は `/ja-writing:textlint draft.md` で、Bash は `python3` で始まる単独のコマンドだけを許す。
- 許可の形は追加のシステムプロンプトでモデルに伝える。承認する人がいないので、合わないコマンドはそのまま拒否されるからである。
- スラッシュコマンドは `Skill` の `tool_use` にならない。スキルが読まれたことは `session/*.jsonl` の `<command-name>/ja-writing:textlint</command-name>` と `Base directory for this skill` で確かめる。
- スキルが動いたことは `textlint-skill/transcript.jsonl` の Bash の `tool_use` で確かめる。`scripts/lint.py draft.md` を含むコマンドが出て、対応する `tool_result` に `draft.md:3:11  error` が出る。
- 直した結果は `textlint-skill/files/draft.md` で確かめる。`することができる` がなくなっている。
- 件数の報告は `textlint-skill/result.json` の `result` で確かめる。直す前の 1 件と、直した後の 0 件が入る。
- 代替の駆動は `verify.sh lint` と `verify.sh lint --fix` である。`fallback-lint/stdout.txt` に `draft.md:3:11  error` の行が出て、終了コードは 1 になる。
- `--fix` のあとは終了コード 0 で、`fallback-lint/draft.after.md` が `変更できる` に直る。見本の指摘は自動で直せるので、error は残らない。

## Gotchas

- 終了コード 2 は使い方か環境の誤りを表す。対象の指定がないとき、`--fix` なしで `--dry-run` を付けたとき、ファイルを読めないときに出る。`bun` がないか古いとき、依存を入れられないとき、設定が壊れているときも同じである。
- 存在しないファイル、フォルダ、何にも当たらない glob は黙って外れ、終了コード 0 になる。フォルダの中は辿らない。指摘がない結果と取り違えない。
- プロジェクトの設定は、作業ディレクトリの直下だけを探す。
- 許可のルール `Bash(python3 *)` が組み合わせたコマンドに当たるかは、組み合わせの中身で変わる。2026-11-01 の実行では、`ls -la && python3 .../lint.py draft.md; echo "exit=$?"` は拒否された。`ls -la && python3 .../lint.py draft.md` は許された。
- このマシンの Bash ツールは利用者のシェルの設定を読むので、`python3` が mise の shim になって失敗する。モデルは `/usr/bin/python3` で実行し直した。そのために `Bash(/usr/bin/python3 *)` も許している。
- Edit と Write は `acceptEdits` で許すので、PostToolUse フックも同時に動く。`hooks.jsonl` の `PostToolUse` はスキルの証拠ではない。
- 件数の報告の書き方によっては、Stop フックが答えに `no-ai-list-formatting` を出す。追加のシステムプロンプトが英語なので、答えが英語になることもある。
- `lint` の代替は毎回 `ws/draft.md` を見本で上書きする。`--fix` の前の状態を残したいときは RUN_ID を分ける。
- 標準エラーの最後の行に、使った設定のパスが出る。同梱の設定以外が出たら、`ws/` に設定が紛れ込んでいる。
