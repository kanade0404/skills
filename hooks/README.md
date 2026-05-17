# hooks/

rulesync 配布元の **hooks** feature 枠。

- consumer は `rulesync fetch kanade0404/skills@<tag> --features hooks` で取得し、
  `rulesync generate --targets claudecode,codexcli` で各ツール形式
  （Claude Code = settings 形式 / Codex = `.codex/hooks.json` 形式）へ変換する。
- ここには **rulesync canonical 形式** のフック定義と、必要な補助スクリプトを置く。
- 現状 placeholder（未収録）。dotfiles `.claude/hooks/`（bash analyzer, commit hook）
  からの移行は次フェーズ。
