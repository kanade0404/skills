# hooks/

rulesync 配布元の **hooks** feature 枠。

- consumer は `rulesync fetch kanade0404/skills@<tag> --features hooks` で取得し、
  `rulesync generate --targets claudecode,codexcli` で各ツール形式
  （Claude Code = settings 形式 / Codex = `.codex/hooks.json` 形式）へ変換する。
- ここには **rulesync canonical 形式** のフック定義と、必要な補助スクリプトを置く。
- 現状 placeholder（未収録）。dotfiles `.claude/hooks/`（bash analyzer, commit hook）
  からの移行は次フェーズ。

## `claude-code-hooks.json`（この repo 専用、配布 feature ではない）

`hooks/claude-code-hooks.json` は上記の rulesync canonical hooks feature とは別物。
Claude Code の `.claude/settings.json` の `hooks` キーにそのまま代入できる形式
（`{"PreToolUse": [...]}` 等）で書かれた **この repo 自身の repo-local hooks
ソース**であり、`node scripts/rulesync-sync.mjs` が rulesync の `generate` 実行後に
読み込み、生成済み `.claude/settings.json` へマージ注入する。

- rulesync の `--features hooks` fetch では配布されない（consumer には渡らない）。
- `.claude/settings.json` を直接編集しても `scripts/rulesync-sync.mjs` の再実行で
  上書きされて消えるため、変更はこのファイル側で行う。
- 現在の内容: pr-monitor の state ファイル (`.claude/.pr-monitor/*`) への
  Write/Edit/MultiEdit 直書きを PreToolUse hook で拒否し、
  `prm state-init`/`prm state-merge` (read-modify-write) に誘導するガード。
