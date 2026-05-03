# Repository Guidelines

## Project Structure & Module Organization

This repository is a catalog of Claude/Codex skills intended for distribution through `apm.yml` as `kanade0404/skills/<name>`. Each skill lives in its own top-level directory, for example `skill-builder/`, `test-review/`, `empirical-prompt-tuning/`, and `research-practices/`. Third-party skills are not vendored here; consumers depend on upstream directly via `apm.yml`.

Use this layout for skill directories:

- `SKILL.md`: required frontmatter plus the primary instructions.
- `references/*.md`: detailed supporting material for progressive disclosure.
- `evals/*.{json,jsonl}`: trigger evaluation cases and result files.
- `scripts/*.py`: helper tools, currently used by `skill-builder`.
- `assets/*`: templates and other reusable artifacts.

Keep `README.md` updated when adding or removing published skills.

## Build, Test, and Development Commands

There is no project-wide build, test, or lint pipeline. Most changes are Markdown edits.

- `rg --files`: list repository files quickly.
- `uv run python skill-builder/scripts/score_triggers.py --cases <skill>/evals/<skill>-trigger.json --preds <skill>/evals/<skill>-trigger-results-YYYY-MM-DD.jsonl`: score trigger predictions.
- `python skill-builder/scripts/run_harness.py --cases <path> --target-skill <name> --out <path>`: run the heavier Claude CLI trigger harness when local credentials and budget are available.

## Coding Style & Naming Conventions

Skill directory names must match the `name` field in `SKILL.md` frontmatter. Use lowercase letters, numbers, and hyphens only. Frontmatter descriptions should be specific, third-person, and describe both what the skill does and when it should be used.

Keep each `SKILL.md` concise, ideally under 500 lines. Move detailed topic material into `references/<topic>.md` and link to it only when the agent should consult it. Python scripts use standard library modules where possible, type hints, `pathlib.Path`, and UTF-8 file IO.

## Testing Guidelines

For trigger evals, place cases in `evals/<skill>-trigger.json` and predictions in `evals/<skill>-trigger-results-YYYY-MM-DD.jsonl`. Include both should-trigger and should-skip prompts, with tags such as `explicit`, `ambiguous`, `adjacent`, and `distractor`. Run `score_triggers.py` after changing trigger descriptions or eval data.

## Commit & Pull Request Guidelines

This working tree has no existing commits, so no historical commit convention is available. Use concise, imperative commit subjects such as `Add postgres skill references` or `Tune test-review trigger evals`.

Pull requests should describe the skill changed, why the change is needed, and any eval results. Include updated `README.md` entries for new skills.

## Agent-Specific Instructions

Treat this as a skill-content repository, not an application. Avoid unrelated refactors, and do not rename skill directories without updating references and distribution paths.
