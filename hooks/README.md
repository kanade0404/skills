# hooks/

rulesync 配布元の **hooks** feature 枠 (placeholder)。

- consumer は `rulesync fetch kanade0404/skills@<tag> --features hooks` で取得し、
  `rulesync generate --targets claudecode,codexcli` で各ツール形式
  （Claude Code = settings 形式 / Codex = `.codex/hooks.json` 形式）へ変換する。
- ここには **rulesync canonical 形式** のフック定義と、必要な補助スクリプトのみを置く。
  consumer が `--features hooks` で丸ごと fetch する対象のため、repo-local な
  生 Claude settings fragment はここに置いてはならない (`hooks-local/` 参照)。
- 現状 placeholder（未収録）。dotfiles `.claude/hooks/`（bash analyzer, commit hook）
  からの移行は次フェーズ。

repo-local hooks (この repo 自身の運用専用、配布されない) は
[`hooks-local/`](../hooks-local/) に置く。`rules/` と `rules-local/` の分離と同じ
理由・同じ命名: 配布 feature 枠と repo-local な生成入力を混在させない。
