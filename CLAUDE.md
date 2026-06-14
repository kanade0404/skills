# CLAUDE.md

Skill カタログ兼 **rulesync 配布元**。consumer は
`rulesync fetch kanade0404/skills@<tag> --features skills,...` → `rulesync generate`
で各エージェントの設定を生成して使う。

- **コードでなく `skills/<name>/SKILL.md` を編集する場所**。build/test/lint は無い。
- **source of truth は `skills/<name>/`**。README / CLAUDE.md に skill 一覧・件数を重複させない。
- **配布の不変条件**: ディレクトリ名 = frontmatter `name`。
- **リリースは git タグ `vX.Y.Z`**（手順は [RELEASING.md](RELEASING.md)）。consumer は `@<tag>` で固定取得。
- **skill 作成・編集・trigger 評価の規範は `skills/skill-builder/SKILL.md`**（frontmatter / 500行 / negative space / eval ループの一次情報）。
- **サードパーティ skill は vendor しない**。copy-in 例外は skill ディレクトリ内に出典 + LICENSE を残す。
