# Repository Guidelines

## Project Structure & Module Organization

This repository is a catalog and **rulesync distribution source** for Claude Code / Codex (and other rulesync-supported tools). Distribution is done with [rulesync](https://github.com/dyoshikawa/rulesync): consumers run `rulesync fetch kanade0404/skills@<tag> --features skills,...` then `rulesync generate`. (Migrated from the previous APM `apm.yml` method.)

`rulesync fetch` reads top-level feature directories at the repository root (not `.rulesync/`):

- `skills/<name>/`: Agent Skills (15 self-authored skills + 1 explicit copy-in live here).
- `subagents/`, `commands/`, `hooks/`, `rules/`: distribution feature slots, currently placeholders (README only); content migration is a later phase.

Each skill directory uses this layout:

- `SKILL.md`: required frontmatter plus the primary instructions.
- `references/*.md`: detailed supporting material for progressive disclosure.
- `evals/*.{json,jsonl}`: trigger evaluation cases and result files.
- `scripts/*.py`: helper tools, currently used by `skill-builder`.
- `assets/*`: templates and other reusable artifacts.

Third-party skills are generally not vendored here; consumers `rulesync fetch` upstream repositories directly. Explicit copy-in exceptions must record source and license information in `README.md` and include required upstream license notices. Keep `README.md` updated when adding or removing published skills.

## Build, Test, and Development Commands

There is no project-wide build, test, or lint pipeline. Most changes are Markdown edits.

- `rg --files`: list repository files quickly.
- `uv run python skills/skill-builder/scripts/score_triggers.py --cases skills/<skill>/evals/<skill>-trigger.json --preds skills/<skill>/evals/<skill>-trigger-results-YYYY-MM-DD.jsonl`: score trigger predictions.
- `python skills/skill-builder/scripts/run_harness.py --cases <path> --target-skill <name> --out <path>`: run the heavier Claude CLI trigger harness when local credentials and budget are available.

## Coding Style & Naming Conventions

Skill directory names must match the `name` field in `SKILL.md` frontmatter. Use lowercase letters, numbers, and hyphens only. Frontmatter descriptions should be specific, third-person, and describe both what the skill does and when it should be used.

Keep each `SKILL.md` concise, ideally under 500 lines. Move detailed topic material into `references/<topic>.md` and link to it only when the agent should consult it. Python scripts use standard library modules where possible, type hints, `pathlib.Path`, and UTF-8 file IO.

## Testing Guidelines

For trigger evals, place cases in `skills/<skill>/evals/<skill>-trigger.json` and predictions in `skills/<skill>/evals/<skill>-trigger-results-YYYY-MM-DD.jsonl`. Include both should-trigger and should-skip prompts, with tags such as `explicit`, `ambiguous`, `adjacent`, and `distractor`. Run `score_triggers.py` after changing trigger descriptions or eval data.

## Commit & Pull Request Guidelines

Use concise, imperative commit subjects such as `Add postgres skill references` or `Tune test-review trigger evals`.

Pull requests should describe the skill changed, why the change is needed, and any eval results. Include updated `README.md` entries for new skills. Cut a git tag (e.g. `v1.1.0`) for releases; consumers pin via `kanade0404/skills@<tag>`.

## Agent-Specific Instructions

Treat this as a skill-content / distribution-source repository, not an application. Avoid unrelated refactors, and do not rename skill directories without updating `README.md`, internal references, and the dir-name = frontmatter `name` invariant.
