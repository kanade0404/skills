#!/usr/bin/env node
// Materialize this repo's own AI-tool configs from its top-level feature dirs
// (dogfooding via rulesync). Source of truth = top-level `skills/` + `permissions.json`
// (+ `hooks-local/claude-code-hooks.json`, a repo-local, non-distributed hooks source —
// see hooks-local/README.md. Kept out of `hooks/`, the rulesync `hooks` feature slot,
// so a consumer's `--features hooks` fetch never picks up this repo-specific fragment).
// Generated (committed) outputs = `.claude/` (Claude Code) and `.agents/` + `.codex/` (Codex).
//
//   node scripts/rulesync-sync.mjs           # write generated outputs
//   node scripts/rulesync-sync.mjs --check   # fail (exit 1) if committed outputs are stale
//
// Uses node:fs for staging so it needs no shell `cp`/`rm`/`cd`. `.rulesync/` is the
// throwaway staging dir (gitignored) rulesync's `generate` reads from.
//
// Generation is single-path: `rulesync generate` always writes to a scratch temp
// output root first, then the repo-local hooks merge (below) is applied to that
// scratch copy, and only then is the result either materialized onto the repo
// (write mode) or diffed against the repo (--check mode). This way `--check`
// verifies the exact same artifact a real run would produce, hooks merge
// included, instead of relying on rulesync's own `--check` — which knows
// nothing about the post-generate hooks injection and would always flag
// `.claude/settings.json` as unexpectedly different.
import {
  rmSync, mkdirSync, cpSync, copyFileSync, statSync, existsSync,
  readFileSync, writeFileSync, readdirSync, mkdtempSync, chmodSync,
} from 'node:fs';
import { basename } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
// The `.codex/config.toml` catch-all patch lives in its own module purely so it
// has a test seam (see scripts/codex-workspace-roots.mjs); it is still applied
// from this script's single post-generate pipeline below.
import { fixCodexWorkspaceRootsCatchAll } from './codex-workspace-roots.mjs';
// Retired generated paths (outputs an abolished feature used to produce) live in
// their own module for the same reason — a test seam. See
// scripts/retired-generated-paths.mjs.
import {
  findRetiredGeneratedPaths,
  removeRetiredGeneratedPaths,
} from './retired-generated-paths.mjs';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const RULESYNC_VERSION = '9.1.1';
const check = process.argv.includes('--check');

// Generated trees that mirror source content 1:1, one subtree/file per source
// skill. When a source skill is deleted or renamed, `rulesync generate`
// simply omits it from the scratch output root — there is no "shrunk" file to
// diff against, the whole path is just absent. That's different from the
// single aggregated files this script also generates (`.claude/settings.json`,
// `.codex/config.toml`, `.codex/rules/rulesync.rules`): those still exist in
// the scratch output with different (smaller) content after a deletion, so
// `diffTree`'s plain content comparison already catches drift there. (Root
// `CLAUDE.md` / `AGENTS.md` used to belong to that aggregated set, but ADR
// 0018 removed the `rules` feature that produced them — generation no longer
// emits them at all, which is what `RETIRED_GENERATED_PATHS` in
// scripts/retired-generated-paths.mjs handles.)
// Restricting the stale-file walk (see
// `findStaleFiles` below) to exactly these three mirrored trees (rather than
// all of `.claude`/`.agents`) also keeps it from ever touching non-generated
// content that happens to live alongside them — e.g. `.claude/settings.json`
// itself, or the gitignored runtime state under `.claude/.pr-monitor/`.
const MIRRORED_DIRS = ['.claude/skills', '.agents/skills'];

