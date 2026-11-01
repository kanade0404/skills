# ja-writing の検証の地図

このディレクトリは、ja-writing プラグインの利用者から見える動きを確かめるための、保守される手順の置き場だ。
駆動する前にこの一覧を読み、当てはまる機能のファイルを手順として使う。

## 前提となる状態

- `RUN_ID` を決め、`.claude/skills/verify-ja-writing/scripts/verify.sh launch` で隔離した作業ディレクトリと textlint の依存を用意する。
- `.claude/skills/verify-ja-writing/scripts/verify.sh doctor` を実行し、`OK:` か `NG:` で始まる確認の行がすべて `OK:` になることを確かめる。
- claude は `ws/` で起動し、`--plugin-dir plugins/ja-writing` だけを読ませる。利用者の設定とプラグインは読ませない。
- 見本の文は `fixtures/` のものを使う。指摘がわざと出る文なので、書き換えない。
- この実行で起動していない claude のセッションは駆動しない。

## 駆動の決まり

- どの手順も launch 直後の状態から始める。前の機能が `ws/` に残したファイルは、次の機能の結果と混ざる。混ぜたくなければ RUN_ID を分ける。
- コマンドは書いたとおりに実行する。機能の名前と引数は変えない。
- 利用者の経路は `.claude/skills/verify-ja-writing/scripts/verify.sh drive <機能>` で動かす。`hook`・`lint`・`yomiyasu` のサブコマンドは代替の駆動である。
- フックのモードは `TEXTLINT_HOOK_MODE` と `TEXTLINT_STOP_MODE` を `verify.sh` の前に付けて切り替える。
- コマンドは、リポジトリの最上位をカレントディレクトリとして、`.claude/skills/verify-ja-writing/scripts/verify.sh` のパスで書いている。
- `verify.sh` は環境変数 `TEXTLINT_CONFIG` を外して動かす。外の設定が検査の規則を差し替えないようにするためである。
- 片付けは `.claude/skills/verify-ja-writing/scripts/verify.sh cleanup` で済ませ、証拠は消さない。

## 証明と見送りの報告

- 利用者の操作と、その結果の状態の両方を残す。フックの返事だけでなく、実行後のファイルも示す。
- ヘッドレスの証明には `transcript.jsonl` の `hook_response`、`init.json`、`files/` の 3 つをそろえる。
- 代替の駆動の証拠は入口ごとにそろえる。`hook` は標準入力の JSON、標準出力、終了コード。`lint` と `yomiyasu` はファイルの引数、標準出力、終了コード、生成されたファイルか記録。
- 証拠には、機能の名前と使った入口（drive または代替）を必ず添える。
- 入口に届かなかったときは、実行したコマンドと満たせなかった前提を報告する。
- 見送った入口を、別の入口で確かめたことにしない。

## 機能のファイルの決まり

各ファイルは H1 の題と、利用者から見える動きを述べる 1 段落から始め、次の 4 つの H2 をこの順に置く。

1. `Sub-features` は、短い ID と 1 行の説明を並べる。
2. `How to get to it (user POV)` は、利用者が機能に届くすべての入口を並べる。
3. `Driving it with verify.sh` は、前提を述べてから、利用者の操作ごとにコマンドと見える結果を文で書く。
4. `Gotchas` は、検証の手間を増やしたり結果を無効にしたりする罠を並べる。

実装の細部は書かない。書くのは利用者の経路、安定した目印、要る状態、コマンド、見える証拠だけにする。

## 機能

- [セッション開始の規約](./session-start.md)は、SessionStart フックが文章の規約を文脈に入れる動きを扱う。
- [編集後の検査](./post-edit.md)は、PostToolUse フックが書き換えた Markdown を検査し、直させる動きを扱う。
- [答えの検査](./stop.md)は、Stop フックがターンの最後の答えを検査する動きを扱う。
- [textlint スキル](./textlint-skill.md)は、利用者が `/ja-writing:textlint` でファイルを検査し、直させる動きを扱う。
- [yomiyasu スキル](./yomiyasu-skill.md)は、利用者が `/ja-writing:yomiyasu` で文章を推敲させ、検査スクリプトで照合する動きを扱う。
