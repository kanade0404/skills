# skills

自分用に書いた Claude Code / Codex Skill のカタログ。
プロジェクトへの配布は [microsoft/apm](https://github.com/microsoft/apm) を想定。

サードパーティ製の skill はここに vendor せず、consumer 側の `apm.yml` から
upstream を直接依存に書く方針（重複と更新追従の手間を避ける）。

## 構造

```
.
├── README.md
├── skill-builder/SKILL.md
├── test-review/SKILL.md
├── empirical-prompt-tuning/SKILL.md
├── research-practices/SKILL.md
├── pr-review-respond/SKILL.md
├── verify-done/SKILL.md
├── tidy-first/SKILL.md
├── tdd/SKILL.md
├── design/SKILL.md
├── software-design/SKILL.md
├── design-review/SKILL.md
├── adr-writer/SKILL.md
├── code-review/SKILL.md
└── ci-self-heal/SKILL.md
```

## 収録 skill

| Skill | 出所・用途 |
| --- | --- |
| `skill-builder` | メタスキル: skill 新規作成 + trigger / quality 改善ループ |
| `test-review` | テストコード review (Khorikov 4 属性 + Meszaros 17 smells + AI 生成パターン) |
| `empirical-prompt-tuning` | プロンプト / skill の subagent dispatch 経験的評価 |
| `research-practices` | リサーチ実践 (情報源評価 / 思考フレームワーク / レポーティング) |
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

## プロジェクトでの利用 (apm.yml)

自作 skill はこのリポから、サードパーティ skill は upstream から直接引く:

```yaml
name: your-project
version: 1.0.0
dependencies:
  apm:
    - kanade0404/skills/skill-builder
    - kanade0404/skills/test-review
    # サードパーティ例
    - planetscale/database-skills/skills/postgres
    - planetscale/database-skills/skills/mysql
```
