#!/usr/bin/env node
// Materialize this repo's own AI-tool configs from its top-level feature dirs
// (dogfooding via rulesync). Source of truth = top-level `skills/` + `permissions.json`
// (+ `hooks/claude-code-hooks.json`, a repo-local, non-distributed hooks source —
// see hooks/README.md). Generated (committed) outputs = `.claude/` (Claude Code)
// and `.agents/` + `.codex/` (Codex).
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
  readFileSync, writeFileSync, readdirSync, mkdtempSync,
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

// `hooks/claude-code-hooks.json` holds a raw Claude Code settings.json `hooks`
// fragment (repo-local; not a rulesync-distributed feature — see hooks/README.md).
// Inject it as the generated `.claude/settings.json`'s `hooks` key, deterministically
// (JSON.parse always yields the same key order from a static source file, and
// `settings.hooks = ...` always appends `hooks` after the freshly generated
// `permissions` key), so repeated runs produce byte-identical output.
function mergeRepoLocalHooks(outRoot) {
  const hooksSource = join(ROOT, 'hooks', 'claude-code-hooks.json');
  const settingsPath = join(outRoot, '.claude', 'settings.json');
  if (!existsSync(hooksSource) || !existsSync(settingsPath)) return;
  const settings = JSON.parse(readFileSync(settingsPath, 'utf8'));
  const hooks = JSON.parse(readFileSync(hooksSource, 'utf8'));
  settings.hooks = hooks;
  writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + '\n');
}

// Recursively compares every file under `generatedRoot` against the same
// relative path under `targetRoot`. Only reports files that are new or changed
// (matches the non-deleting semantics of a plain `cpSync` materialize — this
// script never passes rulesync's `--delete`, so stale extra files in the repo
// are out of scope here too).
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
    } else if (
      readFileSync(join(generatedRoot, relPath), 'utf8') !== readFileSync(targetPath, 'utf8')
    ) {
      diffs.push(relPath);
    }
  }
  return diffs;
}
