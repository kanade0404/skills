// Post-generation patch for the `.codex/config.toml` that `rulesync generate`
// emits, extracted from `scripts/rulesync-sync.mjs` so it can be exercised
// directly by `tests/test_codex_workspace_roots.py` (importing rulesync-sync.mjs
// itself would run its whole staging + `npx rulesync generate` pipeline as an
// import side effect).
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

// `permissions.json`'s `read`/`edit`/`write` categories use the same bare `"*"`
// catch-all token as every other category (e.g. `bash: {"*": "allow"}`) to mean
// "all paths". `rulesync@9.1.1`'s codexcli converter only recognizes `.`, `./`,
// `**` and `./**` as workspace-wide filesystem patterns (see its
// `WORKSPACE_WIDE_WRITE_PATTERNS` set); a bare `"*"` pattern falls through that
// check and is emitted verbatim into the generated `.codex/config.toml`'s
// `[permissions.rulesync.filesystem.":workspace_roots"]` table as `"*" =
// "read"` or `"*" = "write"`. codex-cli's own config schema rejects that at
// load time: "filesystem glob path `*` only supports `deny` access; use an
// exact path or trailing `/**` for `write` subtree access" — so `codex`
// refuses to start in this repo until the entry is rewritten. There is no
// per-target override in rulesync's `permissions.json` schema (it's one flat
// `permission` object shared by every target), so the fix has to be a
// post-generation patch here rather than a change to the source permissions —
// changing the source pattern itself would also change what gets generated
// for every other target (e.g. Claude Code's `.claude/settings.json`).
// Rewrite only the one shape codex actually rejects (a bare `"*"` mapped to a
// non-deny `read`/`write` action) to `"./**"` — one of the exact forms
// rulesync's own `WORKSPACE_WIDE_WRITE_PATTERNS` set already treats as
// workspace-wide (`.`, `./`, `**`, `./**`) and, empirically (`codex exec`
// against this repo), the only one of that set codex-cli's own schema
// actually accepts here: a bare `"**"` still trips the same "only supports
// `deny`" rejection as `"*"` (codex treats it as a single glob path segment,
// not a subtree marker), and a bare `"/**"` is rejected too ("subpath `` must
// be a descendant path") because there's no path component before the
// wildcard. `"./**"` supplies that leading component and loads cleanly.
// `"*" = "allow"` under `[permissions.rulesync.network.domains]` uses a
// different value and is untouched by this regex.
export function fixCodexWorkspaceRootsCatchAll(outRoot) {
  const codexConfigPath = join(outRoot, '.codex', 'config.toml');
  if (!existsSync(codexConfigPath)) return;
  const original = readFileSync(codexConfigPath, 'utf8');
  const fixed = original.replace(/^"\*" = "(read|write)"$/m, '"./**" = "$1"');
  if (fixed !== original) writeFileSync(codexConfigPath, fixed);
}
