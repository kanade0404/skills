# yomiyasu スキル

利用者は `/ja-writing:yomiyasu` か「読みやすくして」の依頼で、AI が書いた日本語を推敲させる。Claude は意味を保って書き直し、`yomiyasu_check.py` で原文と最終稿を照合してから返す。

## Sub-features

- `yomiyasu-rewrite` は、原文のファイルを推敲し、原文と同じフォルダに別の名前で最終稿を保存する。
- `yomiyasu-lint` は、`yomiyasu_lint.py` で原文の候補を出す。
- `yomiyasu-check` は、`yomiyasu_check.py run` で最終稿の lint と原文との差分を実行し、記録を残す。
- `yomiyasu-verify` は、`yomiyasu_check.py verify` で、提出する最終稿が記録と合うかを確かめる。

## How to get to it (user POV)

- Claude Code で `/ja-writing:yomiyasu <ファイル>` を実行する。
- 「この文章を読みやすくして」「AI っぽさをなくして」と頼む。
- 引数なしで呼び、作業フォルダの直下の `.md` を選ばせる。

## Driving it with verify.sh

前提として、launch と doctor が通っている。`verify.sh` が `fixtures/yomiyasu-source.md` を `ws/source.md` に写す。

- 呼び出しは `VERIFY_MODEL=sonnet VERIFY_BUDGET_USD=1.00 verify.sh drive yomiyasu-skill` で再現する。依頼は `/ja-writing:yomiyasu source.md` である。
- 記録は `YOMIYASU_STATE_DIR` で作業ディレクトリの `yomiyasu-state/` に向ける。Bash の許可の形は、textlint スキルと同じく追加のシステムプロンプトで伝える。
- スキルが読まれたことは `session/*.jsonl` の `<command-name>/ja-writing:yomiyasu</command-name>` で確かめる。スラッシュコマンドは `Skill` の `tool_use` にならない。
- 推敲の結果は `yomiyasu-skill/files/` で確かめる。`source.md` は見本と同じままで、`source.rewritten.md` のような別の名前の最終稿が増えている。
- 原文の lint は、Bash の `tool_use` に `yomiyasu_lint.py source.md` が出れば確かめられる。
- 照合は、Bash の `tool_use` の `yomiyasu_check.py run --source ... --final ...` で確かめる。対応する `tool_result` に `yomiyasu-check: executed via=run` の行が出る。
- 提出前の確認は、Bash の `tool_use` の `yomiyasu_check.py verify` で確かめる。`tool_result` の 1 行目が `ハッシュが記録と合いました` を含み、2 行目が `yomiyasu-check: executed via=verify` になる。
- 判定には終了コードより `yomiyasu-check:` の状態の行を使う。終了コード 0 は、記録のない `unrecorded` の場合も含むからである。
- リポジトリに `.yomiyasu/` ができていないことを `git status --short --ignored` で確かめる。
- 代替の駆動は `verify.sh yomiyasu` である。`fixtures/yomiyasu-final.md` を最終稿として渡し、`fallback-yomiyasu/stdout.txt` に `yomiyasu-check: executed` の行と差分が出て、終了コードは 0 になる。

## Gotchas

- スキルの本文が長く、haiku では手順を守りきれないことがある。この drive は sonnet で動かし、費用の上限も上げる。2026-11-01 の実行は 0.25 ドルだった。
- 2026-11-01 の sonnet の 2 回の実行では、どちらも `yomiyasu_lint.py` を実行しなかった。原文の lint は確かめていない手順として報告し、依頼を弱めない。
- 追加のシステムプロンプトがないと、モデルは `printf ... > 最終稿 && python3 ... run && python3 ... verify` のような組み合わせを書いて拒否される。拒否されたあとは、承認の要る操作をすべて諦めて `yomiyasu-check: not_run reason=not_allowed` と答える。
- このマシンの Bash ツールでは `python3` が mise の shim になって失敗する。モデルは `/usr/bin/python3` で実行し直す。
- `yomiyasu_check.py` は、記録の置き場を指定しないと git の最上位か作業ディレクトリの `.yomiyasu/` に書く。リポジトリで直接動かすと `.yomiyasu/` が増えるので、必ず `--state-dir` か `YOMIYASU_STATE_DIR` を使う。
- `--no-record` は利用者が頼んだときだけ使う決まりがあるので、検証でも使わない。
- 代替の終了コード 0 は「検査を最後まで実行した」の意味で、意味が保たれた証明ではない。
- 最終稿の Write でも PostToolUse フックが動く。見本の最終稿では `することができました` に block の指摘が出る。モデルが書き直すか誤検知として残すかは決まっていないので、どの Write がスキルの結果かを `files/` で見分ける。
- スキルには `yomiyasu_hook.py` もあるが、プラグインの `hooks.json` には登録されていない。利用者が `start` のあとに表示される設定を自分で貼ったときだけ動くので、この drive では動かない。
