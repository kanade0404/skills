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
// Rewrite only the one shape codex actually rejects (a bare `"*"` key mapped
// to any non-`deny` access) to `"./**"` — one of the exact forms
// rulesync's own `WORKSPACE_WIDE_WRITE_PATTERNS` set already treats as
// workspace-wide (`.`, `./`, `**`, `./**`) and, empirically (`codex exec`
// against this repo), the only one of that set codex-cli's own schema
// actually accepts here: a bare `"**"` still trips the same "only supports
// `deny`" rejection as `"*"` (codex treats it as a single glob path segment,
// not a subtree marker), and a bare `"/**"` is rejected too ("subpath `` must
// be a descendant path") because there's no path component before the
// wildcard. `"./**"` supplies that leading component and loads cleanly.
// Scope, not value, is what keeps the two other `"*"` keys in this file safe:
// `"*" = "allow"` under `[permissions.rulesync.network.domains]` is a domain
// matcher in a different table and is never visited, and `"*" = "deny"` inside
// a `:workspace_roots` table is left alone because `deny` is exactly the one
// access codex-cli does accept for a bare `*` glob.
export function fixCodexWorkspaceRootsCatchAll(outRoot) {
  const codexConfigPath = join(outRoot, '.codex', 'config.toml');
  if (!existsSync(codexConfigPath)) return;
  const original = readFileSync(codexConfigPath, 'utf8');
  const fixed = rewriteCatchAllEntries(original);
  requireNoCatchAllSurvivors(fixed, codexConfigPath);
  if (fixed !== original) writeFileSync(codexConfigPath, fixed);
}

// The exact bytes rulesync emits today (`"*" = "write"`) are not a contract, so
// none of these pin spacing: whitespace around `=` is free-form, either TOML
// quote style counts, and a trailing inline comment is tolerated.
const TABLE_HEADER = /^\s*\[\[?([^\]]*?)\]\]?\s*(?:#.*)?$/;
const IS_WORKSPACE_ROOTS = /\.filesystem\.["']?:workspace_roots["']?$/;
const QUOTED_KEY = /^\s*(?:"([^"]*)"|'([^']*)')\s*=/;
const QUOTED_KEY_TOKEN = /^(\s*)(?:"[^"]*"|'[^']*')/;
const BARE_KEY = /^\s*[A-Za-z0-9_-]+\s*=/;
const QUOTED_VALUE = /=\s*(?:"([^"]*)"|'([^']*)')\s*(?:#.*)?$/;

const entryKey = (line) => {
  const m = QUOTED_KEY.exec(line);
  return m ? (m[1] ?? m[2]) : null;
};
const entryValue = (line) => {
  const m = QUOTED_VALUE.exec(line);
  return m ? (m[1] ?? m[2]) : null;
};

// Walk the TOML line by line, handing `visit` only the entry lines that sit
// under a table whose dotted header `selectTable` accepts (table headers,
// blanks and comments are passed through). Entries before the first header
// belong to no table and are never visited. Returns the mapped lines.
function mapTableEntries(toml, selectTable, visit) {
  let inSelectedTable = false;
  return toml.split('\n').map((line, index) => {
    const header = TABLE_HEADER.exec(line);
    if (header) {
      inSelectedTable = selectTable(header[1].trim());
      return line;
    }
    if (!inSelectedTable) return line;
    const text = line.trim();
    if (text === '' || text.startsWith('#')) return line;
    return visit(line, index);
  });
}

// The `:workspace_roots` slice of the above. Shared by the rewrite and the
// gate so the gate can never look at a different set of lines than the rewrite.
const mapWorkspaceRootsEntries = (toml, visit) =>
  mapTableEntries(toml, (header) => IS_WORKSPACE_ROOTS.test(header), visit);

// Replace only the key token, leaving spacing, value quoting and any trailing
// comment byte-identical — the generated tree is diffed byte-for-byte by
// `rulesync-sync.mjs --check`, so this must not reformat anything it touches.
function rewriteCatchAllEntries(toml) {
  return mapWorkspaceRootsEntries(toml, (line) => {
    const value = entryValue(line);
    if (entryKey(line) !== '*' || value === null || value === 'deny') return line;
    return line.replace(QUOTED_KEY_TOKEN, '$1"./**"');
  }).join('\n');
}

// Every entry under a `:workspace_roots` table that codex-cli would reject at
// config load, plus every entry whose shape this patch cannot read well enough
// to rule that out. Returns `{ lineNo, text }` for each.
function findCatchAllSurvivors(toml) {
  const survivors = [];
  mapWorkspaceRootsEntries(toml, (line, index) => {
    const key = entryKey(line);
    // A TOML bare key is `[A-Za-z0-9_-]+`, which can never be `*` — such a
    // line is definitively not the catch-all, whatever its value looks like.
    if (key === null && BARE_KEY.test(line)) return line;
    if (key !== null && key !== '*') return line;
    // From here the key is either `*` or unreadable. Only a value this patch
    // can positively read as `deny` clears it: codex accepts a bare `*` glob
    // for `deny` alone.
    if (key === '*' && entryValue(line) === 'deny') return line;
    survivors.push({ lineNo: index + 1, text: line.trim() });
    return line;
  });
  return survivors;
}

// Fail-loud gate on the patched content. Without it the only failure mode is
// silence: `rulesync-sync.mjs --check` diffs freshly generated output against
// the committed output, so a rewrite that quietly stops matching (a
// RULESYNC_VERSION bump changing the TOML serialization, say) produces the
// same codex-rejecting config on both sides and --check still reports "up to
// date", while `codex` refuses to start in this repo.
function requireNoCatchAllSurvivors(toml, codexConfigPath) {
  const survivors = findCatchAllSurvivors(toml);
  if (survivors.length === 0) return;
  console.error(
    `rulesync-sync: ${codexConfigPath} still carries a bare "*" catch-all under a `
    + ':workspace_roots table after the post-generation patch, or an entry there whose '
    + 'shape the patch cannot read. codex-cli rejects that at config load ("filesystem '
    + 'glob path `*` only supports `deny` access; use an exact path or trailing `/**` '
    + 'for `write` subtree access"), so refusing to write it beats committing a config '
    + 'codex cannot load. Update the rewrite in scripts/codex-workspace-roots.mjs to '
    + "match rulesync's current output (see RULESYNC_VERSION in "
    + 'scripts/rulesync-sync.mjs). Offending line(s):',
  );
  for (const s of survivors) console.error(`  line ${s.lineNo}: ${s.text}`);
  process.exit(1);
}
