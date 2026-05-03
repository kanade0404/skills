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
└── research-practices/SKILL.md
```

## 収録 skill

| Skill | 出所 |
| --- | --- |
| `skill-builder` | 自作（`agegis` で運用していたもの） |
| `test-review` | 自作（`agegis` で運用していたもの） |
| `empirical-prompt-tuning` | 自作（`agegis` で運用していたもの） |
| `research-practices` | 自作（`agegis` で運用していたもの） |

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
