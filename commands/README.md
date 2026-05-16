# commands/

rulesync 配布元の **commands**（スラッシュコマンド）feature 枠。

- consumer は `rulesync fetch kanade0404/skills@<tag> --features commands` で取得し、
  `rulesync generate --targets claudecode,codexcli --simulate-commands` で変換する。
- Codex は slash command 非対応のため `--simulate-commands` で吸収される
  （再利用ロジックは可能な限り `skills/` に書くほうが両ツールでネイティブに動く）。
- ここには **rulesync canonical 形式** のコマンド定義を置く。
- 現状 placeholder（未収録）。移行は次フェーズ。
