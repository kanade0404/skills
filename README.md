# skills

自分用に書いた Claude Code / Codex 向け Agent Skill 等のカタログ兼 **配布元リポジトリ**。

各プロジェクトへは [rulesync](https://github.com/dyoshikawa/rulesync) で配布する
（`rulesync fetch` でタグ固定して取り込み、`rulesync generate` で各ツールの設定を生成）。

> **配布方式を APM (`microsoft/apm`) から rulesync へ移行。**
> 理由: rulesync の方が成熟（双方向 import / CI ドリフト検出 / `--simulate-commands`
> で Codex の slash-command 非対応を吸収）で、生成器であり Claude ネイティブ plugin を
> 置換しない。Codex も即対応できる。

サードパーティ製 skill は原則ここに vendor しない。consumer 側で upstream リポジトリを
直接 `rulesync fetch` する（重複と更新追従の手間を避ける方針は維持）。明示的に
copy-in した例外は、該当 skill ディレクトリ内に出典とライセンスを残す。

## 構造

rulesync の `fetch` は配布元リポジトリの **トップレベルの feature ディレクトリ**
（`.rulesync/` ではなくルート直下）を読む。

```text
.
├── README.md / CLAUDE.md / AGENTS.md   # このリポ自体の開発ガイド（配布対象外）
├── .gemini/settings.json               # Gemini CLI に AGENTS.md を読ませる手書き設定（配布対象外・生成物ではない）
├── skills/        # Agent Skills
│   └── <name>/
│       ├── SKILL.md          # 必須
│       ├── references/*.md   # progressive disclosure 第3層
│       ├── evals/*.{json,jsonl}
│       ├── scripts/*.py
│       └── assets/*
├── subagents/     # サブエージェント配布枠
├── commands/      # スラッシュコマンド配布枠
├── hooks/         # フック配布枠
└── rules/         # 横断指示ルール配布枠
```

各 feature ディレクトリは rulesync の配布単位。空に近いディレクトリは将来の配布枠として
README 等の placeholder だけを置くことがある。

このリポジトリ自身の `.gemini/settings.json` は feature ディレクトリではないため
`rulesync fetch` の対象にならず、**consumer には配布されない**。consumer 側で Gemini CLI
に `AGENTS.md` を読ませたい場合は、そのリポジトリに自分で置く —「Gemini CLI を使う場合」を参照。

## 収録内容

`skills/<name>/` が収録 skill の source of truth。README には個別 skill の一覧や件数を
持たせない。追加・削除・説明変更は各 `SKILL.md` の frontmatter とディレクトリ構造で表現する。

## プロジェクトでの利用 (rulesync)

```bash
# 1. 取り込み（タグ固定推奨。private repo は GITHUB_TOKEN/GH_TOKEN）
rulesync fetch kanade0404/skills@<tag> --features skills,subagents,commands,hooks,rules
#   サードパーティ skill はそれぞれ upstream を直接
rulesync fetch planetscale/database-skills@<tag> --features skills

# 2. 各ツール設定を生成（Codex の command/subagent 非対応は simulate で吸収）
rulesync generate --targets claudecode,codexcli --simulate-commands --simulate-subagents

# 3. 生成物（.claude/ .codex/ .agents/skills/ CLAUDE.md AGENTS.md）をコミット

# 4. CI でドリフト検出
rulesync generate --targets claudecode,codexcli --check
```

- **更新**は `rulesync fetch …@<新タグ>` を再実行 → `generate` し直してコミット。
  vendoring モデルのため中央更新は自動伝播しない。**タグ運用で固定**するのが定石。
- `--conflict skip` で consumer 固有ファイルとの衝突を回避できる（既定は `overwrite`）。

### Gemini CLI を使う場合

Gemini CLI は既定では `GEMINI.md` しか context ファイルとして読まないため、上の手順で
生成した `AGENTS.md` はそのままでは読まれない。rulesync に `gemini` ターゲットは
存在しない（Gemini CLI 系譜は `antigravity-cli` / `antigravity-ide` に統合済み）ので、
`--targets` では解決できない。consumer リポジトリのルートに **手書きで**
`.gemini/settings.json` を置き、context ファイル名に `AGENTS.md` を足す。

```json
{
  "context": {
    "fileName": ["GEMINI.md", "AGENTS.md"]
  }
}
```

- キーは Gemini CLI の settings v2（ネスト形式）の `context.fileName`
  (`string | string[]`)。フラットな `contextFileName` は旧表記で、settings v2 に対応
  していない古い Gemini CLI はネスト形式を読まない。観測された範囲ではエラー表示が無く
  `AGENTS.md` が読まれないだけなので気付きにくい（公式に文書化された挙動ではない）。
  効いていないと感じたら CLI を settings v2 対応版へ更新し、v1 の設定が残っているなら
  Gemini CLI 側のマイグレーションを走らせる。旧表記の `contextFileName` を併記して
  試すのは避ける — 両キーが同時に存在するときの優先順位は文書化されておらず、
  自動マイグレーションと干渉しうる。
- **効いているかは Gemini CLI の `/memory show` で確認する。** このコマンドは
  読み込まれた階層 context の連結内容を表示するので、そこに `AGENTS.md` の中身が
  現れなければこの設定は効いていない。settings v2 の `context.fileName` を解釈する
  最小 CLI バージョンは上流ドキュメントに明記が無いため、バージョン番号ではなく
  この確認手順で判定する。
- これは rulesync の**生成物ではない手書き設定**なので、上の手順 3 の生成物一覧とは
  別枠。consumer 側で 1 回作成してコミットすれば、上記手順の
  `--targets claudecode,codexcli` では以後 `rulesync generate` でも `--check` でも
  触られない（手順 4 のドリフト検出の対象外）。他の target を足すなら要確認 —
  `antigravity-cli` / `antigravity-ide` は project scope ではルートの `AGENTS.md` と
  `.agents/rules/` に出力するため、手順 2 で codexcli が生成した `AGENTS.md`
  （まさにここで Gemini に読ませようとしているファイル）を上書きする。
  `.gemini/GEMINI.md` や `.gemini/antigravity-cli/settings.json` というパスは
  これらの target の **global scope 専用**（`~/.gemini/` 配下）で、リポジトリの
  `.gemini/` ではないため、この手書き設定とは衝突しない。なお
  `scripts/rulesync-sync.mjs` の `RULESYNC_VERSION` 時点では、`.gemini/settings.json`
  そのものを出力する target は無い。
- 配列に `GEMINI.md` を残すのは、Gemini 固有の追記をしたくなったときの余地。
  このリポジトリからは `GEMINI.md` を配布せず、ファイルが無ければ単にスキップされる。
- project scope（`<repo>/.gemini/settings.json`）は user scope（`~/.gemini/settings.json`）
  を上書きする。リポジトリ全員に効かせたいならリポジトリ側に置く。

## このリポの配布（メンテナ向け）

- skill 追加/更新 → `skills/<name>/` を変更する。README は配布方式や運用ポリシーが変わるときだけ更新する。
- ディレクトリ名 = `SKILL.md` の `name` frontmatter を一致させる。
- リリースは git タグ（例 `v1.1.0`）。consumer は `kanade0404/skills@vX.Y.Z` で固定取得。
