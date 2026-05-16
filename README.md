# skills

自分用に書いた Claude Code / Codex 向け Agent Skill 等のカタログ兼 **配布元リポジトリ**。

各プロジェクトへは [rulesync](https://github.com/dyoshikawa/rulesync) で配布する
（`rulesync fetch` でタグ固定して取り込み、`rulesync generate` で各ツールの設定を生成）。

> **配布方式を APM (`microsoft/apm`) から rulesync へ移行。**
> 理由: rulesync の方が成熟（双方向 import / CI ドリフト検出 / `--simulate-commands`
> で Codex の slash-command 非対応を吸収）で、生成器であり Claude ネイティブ plugin を
> 置換しない。Codex も即対応できる。

サードパーティ製 skill はここに vendor しない。consumer 側で upstream リポジトリを
直接 `rulesync fetch` する（重複と更新追従の手間を避ける方針は維持）。

## 構造

rulesync の `fetch` は配布元リポジトリの **トップレベルの feature ディレクトリ**
（`.rulesync/` ではなくルート直下）を読む。

```
.
├── README.md / CLAUDE.md / AGENTS.md   # このリポ自体の開発ガイド（配布対象外）
├── skills/        # Agent Skills（収録済み・自作15）
│   └── <name>/
│       ├── SKILL.md          # 必須
│       ├── references/*.md   # progressive disclosure 第3層
│       ├── evals/*.{json,jsonl}
│       ├── scripts/*.py
│       └── assets/*
├── subagents/     # サブエージェント（配布枠・未収録 / placeholder）
├── commands/      # スラッシュコマンド（配布枠・未収録 / placeholder）
├── hooks/         # フック（配布枠・未収録 / placeholder）
└── rules/         # 横断指示ルール（配布枠・未収録 / placeholder）
```

`subagents/ commands/ hooks/ rules/` は現状プレースホルダ（README のみ）。
コンテンツの移行は次フェーズ。

## 収録 skill

いずれも自作。`skills/<name>/` に配置し、rulesync で project-agnostic に配布する。

| Skill | 概要 |
| --- | --- |
| `skill-builder` | メタスキル: Claude Code skill 新規作成 + trigger / quality 改善ループ |
| `test-review` | テストコード review (Khorikov 4 属性 + Meszaros 17 smells + AI 生成パターン、言語・スタック非依存) |
| `empirical-prompt-tuning` | プロンプト / skill の subagent dispatch 経験的評価 |
| `research-practices` | リサーチ実践 (情報源評価 / 思考フレームワーク / レポーティング、CRAAP / SIFT / S0-S5 信頼度タグ) |
| `product-discovery` | 要求定義（要件定義の前段）。Outcome > Output で PRD を起こし、`prd-review` → `requirements` に渡す |
| `pr-review-respond` | CodeRabbit / Devin / 人間レビュアーのコメント verify-and-respond ループ |
| `verify-done` | 完了宣言前の最終 gate (Iron Law: NO COMPLETION CLAIMS WITHOUT FRESH EVIDENCE) |
| `tidy-first` | structural / behavioral 分離規律 (Kent Beck *Tidy First?*) |
| `tdd` | Test-Driven Development (RED-GREEN-REFACTOR + Verify RED gate) |
| `design` | 設計検討 (scratchpad で検討、決定のみ ADR に蒸留、spec を永続化しない) |
| `software-design` | 13 レンズの設計支援フレームワーク (PoSD / Immutable Data Model / TM法 / FP / DDD / TDD / RoP / FoSA / xUnit / CQRS / ES / ADR / Secure by Design) |
| `design-review` | `software-design` 成果物を別 agent に白紙で読ませてレビューする pair skill (severity 三分類 + 13 レンズ checklist) |
| `adr-writer` | Michael Nygard 形式 ADR の値判定 + 生成 |
| `code-review` | PR 起票前の subagent コードレビュー (severity 三分類) |
| `ci-self-heal` | CI 失敗の root-cause-first 自己修復ループ (3-failure architecture gate) |

## プロジェクトでの利用 (rulesync)

```bash
# 1. 取り込み（タグ固定推奨。private repo は GITHUB_TOKEN/GH_TOKEN）
rulesync fetch kanade0404/skills@v1.0.0 --features skills,subagents,commands,hooks,rules
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

- skill 追加/更新 → `skills/<name>/`、本 README の表を 1 行更新。
- ディレクトリ名 = `SKILL.md` の `name` frontmatter を一致させる。
- リリースは git タグ（例 `v1.1.0`）。consumer は `kanade0404/skills@vX.Y.Z` で固定取得。