// Stage the source-of-truth feature content into `.rulesync/` for `generate`.
// Only features with real content are staged; commands/hooks/subagents are
// placeholder-only (README without frontmatter) and would fail rulesync parsing.
// Python bytecode caches (skills/*/scripts/__pycache__/*.pyc) are gitignored
// but reappear on disk whenever `uv run python3 -m unittest discover -s tests`
// imports a skill's Python script (e.g. skills/retro/scripts/retro_scan.py).
// They must never enter the generate pipeline: staged into `.rulesync/skills`
// here, `rulesync generate` would mirror them into `.claude/skills/` and
// `.agents/skills/`, and a fresh checkout without that local cache would then
// have `--check` report the mirrored path as missing/stale. Filtering them
// out at this single staging entry point — the only place top-level `skills/`
// content enters the pipeline — keeps them out of `genOut` entirely, so both
// `diffTree` (walks `genOut`) and the write-mode `cpSync` overlay never see
// them either.
const isPycacheEntry = (name) => name === '__pycache__' || name.endsWith('.pyc');
const stage = join(ROOT, '.rulesync');
rmSync(stage, { recursive: true, force: true });
mkdirSync(stage, { recursive: true });
cpSync(join(ROOT, 'skills'), join(stage, 'skills'), {
  recursive: true,
  filter: (src) => !isPycacheEntry(basename(src)),
});
copyFileSync(join(ROOT, 'permissions.json'), join(stage, 'permissions.json'));

// Always generate into a scratch output root (never straight onto the repo, and
// never with rulesync's own `--check`) so both modes share one generation call.
const genOut = mkdtempSync(join(tmpdir(), 'rulesync-sync-'));
try {
  const args = [
    '-y', `rulesync@${RULESYNC_VERSION}`, 'generate',
    '--targets', 'claudecode,codexcli',
    '--features', 'skills,permissions',
    '--simulate-skills',
    '-o', genOut,
  ];
  execFileSync('npx', args, { cwd: ROOT, stdio: 'inherit' });

  mergeRepoLocalHooks(genOut);
  mergeRepoLocalEnv(genOut);
  restoreSourceExecutableBits(genOut);
  fixCodexWorkspaceRootsCatchAll(genOut);

  if (check) {
    // Compute stale/type-mismatch paths BEFORE diffing (not after): a
    // mirrored path that changed shape (directory <-> file) between the
    // previous and current generation makes `diffTree` try to read the
    // wrong type at that same relative path (EISDIR/ENOTDIR) before --check
    // even gets a chance to report it as stale (PR #78 review). `diffTree`
    // is told which paths are already-known type mismatches (via `stale`,
    // which includes them alongside plain missing-in-genOut paths) so it can
    // skip them instead of crashing.
    // The two stale kinds get distinct diagnostics: a mirrored path is stale
    // because its *source skill* moved or vanished (fixable by restoring or
    // accepting the rename), while a root file is stale because the feature
    // that produced it was abolished outright — no source can bring it back,
    // the only action is deletion. One shared message would send a reader of
    // the second case hunting for a deleted skill that never existed.
    const staleMirrored = findStaleFiles(genOut, ROOT);
    const staleRetired = findRetiredGeneratedPaths(genOut, ROOT);
    const stale = [...staleMirrored, ...staleRetired];
    const diffs = diffTree(genOut, ROOT, new Set(stale));
    if (diffs.length > 0 || stale.length > 0) {
      console.error(
        'rulesync-sync: generated outputs are stale (run `node scripts/rulesync-sync.mjs`):',
      );
      for (const d of diffs) console.error(`  ${d}`);
      for (const s of staleMirrored) {
        console.error(`  ${s} (stale — no longer generated in this shape; source skill likely deleted, renamed, or changed between file and directory)`);
      }
      for (const s of staleRetired) {
        console.error(`  ${s} (stale — root rule 由来の生成物は ADR 0018 で廃止済み — 削除対象)`);
      }
      process.exit(1);
    }
    console.log('rulesync-sync --check: up to date.');
  } else {
    // Remove stale paths BEFORE overlaying the freshly generated tree (not
    // after, as a plain "copy then sweep" order would do): a stale entry can
    // now be a directory that must replace a file (or vice versa) at the
    // same relative path, and `cpSync` cannot perform that type change over
    // an existing conflicting path even with `force: true`. Clearing it
    // first guarantees `cpSync` only ever writes into a location that is
    // either absent or already the same type.
    // Announce every removal. This branch deletes committed files (a whole
    // generated skill subtree, or a root file the abolished `rules` feature
    // used to produce), and a silent delete inside an otherwise additive
    // "regenerate" run reads as an unexplained deletion in the next
    // `git status` — say which path went and why while the reason is known.
    const staleMirrored = findStaleFiles(genOut, ROOT);
    const staleRetired = findRetiredGeneratedPaths(genOut, ROOT);
    for (const s of staleMirrored) {
      console.log(`rulesync-sync: removing ${s} (no longer generated; source skill deleted, renamed, or changed shape)`);
      rmSync(join(ROOT, s), { recursive: true, force: true });
    }
    removeRetiredGeneratedPaths(ROOT, staleRetired);
    cpSync(genOut, ROOT, { recursive: true });
    for (const dir of MIRRORED_DIRS) pruneEmptyDirs(join(ROOT, dir));
  }
} finally {
  rmSync(genOut, { recursive: true, force: true });
}

