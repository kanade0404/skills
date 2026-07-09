# Repository Guidelines

## Project Structure & Module Organization

This repository is a catalog and **rulesync distribution source** for Claude Code / Codex (and other rulesync-supported tools). Distribution is done with [rulesync](https://github.com/dyoshikawa/rulesync): consumers run `rulesync fetch kanade0404/skills@<tag> --features skills,rules,...` then `rulesync generate`.

`rulesync fetch` reads top-level feature directories at the repository root (not `.rulesync/`):

- `skills/<name>/`: Agent Skills. The directories and `SKILL.md` frontmatter are the inventory source of truth.
- `rules/`: **distributable** cross-cutting rules. Consumers fetch every file in this directory, so it must contain only frontmattered rule files (no README, no repo-local content) — machine-checked by `tests/`.
- `rules-local/`: repo-local rules (this file included). Staged into this repo's own generated configs by `scripts/rulesync-sync.mjs` but **not** part of the fetched `rules` feature.
- `subagents/`, `commands/`, `hooks/`: distribution feature slots, currently placeholders (README only).

Each skill directory uses this layout:

- `SKILL.md`: required frontmatter plus the primary instructions.
- `references/*.md`: detailed supporting material for progressive disclosure.
- `evals/*.{json,jsonl}`: trigger evaluation cases and result files.
- `scripts/*`: helper tools.
- `assets/*`: templates and other reusable artifacts.

Generated outputs (`.claude/`, `.agents/`, `.codex/`, root `AGENTS.md` / `CLAUDE.md`) are materialized by `node scripts/rulesync-sync.mjs` and verified by the drift CI — edit the sources under `skills/` / `rules/` / `rules-local/`, never the generated files. Catalog-specific guidance belongs in `rules-local/`, distributable rules in `rules/`.

Third-party skills are generally not vendored here; consumers `rulesync fetch` upstream repositories directly. Explicit copy-in exceptions must record source and license information inside the skill directory. Do not maintain a duplicated skill inventory in `README.md` or rules.

## Invariants

- Skill directory names must match the `name` field in `SKILL.md` frontmatter (lowercase letters, numbers, hyphens only; ≤64 chars).
- Frontmatter `description` must be ≤1024 characters (other agents' skill loaders reject longer ones), specific, third-person, and describe both what the skill does and when to use it.
- Keep each `SKILL.md` under 500 lines; move detail into `references/<topic>.md`.
- The authoring / trigger-eval norms live in `skills/skill-builder/SKILL.md` (source of truth for skill creation).
- These invariants are machine-checked by `tests/` (run via `uv run python3 -m unittest discover -s tests`) and the trigger-evals CI.

## Build, Test, and Development Commands

There is no project-wide build. Useful commands:

- `uv run python3 -m unittest discover -s tests`: frontmatter invariants + CI checker unit tests.
- `uv run python .github/scripts/check_trigger_evals.py`: full trigger-eval scan (known failures live in `.github/trigger-evals-known-failures.json`).
- `uv run python skills/skill-builder/scripts/score_triggers.py --cases <cases> --preds <results>`: score one skill's trigger predictions.
- `node scripts/rulesync-sync.mjs [--check]`: regenerate (or verify) generated agent configs.

Bare `python3` / `python` (without `uv run`) resolve to a broken alias in some development environments used against this repository — this has been independently rediscovered by multiple subagents. Always invoke Python through `uv run`, as all commands above already do.

## Testing Guidelines

For trigger evals, place cases in `skills/<skill>/evals/<skill>-trigger.json` and predictions in `skills/<skill>/evals/<skill>-trigger-results-YYYY-MM-DD.jsonl`. Include both should-trigger and should-skip prompts, with tags such as `explicit`, `ambiguous`, `adjacent`, and `distractor`. When a skill's frontmatter (trigger surface) changes, re-measure and add a new dated results file — CI enforces this. When that change adds or alters a trigger surface (new wording, new condition), also add at least 2-3 new eval cases that target the new surface directly before re-measuring — re-scoring only the old cases cannot detect false triggers/misses on the new surface (observed in PR #74).

## Commit & Pull Request Guidelines

Use concise, imperative commit subjects such as `Add postgres skill references` or `Tune test-review trigger evals`.

Pull requests should describe the skill changed, why the change is needed, and any eval results. Releases are git tags `vX.Y.Z` (semver; see RELEASING.md) — consumers pin via `kanade0404/skills@<tag>`.

## Agent-Specific Instructions

Treat this as a skill-content / distribution-source repository, not an application. Avoid unrelated refactors, and do not rename skill directories without updating internal references and the dir-name = frontmatter `name` invariant.
