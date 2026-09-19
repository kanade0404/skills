// Retired generated outputs: paths `rulesync generate` USED to emit for this
// repo and no longer emits at all. Extracted from `scripts/rulesync-sync.mjs`
// purely so it has a test seam (see `tests/test_retired_generated_paths.py`);
// importing rulesync-sync.mjs itself would run its whole staging +
// `npx rulesync generate` pipeline as an import side effect.
//
// Neither of that script's two other stale detectors covers these paths:
// `diffTree` only walks paths that exist under the freshly generated scratch
// root (a retired path is absent there by definition, so the walk never visits
// it), and `findStaleFiles` only walks `MIRRORED_DIRS`. Without this module a
// leftover copy sits in an existing checkout forever — `--check` stays silently
// green and write mode never removes it either, since materialize is a
// non-deleting `cpSync` overlay.
import { existsSync, rmSync } from 'node:fs';
import { join } from 'node:path';

// ADR 0018 abolished the rulesync `rules` feature. Root `CLAUDE.md` / `AGENTS.md`
// were aggregated from root rules and are the only retired outputs so far;
// `generate` with `--features skills,permissions` emits neither.
//
// `.codex/rules/rulesync.rules` must never be listed here: despite the name it is
// a `permissions` feature output that is still generated (ADR 0018 条件 4), so it
// exists under the generated root on every run — listing it would delete a live
// output. The guard is structural, not just documentary: a path is only reported
// when generation does NOT produce it (see below), so a still-generated path
// listed here by mistake is never returned.
export const RETIRED_GENERATED_PATHS = ['CLAUDE.md', 'AGENTS.md'];

// Reports every retired path that still exists in the repo while the freshly
// generated tree has no counterpart for it. `paths` is injectable so tests can
// pin the detection rule itself without depending on the current list.
export function findRetiredGeneratedPaths(
  generatedRoot,
  targetRoot,
  paths = RETIRED_GENERATED_PATHS,
) {
  const stale = [];
  for (const name of paths) {
    if (existsSync(join(targetRoot, name)) && !existsSync(join(generatedRoot, name))) {
      stale.push(name);
    }
  }
  return stale;
}

// Announce every removal: this deletes committed files, and a silent delete
// inside an otherwise additive "regenerate" run reads as an unexplained deletion
// in the next `git status` — say which path went and why while the reason is
// known. Removal is recursive because a retired path can be a whole directory,
// not only a single root file.
export function removeRetiredGeneratedPaths(targetRoot, stale, log = console.log) {
  for (const name of stale) {
    log(`rulesync-sync: removing ${name} (root rule 由来の生成物は ADR 0018 で廃止済み — 削除対象)`);
    rmSync(join(targetRoot, name), { recursive: true, force: true });
  }
}