// `hooks-local/claude-code-hooks.json` holds a raw Claude Code settings.json `hooks`
// fragment (repo-local; not a rulesync-distributed feature — see hooks-local/README.md).
// It deliberately lives outside `hooks/` (the rulesync `hooks` feature slot) so a
// consumer's `rulesync fetch --features hooks` never receives this repo-specific
// fragment. Inject it as the generated `.claude/settings.json`'s `hooks` key,
// deterministically (JSON.parse always yields the same key order from a static
// source file, and `settings.hooks = ...` always appends `hooks` after the freshly
// generated `permissions` key), so repeated runs produce byte-identical output.
// Fail-closed reader for the repo-local settings fragments below: a missing
// source file is a valid state (both fragments are optional features — the
// caller skips the merge), but a file that EXISTS yet doesn't parse to a JSON
// object means the fragment would be merged as garbage or silently dropped —
// exit 1 loudly instead.
function readFragmentObject(sourcePath, what) {
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(sourcePath, 'utf8'));
  } catch (err) {
    console.error(`rulesync-sync: ${sourcePath} exists but is not valid JSON (${err.message})`);
    process.exit(1);
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    console.error(
      `rulesync-sync: ${sourcePath} must hold a JSON object (a settings.json \`${what}\` fragment), `
      + `got ${parsed === null ? 'null' : Array.isArray(parsed) ? 'an array' : typeof parsed}`,
    );
    process.exit(1);
  }
  return parsed;
}

// `existsSync` returns false for ANY failure (EACCES, EIO, ENOTDIR, ...), which
// would silently skip merging a fragment that actually exists but is unreadable
// — the generated settings.json would ship without it, symptom-free.
// Treat only ENOENT as "absent"; rethrow everything else.
function optionalFragmentExists(path) {
  try {
    statSync(path);
    return true;
  } catch (err) {
    if (err && err.code === 'ENOENT') return false;
    throw err;
  }
}

function mergeRepoLocalHooks(outRoot) {
  const hooksSource = join(ROOT, 'hooks-local', 'claude-code-hooks.json');
  const settingsPath = join(outRoot, '.claude', 'settings.json');
  if (!optionalFragmentExists(hooksSource)) return; // optional fragment — nothing to merge
  requireGeneratedSettings(settingsPath, hooksSource, 'hooks');
  const settings = JSON.parse(readFileSync(settingsPath, 'utf8'));
  settings.hooks = readFragmentObject(hooksSource, 'hooks');
  writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + '\n');
}

// A fragment source that EXISTS while the generated settings.json it must be
// merged into does NOT is a broken generation (e.g. the permissions feature
// stopped emitting `.claude/settings.json`): returning silently would ship a
// settings.json without the fragment this repo declares, with no symptom until
// the missing hooks/env bite downstream. Exit 1 loudly.
function requireGeneratedSettings(settingsPath, sourcePath, what) {
  if (existsSync(settingsPath)) return;
  console.error(
    `rulesync-sync: ${sourcePath} exists but ${settingsPath} was not generated; `
    + `cannot merge the \`${what}\` fragment (refusing to silently drop it)`,
  );
  process.exit(1);
}

