# hooks-local/

この repo 自身の運用専用の hooks ソース。`rules/` に対する `rules-local/` と同じ
役割・同じ命名規則: 配布 feature 枠 (`hooks/`) には置けない repo-local な内容を
ここに置き、`scripts/rulesync-sync.mjs` がこの repo 自身の生成物にのみ反映する。

- rulesync の `--features hooks` fetch では配布されない（consumer には渡らない）。
- 配布 feature ではないため rulesync canonical hooks 形式ではなく、対象ツールの
  ネイティブ形式でそのまま書く。

## `claude-code-hooks.json`

Claude Code の `.claude/settings.json` の `hooks` キーにそのまま代入できる形式
（`{"PreToolUse": [...]}` 等）で書かれた、この repo 自身の repo-local hooks
ソース。`node scripts/rulesync-sync.mjs` が rulesync の `generate` 実行後に
読み込み、生成済み `.claude/settings.json` へマージ注入する。

- `.claude/settings.json` を直接編集しても `scripts/rulesync-sync.mjs` の再実行で
  上書きされて消えるため、変更はこのファイル側で行う。
- 現在の内容: pr-monitor の state ファイル (`.claude/.pr-monitor/*`) への
  Write/Edit/MultiEdit 直書きを PreToolUse hook で拒否し、
  `prm state-init`/`prm state-merge` (read-modify-write) に誘導するガード。
