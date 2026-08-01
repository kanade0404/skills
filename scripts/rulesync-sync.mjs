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

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const RULESYNC_VERSION = '9.1.1';
const check = process.argv.includes('--check');

// Generated trees that mirror source content 1:1, one subtree/file per source
// skill or rule. When a source item is deleted or renamed, `rulesync generate`
// simply omits it from the scratch output root — there is no "shrunk" file to
// diff against, the whole path is just absent. That's different from the
// single aggregated files this script also generates (`.claude/settings.json`,
// `.codex/config.toml`, `.codex/rules/rulesync.rules`, root `CLAUDE.md`/
// `AGENTS.md`): those still exist in the scratch output with different
// (smaller) content after a deletion, so `diffTree`'s plain content comparison
// already catches drift there. Restricting the stale-file walk (see
// `findStaleFiles` below) to exactly these three mirrored trees (rather than
// all of `.claude`/`.agents`) also keeps it from ever touching non-generated
// content that happens to live alongside them — e.g. `.claude/settings.json`
// itself, or the gitignored runtime state under `.claude/.pr-monitor/`.
const MIRRORED_DIRS = ['.claude/skills', '.claude/rules', '.agents/skills'];

// Stage the source-of-truth feature content into `.rulesync/` for `generate`.
// Only features with real content are staged; commands/hooks/subagents are
// placeholder-only (README without frontmatter) and would fail rulesync parsing.
// rules/ は配布用 (consumer が fetch で丸ごと受け取る)、rules-local/ はこの repo
// 専用 (root rule 等。配布 feature には含まれない) — 自前生成では両方を staging する。
const stage = join(ROOT, '.rulesync');
rmSync(stage, { recursive: true, force: true });
mkdirSync(stage, { recursive: true });
cpSync(join(ROOT, 'skills'), join(stage, 'skills'), { recursive: true });
copyFileSync(join(ROOT, 'permissions.json'), join(stage, 'permissions.json'));
mkdirSync(join(stage, 'rules'), { recursive: true });
// rulesync は nested rule (rules/**/*.md) を扱えるため再帰的に staging する
// (top-level しか見ないと consumer の fetch (再帰) と自前生成が乖離する)
const isRuleFile = (src) =>
  statSync(src).isDirectory() || (src.endsWith('.md') && basename(src) !== 'README.md');
for (const dir of ['rules', 'rules-local']) {
  cpSync(join(ROOT, dir), join(stage, 'rules'), {
    recursive: true,
    force: true,
    filter: isRuleFile,
  });
}

// Always generate into a scratch output root (never straight onto the repo, and
// never with rulesync's own `--check`) so both modes share one generation call.
const genOut = mkdtempSync(join(tmpdir(), 'rulesync-sync-'));
try {
  const args = [
    '-y', `rulesync@${RULESYNC_VERSION}`, 'generate',
    '--targets', 'claudecode,codexcli',
    '--features', 'skills,permissions,rules',
    '--simulate-skills',
    '-o', genOut,
  ];
  execFileSync('npx', args, { cwd: ROOT, stdio: 'inherit' });

  mergeRepoLocalHooks(genOut);
  mergeRepoLocalEnv(genOut);
  restoreSourceExecutableBits(genOut);

  if (check) {
    // Compute stale/type-mismatch paths BEFORE diffing (not after): a
    // mirrored path that changed shape (directory <-> file) between the
    // previous and current generation makes `diffTree` try to read the
    // wrong type at that same relative path (EISDIR/ENOTDIR) before --check
    // even gets a chance to report it as stale (PR #78 review). `diffTree`
    // is told which paths are already-known type mismatches (via `stale`,
    // which includes them alongside plain missing-in-genOut paths) so it can
    // skip them instead of crashing.
    const stale = findStaleFiles(genOut, ROOT);
    const diffs = diffTree(genOut, ROOT, new Set(stale));
    if (diffs.length > 0 || stale.length > 0) {
      console.error(
        'rulesync-sync: generated outputs are stale (run `node scripts/rulesync-sync.mjs`):',
      );
      for (const d of diffs) console.error(`  ${d}`);
      for (const s of stale) {
        console.error(`  ${s} (stale — no longer generated in this shape; source skill/rule likely deleted, renamed, or changed between file and directory)`);
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
    const stale = findStaleFiles(genOut, ROOT);
    for (const s of stale) rmSync(join(ROOT, s), { recursive: true, force: true });
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
// exit 1 loudly instead (rules/fail-closed.md).
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

function mergeRepoLocalHooks(outRoot) {
  const hooksSource = join(ROOT, 'hooks-local', 'claude-code-hooks.json');
  const settingsPath = join(outRoot, '.claude', 'settings.json');
  if (!existsSync(hooksSource)) return; // optional fragment — nothing to merge
  requireGeneratedSettings(settingsPath, hooksSource, 'hooks');
  const settings = JSON.parse(readFileSync(settingsPath, 'utf8'));
  settings.hooks = readFragmentObject(hooksSource, 'hooks');
  writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + '\n');
}

// A fragment source that EXISTS while the generated settings.json it must be
// merged into does NOT is a broken generation (e.g. the permissions feature
// stopped emitting `.claude/settings.json`): returning silently would ship a
// settings.json without the fragment this repo declares, with no symptom until
// the missing hooks/env bite downstream. Exit 1 loudly (rules/fail-closed.md).
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
// silently breaks downstream `jq` (observed 3+ times — see
// rules/bash-and-api-discipline.md). Key order stays deterministic: `env` is
// always appended after `permissions` and `hooks`.
function mergeRepoLocalEnv(outRoot) {
  const envSource = join(ROOT, 'hooks-local', 'claude-code-env.json');
  const settingsPath = join(outRoot, '.claude', 'settings.json');
  if (!existsSync(envSource)) return; // optional fragment — nothing to merge
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
// still exist there; a path whose *source* skill/rule was deleted or
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
// `generatedRoot` in the first place — a source skill/rule deleted or renamed
// leaves its old generated mirror behind, `--check` stays green, and (since
// materialize is a non-deleting `cpSync` overlay) `write` mode never removes
// it either, so agents keep seeing a skill/rule that no longer has a source
// of truth. The type-mismatch case additionally matters because `cpSync`
// cannot overlay a directory onto an existing file, or a file onto an
// existing directory, even with `force: true` — an existence-only check
// would treat the old-typed path as "not stale" (something IS there) and let
// `write` mode crash mid-copy instead of clearing the conflicting path first.
function findStaleFiles(generatedRoot, targetRoot) {
  const stale = [];
  const walk = (absDir, relDir) => {
    for (const entry of readdirSync(absDir, { withFileTypes: true })) {
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

// After deleting the stale files found by `findStaleFiles`, a fully-removed
// skill/rule can leave behind empty directories (e.g. `.claude/skills/<old
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