// `hooks-local/claude-code-env.json` holds a raw Claude Code settings.json `env`
// fragment (repo-local, same non-distributed contract as claude-code-hooks.json
// above). Injected as the generated `.claude/settings.json`'s `env` key so every
// session in this repo runs with terminal decoration structurally disabled
// (`NO_COLOR=1`, `CLICOLOR_FORCE=0`): with `CLICOLOR_FORCE=1` inherited from the
// environment, `gh`'s raw JSON output gets ANSI-colored even when piped and
// silently breaks downstream `jq` (observed 3+ times). Key order stays
// deterministic: `env` is always appended after `permissions` and `hooks`.
function mergeRepoLocalEnv(outRoot) {
  const envSource = join(ROOT, 'hooks-local', 'claude-code-env.json');
  const settingsPath = join(outRoot, '.claude', 'settings.json');
  if (!optionalFragmentExists(envSource)) return; // optional fragment — nothing to merge
  requireGeneratedSettings(settingsPath, envSource, 'env');
  const settings = JSON.parse(readFileSync(settingsPath, 'utf8'));
  settings.env = readFragmentObject(envSource, 'env');
  writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + '\n');
}

// For a generated relative path like `.claude/skills/<name>/scripts/foo.sh`
// (or `.agents/skills/<name>/scripts/foo.sh`), returns the matching top-level
// `skills/<name>/scripts/foo.sh` source path if one exists, else null.
function sourceCounterpart(relPath) {
  const parts = relPath.split('/');
  if (parts.length < 3 || parts[1] !== 'skills') return null;
  const sourcePath = join(ROOT, 'skills', ...parts.slice(2));
  return existsSync(sourcePath) && statSync(sourcePath).isFile() ? sourcePath : null;
}

// `rulesync generate` re-serializes skill content and does not preserve the
// source scripts' executable bit (P1 in PR #78 review: generated
// `issue-driven-development/scripts/acquire-lock.sh` regressed 100755 ->
// 100644, breaking that skill's direct `"${CLAUDE_SKILL_DIR}/scripts/
// acquire-lock.sh"` invocation — unlike e.g. `pr-review-respond`/`pr-monitor`,
// which always invoke their scripts via `bash "${CLAUDE_SKILL_DIR}/scripts/..."`
// and so don't depend on the exec bit surviving materialize). For every
// generated file with a source counterpart (see `sourceCounterpart`), mirror
// the source's executable bit onto the generated copy before it is either
// materialized onto the repo or diffed against it in --check. Only the exec
// bit is OR'd in (never stripped) so this can't regress a file rulesync
// itself needs writable/non-executable.
function restoreSourceExecutableBits(outRoot, rel = '') {
  for (const entry of readdirSync(join(outRoot, rel), { withFileTypes: true })) {
    const relPath = rel ? join(rel, entry.name) : entry.name;
    if (entry.isDirectory()) {
      restoreSourceExecutableBits(outRoot, relPath);
      continue;
    }
    const sourcePath = sourceCounterpart(relPath);
    if (!sourcePath || (statSync(sourcePath).mode & 0o111) === 0) continue;
    const outPath = join(outRoot, relPath);
    chmodSync(outPath, statSync(outPath).mode | 0o111);
  }
}

// Recursively compares every file under `generatedRoot` against the same
// relative path under `targetRoot`. Only reports files that are new or
// changed (matches the non-deleting semantics of a plain `cpSync`
// materialize — this script never passes rulesync's `--delete`). Because
// this walk starts from `generatedRoot`, it can only ever find paths that
// still exist there; a path whose *source* skill was deleted or
// renamed has no counterpart in `generatedRoot` at all and is invisible to
// this walk no matter how it's phrased — that's what `findStaleFiles`
// below is for (the same problem from the other direction). Executable-bit
// drift is only compared for files with a source script counterpart
// (`restoreSourceExecutableBits` above) — comparing exec bits repo-wide
// would make --check depend on the runner's umask for the many non-script
// generated files.
//
// `staleSet` is the set of relative paths `findStaleFiles` already flagged
// (computed by the caller BEFORE this call — see the `--check` branch
// above). A path in that set may be a type mismatch (directory in one root,
// file in the other) at the same relative location; recursing into it or
// reading it as the wrong type would throw (EISDIR/ENOTDIR) before --check
// can even report the mismatch, so it's skipped here and left entirely to
// `findStaleFiles`'s own report (PR #78 review).
function diffTree(generatedRoot, targetRoot, staleSet, rel = '') {
  const diffs = [];
  for (const entry of readdirSync(join(generatedRoot, rel), { withFileTypes: true })) {
    const relPath = rel ? join(rel, entry.name) : entry.name;
    if (staleSet.has(relPath)) continue;
    if (entry.isDirectory()) {
      diffs.push(...diffTree(generatedRoot, targetRoot, staleSet, relPath));
      continue;
    }
    const targetPath = join(targetRoot, relPath);
    if (!existsSync(targetPath)) {
      diffs.push(`${relPath} (missing)`);
      continue;
    }
    if (
      readFileSync(join(generatedRoot, relPath), 'utf8') !== readFileSync(targetPath, 'utf8')
    ) {
      diffs.push(relPath);
      continue;
    }
    if (sourceCounterpart(relPath)) {
      const generatedIsExec = (statSync(join(generatedRoot, relPath)).mode & 0o111) !== 0;
      const targetIsExec = (statSync(targetPath).mode & 0o111) !== 0;
      if (generatedIsExec !== targetIsExec) diffs.push(`${relPath} (executable bit)`);
    }
  }
  return diffs;
}

