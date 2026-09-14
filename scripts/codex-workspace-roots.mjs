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
// That same scoping is also the rewrite's blind spot, so two gates run over the
// patched content before it is written: `requireNoCatchAllSurvivors`, scoped
// exactly as the rewrite is, and then `requireNoUnrecognizedFilesystemCatchAlls`,
// a coarser pass over every filesystem-ish table that catches what a renamed
// header would hide from both. See the latter for what they do and do not
// guarantee.
export function fixCodexWorkspaceRootsCatchAll(outRoot) {
  const codexConfigPath = join(outRoot, '.codex', 'config.toml');
  if (!existsSync(codexConfigPath)) return;
  const original = readFileSync(codexConfigPath, 'utf8');
  const fixed = rewriteCatchAllEntries(original);
  requireNoCatchAllSurvivors(fixed, codexConfigPath);
  requireNoUnrecognizedFilesystemCatchAlls(fixed, codexConfigPath);
  if (fixed !== original) writeFileSync(codexConfigPath, fixed);
}

// The exact bytes rulesync emits today (`"*" = "write"`) are not a contract, so
// none of these pin spacing: whitespace around `=` is free-form, either TOML
// quote style counts, and a trailing inline comment is tolerated.
const TABLE_HEADER = /^\s*\[\[?([^\]]*?)\]\]?\s*(?:#.*)?$/;
const IS_WORKSPACE_ROOTS = /\.filesystem\.["']?:workspace_roots["']?$/;
// Deliberately loose, and only ever used to *reject*, never to rewrite: any
// dotted header that mentions a filesystem or workspace-roots segment in any
// spelling (`:workspaceRoots`, `workspace-roots`, a reparented
// `permissions.rulesync.fs.":workspace_roots"`). See
// `requireNoUnrecognizedFilesystemCatchAlls` for why this width is safe.
const IS_FILESYSTEM_ADJACENT = /filesystem|workspace[_-]?roots/i;
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

// Second net. Everything above — the rewrite and the gate that checks it —
// only ever looks inside a table header matching IS_WORKSPACE_ROOTS, so the
// two share one blind spot: if rulesync renames that header, the offending
// entry moves out of reach of both. Nothing is rewritten, nothing is reported,
// `--check` diffs two equally broken trees and reports "up to date", and the
// first symptom is codex-cli refusing to start. This pass re-reads the same
// patched content with the header requirement relaxed to "mentions filesystem
// or workspace roots at all" and rejects any bare `"*"` catch-all left there.
//
// It does not re-exclude the recognized `:workspace_roots` tables, because
// `requireNoCatchAllSurvivors` has already exited on anything wrong inside
// them by the time this runs — the two nets must stay in that order, which
// `test_names_the_renamed_table_distinctly_from_a_recognized_survivor` pins
// (swap them and a recognized survivor gets reported with this message and its
// wrong remedy).
//
// WHAT THIS GUARANTEES: a bare `"*"` key whose value is not readable as
// `deny`, sitting under a line-form table header that names a filesystem or
// workspace-roots segment in any spelling, stops the build.
//
// WHAT IT DOES NOT: it is a rejection net, not a rewrite — it never repairs
// what it finds, so a header rename still needs IS_WORKSPACE_ROOTS taught the
// new name. It is scoped to filesystem-ish *line-form* table headers, so it
// misses a catch-all rulesync moves into an inline table
// (`filesystem = { ... "*" = "write" ... }`), a multi-line array-of-tables
// value, or a filesystem table renamed to something naming neither concept
// (`permissions.rulesync.paths`). It requires the key to read positively as
// `"*"`, so an unparseable *key* under an unrecognized table passes — the
// stricter "shape I cannot read is a survivor" rule stays confined to tables
// we positively recognize, where a false fire is a loud local bug rather than
// a permanent block on generation. Do not treat a green run as proof the
// generated config loads; `codex doctor` is the real check.
//
// The scope is the tradeoff: matching every `"*"` in the file would fire on
// `[permissions.rulesync.network.domains]`'s `"*" = "allow"` (a domain
// matcher, not a path glob) and on any future non-filesystem namespace with
// the same idiom, which would wedge generation permanently. Filesystem-ish
// headers keep the net wide enough for the realistic drift — a renamed or
// reparented workspace-roots table — without inventing a policy for keys this
// patch has no business judging.
function findUnrecognizedFilesystemCatchAlls(toml) {
  const survivors = [];
  mapTableEntries(
    toml,
    (header) => IS_FILESYSTEM_ADJACENT.test(header),
    (line, index) => {
      // `deny` is the one access codex-cli accepts for a bare `*` glob; an
      // unreadable value cannot be shown to be `deny`, so it counts against us.
      if (entryKey(line) !== '*' || entryValue(line) === 'deny') return line;
      survivors.push({ lineNo: index + 1, text: line.trim() });
      return line;
    },
  );
  return survivors;
}

function requireNoUnrecognizedFilesystemCatchAlls(toml, codexConfigPath) {
  const survivors = findUnrecognizedFilesystemCatchAlls(toml);
  if (survivors.length === 0) return;
  console.error(
    `rulesync-sync: ${codexConfigPath} carries an unrecognized filesystem catch-all `
    + 'shape: a bare "*" key with non-deny access under a filesystem table that the '
    + ':workspace_roots rewrite never visited, so nothing repaired it. codex-cli '
    + 'rejects a bare "*" filesystem glob for anything but `deny` at config load, '
    + 'so refusing to write it beats committing a config '
    + 'codex cannot load. This usually means rulesync renamed or reparented the '
    + 'workspace_roots table: widen IS_WORKSPACE_ROOTS in '
    + 'scripts/codex-workspace-roots.mjs to cover the new header (see RULESYNC_VERSION '
    + 'in scripts/rulesync-sync.mjs). Offending line(s):',
  );
  for (const s of survivors) console.error(`  line ${s.lineNo}: ${s.text}`);
  process.exit(1);
}
