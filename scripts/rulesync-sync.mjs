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
  restoreSourceExecutableBits(genOut);

  if (check) {
    const diffs = diffTree(genOut, ROOT);
    if (diffs.length > 0) {
      console.error(
        'rulesync-sync: generated outputs are stale (run `node scripts/rulesync-sync.mjs`):',
      );
      for (const d of diffs) console.error(`  ${d}`);
      process.exit(1);
    }
    console.log('rulesync-sync --check: up to date.');
  } else {
    cpSync(genOut, ROOT, { recursive: true });
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
function mergeRepoLocalHooks(outRoot) {
  const hooksSource = join(ROOT, 'hooks-local', 'claude-code-hooks.json');
  const settingsPath = join(outRoot, '.claude', 'settings.json');
  if (!existsSync(hooksSource) || !existsSync(settingsPath)) return;
  const settings = JSON.parse(readFileSync(settingsPath, 'utf8'));
  const hooks = JSON.parse(readFileSync(hooksSource, 'utf8'));
  settings.hooks = hooks;
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
// relative path under `targetRoot`. Only reports files that are new or changed
// (matches the non-deleting semantics of a plain `cpSync` materialize — this
// script never passes rulesync's `--delete`, so stale extra files in the repo
// are out of scope here too). Executable-bit drift is only compared for files
// with a source script counterpart (`restoreSourceExecutableBits` above) —
// comparing exec bits repo-wide would make --check depend on the runner's
// umask for the many non-script generated files.
function diffTree(generatedRoot, targetRoot, rel = '') {
  const diffs = [];
  for (const entry of readdirSync(join(generatedRoot, rel), { withFileTypes: true })) {
    const relPath = rel ? join(rel, entry.name) : entry.name;
    if (entry.isDirectory()) {
      diffs.push(...diffTree(generatedRoot, targetRoot, relPath));
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
