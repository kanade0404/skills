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
- 現在の内容:
  - pr-monitor の state ファイル (`.claude/.pr-monitor/*`) への
    Write/Edit/MultiEdit 直書きを PreToolUse hook で拒否し、
    `prm state-init`/`prm state-merge` (read-modify-write) に誘導するガード。
  - SessionStart hook: `scripts/session-setup.sh` を起動し、この checkout の
    nix devshell (`flake.nix`) をバックグラウンドで build する
    (direnv/nix が無い環境では警告を出すだけで exit 0、セッション開始をブロックしない)。
    `direnv allow` は既定では実行しない — このリポジトリは任意の PR ブランチを
    worktree に checkout する自動フローがあり、無条件 allow は direnv の
    trust-on-first-use を無効化するため。環境変数 `SKILLS_DIRENV_AUTO_ALLOW=1` を
    設定したセッションでのみ自動 allow する (未設定なら手動で
    `direnv allow <root>` を促す警告を出す)。

## `claude-code-env.json`

Claude Code の `.claude/settings.json` の `env` キーにそのまま代入できる形式で
書かれた、この repo 自身の repo-local な環境変数ソース。`claude-code-hooks.json`
と同じ契約で `node scripts/rulesync-sync.mjs` が生成済み `.claude/settings.json`
へマージ注入する（配布 feature には含まれない）。

- 現在の内容: `NO_COLOR=1` / `CLICOLOR_FORCE=0` — `CLICOLOR_FORCE=1` を継承する
  環境では `gh` の生 JSON 出力が pipe 先でも ANSI 色付けされ下流の jq を静かに
  壊すため（実測 3+ 回）、セッション全体で端末装飾を構造的に無効化する。
