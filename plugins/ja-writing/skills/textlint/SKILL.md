---
name: textlint
description: Markdown やテキストの日本語を textlint で検査し、指摘を直すスキル。preset-ja-technical-writing、preset-ja-spacing、AI らしい書き方を見つける @textlint-ja/preset-ai-writing を同梱する。「textlint をかけて」「文章をチェックして」「校正して」「AI っぽい表現を直して」といった依頼や、.md・.txt をまとめて検査するときに使う。
argument-hint: "[ファイルかglob ...] [--fix]"
---

# textlint

このスキルのディレクトリにある `scripts/lint.py` を Python 3.9 以上で実行する。Node.js 22.18 以上と npm も必要。初回は textlint の依存を `~/.cache/ja-writing-textlint/` へ `npm ci` で入れるので、少し時間がかかる。

```bash
python3 <このスキルのディレクトリ>/scripts/lint.py <ファイルかglob>...
python3 <このスキルのディレクトリ>/scripts/lint.py --fix <ファイルかglob>...
python3 <このスキルのディレクトリ>/scripts/lint.py --fix --dry-run <ファイルかglob>...
```

`--fix` は自動で直せる指摘を直してファイルに書き戻す。`--fix --dry-run` は書き戻さずに差分を表示する。error の指摘が残れば終了コード 1 を返す。標準エラーの最後の行に、使った設定ファイルが出る。

## 設定ファイルの選び方

1. 環境変数 `TEXTLINT_CONFIG`（JSON）
2. プロジェクトの `.textlintrc.json` か `.textlintrc`（JSON で書いたもの）
3. 同梱の `config/textlintrc.json`

検査は @textlint/kernel で行い、同梱のプリセットとフィルタだけを読み込む。プロジェクトの設定は、同梱ルールの有効・無効とオプションとして使う。同梱にないルールの名前は無視し、「同梱にないため無視した設定」として表示する。YAML や JS の設定は読まない。

## 手順

1. 対象を決める。引数があればそれを使う。なければ作業ツリーで変更した `.md`・`.txt` を対象にする（`git diff --name-only HEAD` と未追跡のファイル）。glob は引用符で囲む。
2. `--fix` を頼まれたら、先に `--fix` で自動修正し、残った指摘を確かめる。
3. 残った指摘は文章を書き直して直す。
   - `@textlint-ja/ai-writing/*` の指摘は、自然な日本語に書き直す。規約は `config/guidelines.md` にまとめている。
   - `ai-tech-writing-guideline` は info なので提案として扱う。明らかに良くなるものだけ反映する。
   - `ja-technical-writing/*` はメッセージに従う。長い文を分ける、である調とですます調をそろえる、などで直す。
   - 意味は変えない。指摘を消すために内容を削らない。
4. エラーがなくなるまで繰り返す。誤検知は文章を残す。利用者が同意したときだけ `<!-- textlint-disable ルール名 -->` と `<!-- textlint-enable -->` で囲む。
5. 直す前と後の件数を伝え、残した指摘があれば理由を添える。

## 自動の検査

Claude Code と Codex にプラグインとして入れたときは、次のフックも動く。

| フック       | 内容                                                                 | 切り替え                                                      |
| ------------ | -------------------------------------------------------------------- | ------------------------------------------------------------- |
| SessionStart | 依存を入れ、`config/guidelines.md` の規約を文脈に渡す                | `TEXTLINT_SESSION_CONTEXT`（`session_context`）               |
| PostToolUse  | 書き換えた `.md`・`.txt` を検査する。かなを含まないファイルは対象外  | `TEXTLINT_HOOK_MODE`（`hook_mode`）: `block`・`warn`・`off`   |
| Stop         | ターンの最後の答えを、チャット用の設定 `config/chat.json` で検査する | `TEXTLINT_STOP_MODE`（`stop_mode`）: `notify`・`block`・`off` |

括弧の中は Claude Code のプラグイン設定の名前。
