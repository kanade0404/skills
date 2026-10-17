# skills

エージェント向けのプラグインとスキルを置くリポジトリ。Claude Code と Codex にはプラグインとして、その他のエージェントにはスキルとして入れる。

## ja-writing

日本語の文章を検査、推敲するプラグイン。次の 2 つのスキルと、書き込み後のフックを含む。

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

### 書き込み後のフック

Claude Code と Codex では、エージェントが `.md`・`.txt` を書き換えるたびに textlint を実行し、指摘をエージェントに返す。かなを含まないファイルは対象外にしている。

| 環境変数 | Claude Code の設定 | 意味 |
| --- | --- | --- |
| `TEXTLINT_HOOK_MODE` | `hook_mode` | `block`（既定）は直させる。`warn` は文脈として渡すだけ。`off` は検査しない |
| `TEXTLINT_TRUST_PROJECT` | `trust_project_toolchain` | `1` にすると、プロジェクトの `node_modules/textlint` と `.textlintrc.js` を使う |
| `TEXTLINT_CONFIG` | なし | 使う設定ファイルを指定する |

設定ファイルは `TEXTLINT_CONFIG`、プロジェクトの `.textlintrc.json` か `.textlintrc`、同梱の `config/textlintrc.json` の順に探す。プロジェクトのコードを実行しないよう、`TEXTLINT_TRUST_PROJECT` を設定しない限り JSON の設定だけを読み、ルール名にパスが書かれていれば実行を止める。YAML・JS の設定とプロジェクトの textlint も、信頼したときだけ使う。

### 依存

- Node.js 20.18 以上と npm（textlint）、Python 3.9 以上（yomiyasu）が必要
- textlint の依存は、初回に `~/.cache/ja-writing-textlint/<ロックファイルのハッシュ>/` へ `npm ci --ignore-scripts` で入れる。バージョンは `skills/textlint/package-lock.json` で固定している

### yomiyasu の更新

yomiyasu は依存として取り込まず、見直したコミットを写している。上流の変更は自動では入らない。各ファイルの先頭に参照元のコミットを書いている。更新するときは、新しいコミットを見直してから写し直す。

```
git clone https://github.com/nanaism/yomiyasu /tmp/yomiyasu
git -C /tmp/yomiyasu checkout <見直したコミット>
node tools/vendor-yomiyasu.mjs /tmp/yomiyasu
```
