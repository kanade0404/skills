# skills

エージェント向けのプラグインとスキルを置くリポジトリ。Claude Code と Codex にはプラグインとして、その他のエージェントにはスキルとして入れる。

## ja-writing

日本語の文章を検査、推敲するプラグイン。次の 2 つのスキルとフックを含む。

- `textlint`: textlint で `.md`・`.txt` を検査し、指摘を直す
- `yomiyasu`: AI が書いた日本語を、意味を保ったまま読みやすく直す（[nanaism/yomiyasu](https://github.com/nanaism/yomiyasu)の写し）

textlint には次のルールを同梱している。フィルタは comments と allowlist を有効にしている。

- `textlint-rule-preset-ja-technical-writing`
- `textlint-rule-preset-ja-spacing`
- `@textlint-ja/textlint-rule-preset-ai-writing`

### 入れ方

Claude Code:

```
/plugin marketplace add kanade0404/skills
/plugin install ja-writing@kanade0404-skills
```

Codex:

```
codex plugin marketplace add kanade0404/skills
codex plugin add ja-writing@kanade0404-skills
```

Codex は信頼していないフックを実行しない。入れたあとに `/hooks` でフックを確認して信頼する。

Cursor や Gemini CLI などのその他のエージェントには、スキルだけを次のコマンドで入れる。

```
npx skills add kanade0404/skills
```

### フック

Claude Code と Codex では、次のフックが動く。括弧の中は Claude Code のプラグイン設定の名前。

| フック | 内容 | 切り替え |
| --- | --- | --- |
| SessionStart | 依存を入れ、文章の規約（`config/guidelines.md`）を文脈に渡す | `TEXTLINT_SESSION_CONTEXT`（`session_context`） |
| PostToolUse | エージェントが書き換えた `.md`・`.txt` を検査し、指摘を返す | `TEXTLINT_HOOK_MODE`（`hook_mode`）: `block`・`warn`・`off` |
| Stop | ターンの最後の答えを検査する。既定では利用者にだけ表示する | `TEXTLINT_STOP_MODE`（`stop_mode`）: `notify`・`block`・`off` |

かなを含まない本文は検査しない。規約は数百トークンで、セッションの開始、`/clear`、compact のたびに1回だけ入る。

### 設定ファイル

ファイルの検査には、`TEXTLINT_CONFIG`、プロジェクトの `.textlintrc.json` か `.textlintrc`、同梱の `config/textlintrc.json` の順に探した設定を使う。答えの検査には、誤検知の少ない `config/chat.json` を使う。

検査は @textlint/kernel で行い、同梱のプリセットとフィルタだけを読み込む。プロジェクトの設定は、同梱ルールの有効・無効とオプションとしてだけ使うので、設定からプロジェクトのコードが実行されることはない。同梱にないルールの名前は無視して表示する。

### 依存

- Python 3.9 以上（フックとスキル、yomiyasu）
- bun 1.3 以上（textlint。kernel ランナーは TypeScript で、bun でそのまま動く）
- textlint の依存は、初回に `~/.cache/ja-writing-textlint/<ロックファイルのハッシュ>/` へ入れる。コマンドは `bun install --frozen-lockfile --production --ignore-scripts` で、バージョンは `skills/textlint/bun.lock` で固定している

## productivity

作業を進めるためのスキルをまとめたプラグイン。

- `handoff`: 今の会話を、次のエージェントが引き継ぐための文書にまとめる（[mattpocock/skills](https://github.com/mattpocock/skills/tree/main/skills/productivity/handoff)の写し）。Claude Code では `/productivity:handoff [次のセッションでやること]` で呼ぶ

入れ方は ja-writing と同じで、プラグイン名を `productivity` にする。

## 上流から写したスキル

yomiyasu と handoff は依存として取り込まず、見直したコミットを写している。上流の変更は自動では入らない。写す元は `tools/vendored.json` にまとめている。

ファイルには手を加えずに写し、写したコミットは写した先の `SOURCE.md` に書く。ライセンスの本文も `LICENSE` として一緒に写す。更新するときは、新しいコミットを見直してから写し直す。

```
git clone <tools/vendored.json の repository> /tmp/upstream
git -C /tmp/upstream checkout <見直したコミット>
uv run python tools/vendor_skill.py <tools/vendored.json の destination> /tmp/upstream
```

git に記録されたファイルだけを写し、シンボリックリンクは拒否する。新しい写しが完成してから既存の写しと置き換えるので、途中で失敗しても既存の写しは残る。

## 開発

開発に使うツール（uv、Python、bun、semgrep、git）は `flake.nix` の開発シェルで用意し、バージョンは `flake.lock` で固定する。CI も同じシェルで動く。Python の依存は `uv.lock`、TypeScript の依存は `bun.lock` で固定する。

```
nix develop
uv sync
bun install --frozen-lockfile --ignore-scripts --cwd plugins/ja-writing/skills/textlint
```

CI と同じ検査は次のとおり。

| 対象 | コマンド | 内容 |
| --- | --- | --- |
| Python | `uv run ruff format --check .` と `uv run ruff check .` | ruff の全ルール（preview を含む） |
| Python | `uv run mypy` | strict と追加のエラーコード |
| Python | `uv run basedpyright` | 全ルール（`all`） |
| Python | `uv run pytest` と `uv run --python 3.9 pytest` | 警告をエラーにし、分岐カバレッジ 100% を求める |
| TypeScript | `bun --bun run --cwd plugins/ja-writing/skills/textlint check` | tsc（strict と追加フラグ）、oxfmt、oxlint（nursery を含む全カテゴリ、型を使うルールを含む） |
| 全体 | `semgrep scan ...`（引数は `.github/workflows/ci.yml`） | semgrep の python・security-audit・secrets・github-actions・typescript |

ruff と oxlint で外したルールは、それぞれ `pyproject.toml` と `.oxlintrc.json` に理由を書いている。上流から写したスキルは手を加えないので検査しない。
