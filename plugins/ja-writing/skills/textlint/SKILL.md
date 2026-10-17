---
name: textlint
description: Markdown やテキストの日本語を textlint で検査し、指摘を直すスキル。preset-ja-technical-writing、preset-ja-spacing、AI らしい書き方を見つける @textlint-ja/preset-ai-writing を同梱する。「textlint をかけて」「文章をチェックして」「校正して」「AI っぽい表現を直して」といった依頼や、.md・.txt をまとめて検査するときに使う。
argument-hint: "[ファイルかglob ...] [--fix]"
---

# textlint

このスキルのディレクトリにある `scripts/lint.mjs` を Node.js（20.18 以上）で実行する。初回は textlint と同梱ルールを `~/.cache/ja-writing-textlint/` へ `npm ci` で入れるので、少し時間がかかる。

```bash
node <このスキルのディレクトリ>/scripts/lint.mjs <ファイルかglob>...
```

textlint のオプションはそのまま渡せる。たとえば `--fix`、`--fix --dry-run -f diff`、`-f json` を使える。標準エラーの最後の行に、使った設定ファイルが出る。

## 設定ファイルの選び方

1. 環境変数 `TEXTLINT_CONFIG`
2. プロジェクトの `.textlintrc.json` か `.textlintrc`（JSON で書いたもの）
3. 同梱の `config/textlintrc.json`

プロジェクトの設定に書かれたルールは、同梱の依存から探す。プロジェクトのコードを実行しないよう、既定では JSON の設定だけを読み、ルール名がパッケージ名の形をしているかを確かめる。YAML や JS の設定とプロジェクトの `node_modules/textlint` を使うときは `TEXTLINT_TRUST_PROJECT=1` を設定する。

## 手順

1. 対象を決める。引数があればそれを使う。なければ作業ツリーで変更した `.md`・`.txt` を対象にする（`git diff --name-only HEAD` と未追跡のファイル）。glob は引用符で囲む。
2. `--fix` を頼まれたら、先に `--fix` で自動修正し、もう一度検査する。
3. 残った指摘は文章を書き直して直す。
   - `@textlint-ja/ai-writing/*` の指摘は、自然な日本語に書き直す。`**ラベル**: 説明` 形式の箇条書きは普通の文か普通の箇条書きにし、飾りの絵文字を外す。「次の通りです:」の後にブロックを続ける形は、コロンで終わらない文に直し、誇張した語を抑える。
   - `ai-tech-writing-guideline` は info なので提案として扱う。明らかに良くなるものだけ反映する。
   - `ja-technical-writing/*` はメッセージに従う。長い文を分ける、である調とですます調をそろえる、などで直す。
   - 意味は変えない。指摘を消すために内容を削らない。
4. エラーがなくなるまで繰り返す。誤検知は文章を残す。利用者が同意したときだけ `<!-- textlint-disable ルール名 -->` と `<!-- textlint-enable -->` で囲む。
5. 直す前と後の件数を伝え、残した指摘があれば理由を添える。

## 自動の検査

Claude Code と Codex にプラグインとして入れたときは、書き込み後のフックが `.md`・`.txt` を自動で検査する。かなを含まないファイルは対象外にしている。挙動は `TEXTLINT_HOOK_MODE` で切り替える。Claude Code ではプラグインの `hook_mode` 設定でも切り替えられる。値は `block`（既定）、`warn`、`off` のどれか。
