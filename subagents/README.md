# subagents/

rulesync 配布元の **subagents** feature 枠。

- consumer は `rulesync fetch kanade0404/skills@<tag> --features subagents` で取得し、
  `rulesync generate --targets claudecode,codexcli --simulate-subagents` で
  各ツール形式（Claude Code = Markdown+frontmatter / Codex = TOML）へ変換する。
- ここには **rulesync canonical 形式** の subagent 定義を 1 ファイル 1 エージェントで置く。
- 現状 placeholder（未収録）。dotfiles `.claude-plugin/agents/` 等からの移行は次フェーズ。
