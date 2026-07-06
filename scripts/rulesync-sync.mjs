#!/usr/bin/env node
// Materialize this repo's own AI-tool configs from its top-level feature dirs
// (dogfooding via rulesync). Source of truth = top-level `skills/` + `permissions.json`.
// Generated (committed) outputs = `.claude/` (Claude Code) and `.agents/` + `.codex/` (Codex).
//
//   node scripts/rulesync-sync.mjs           # write generated outputs
//   node scripts/rulesync-sync.mjs --check   # fail (exit 1) if committed outputs are stale
//
// Uses node:fs for staging so it needs no shell `cp`/`rm`/`cd`. `.rulesync/` is the
// throwaway staging dir (gitignored) rulesync's `generate` reads from.
import { rmSync, mkdirSync, cpSync, copyFileSync, statSync } from 'node:fs';
import { basename } from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

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

const args = [
  '-y', `rulesync@${RULESYNC_VERSION}`, 'generate',
  '--targets', 'claudecode,codexcli',
  '--features', 'skills,permissions,rules',
  '--simulate-skills',
];
if (check) args.push('--check');

execFileSync('npx', args, { cwd: ROOT, stdio: 'inherit' });