// The inverse of diffTree: walks the TARGET (repo) side of each MIRRORED_DIRS
// root and reports every path that has no counterpart at the same relative
// path under `generatedRoot`, OR whose counterpart there exists but changed
// type (file <-> directory) at the same relative path. `diffTree` alone can
// never surface either case because it only ever walks paths that exist in
// `generatedRoot` in the first place — a source skill deleted or renamed
// leaves its old generated mirror behind, `--check` stays green, and (since
// materialize is a non-deleting `cpSync` overlay) `write` mode never removes
// it either, so agents keep seeing a skill that no longer has a source
// of truth. The type-mismatch case additionally matters because `cpSync`
// cannot overlay a directory onto an existing file, or a file onto an
// existing directory, even with `force: true` — an existence-only check
// would treat the old-typed path as "not stale" (something IS there) and let
// `write` mode crash mid-copy instead of clearing the conflicting path first.
function findStaleFiles(generatedRoot, targetRoot) {
  const stale = [];
  const walk = (absDir, relDir) => {
    for (const entry of readdirSync(absDir, { withFileTypes: true })) {
      // Pre-existing `__pycache__`/`*.pyc` copies from before the staging
      // filter above existed (or from a stray local run) never appear in
      // `generatedRoot` now, which would otherwise make every one of them
      // "stale" forever with no way to clear them (they're gitignored, not
      // committed, and this script has no delete-on-check mode). They're
      // harmless build byproducts, not generated content this script owns —
      // skip them entirely rather than flag or recurse into them.
      if (isPycacheEntry(entry.name)) continue;
      const relPath = join(relDir, entry.name);
      const absPath = join(absDir, entry.name);
      const genPath = join(generatedRoot, relPath);
      if (!existsSync(genPath)) {
        stale.push(relPath);
        continue;
      }
      if (statSync(genPath).isDirectory() !== entry.isDirectory()) {
        // Same relative path exists on both sides but changed shape — do not
        // recurse into it (its children aren't comparable across the type
        // change); report the whole path so the caller removes it wholesale
        // before copying the newly-shaped generated content over it.
        stale.push(relPath);
        continue;
      }
      if (entry.isDirectory()) walk(absPath, relPath);
    }
  };
  for (const dir of MIRRORED_DIRS) {
    const targetDir = join(targetRoot, dir);
    if (existsSync(targetDir)) walk(targetDir, dir);
  }
  return stale;
}

// The sibling of `findStaleFiles` for retired generated paths (outputs an
// abolished feature used to produce) lives in
// scripts/retired-generated-paths.mjs — see that module's header for why
// neither `diffTree` nor `findStaleFiles` can cover them.

// After deleting the stale files found by `findStaleFiles`, a fully-removed
// skill can leave behind empty directories (e.g. `.claude/skills/<old
// name>/scripts/` once its one file is gone). Recursively prunes any
// directory under `dir` (bottom-up) that ends up with zero entries. Safe to
// call unconditionally on all of MIRRORED_DIRS every write run — directories
// that still have generated content in them are never touched.
function pruneEmptyDirs(dir) {
  if (!existsSync(dir)) return;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) pruneEmptyDirs(join(dir, entry.name));
  }
  if (readdirSync(dir).length === 0) rmSync(dir, { recursive: true, force: true });
}
