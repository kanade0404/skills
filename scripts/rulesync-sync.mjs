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
import { rmSync, mkdirSync, cpSync, copyFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const RULESYNC_VERSION = '9.1.1';
const check = process.argv.includes('--check');

// Stage the source-of-truth feature content into `.rulesync/` for `generate`.
// Only features with real content are staged; commands/hooks/rules/subagents are
// placeholder-only (README without frontmatter) and would fail rulesync parsing.
const stage = join(ROOT, '.rulesync');
rmSync(stage, { recursive: true, force: true });
mkdirSync(stage, { recursive: true });
cpSync(join(ROOT, 'skills'), join(stage, 'skills'), { recursive: true });
copyFileSync(join(ROOT, 'permissions.json'), join(stage, 'permissions.json'));

const args = [
  '-y', `rulesync@${RULESYNC_VERSION}`, 'generate',
  '--targets', 'claudecode,codexcli',
  '--features', 'skills,permissions',
  '--simulate-skills',
];
if (check) args.push('--check');

execFileSync('npx', args, { cwd: ROOT, stdio: 'inherit' });
