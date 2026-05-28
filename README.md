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

## このリポの配布（メンテナ向け）

- skill 追加/更新 → `skills/<name>/` を変更する。README は配布方式や運用ポリシーが変わるときだけ更新する。
- ディレクトリ名 = `SKILL.md` の `name` frontmatter を一致させる。
- リリースは git タグ（例 `v1.1.0`）。consumer は `kanade0404/skills@vX.Y.Z` で固定取得。
