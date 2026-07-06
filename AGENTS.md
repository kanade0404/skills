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
- These invariants are machine-checked by `tests/` (run via `python3 -m unittest discover -s tests`) and the trigger-evals CI.

## Build, Test, and Development Commands

There is no project-wide build. Useful commands:

- `python3 -m unittest discover -s tests`: frontmatter invariants + CI checker unit tests.
- `uv run python .github/scripts/check_trigger_evals.py`: full trigger-eval scan (known failures live in `.github/trigger-evals-known-failures.json`).
- `uv run python skills/skill-builder/scripts/score_triggers.py --cases <cases> --preds <results>`: score one skill's trigger predictions.
- `node scripts/rulesync-sync.mjs [--check]`: regenerate (or verify) generated agent configs.

## Testing Guidelines

For trigger evals, place cases in `skills/<skill>/evals/<skill>-trigger.json` and predictions in `skills/<skill>/evals/<skill>-trigger-results-YYYY-MM-DD.jsonl`. Include both should-trigger and should-skip prompts, with tags such as `explicit`, `ambiguous`, `adjacent`, and `distractor`. When a skill's frontmatter (trigger surface) changes, re-measure and add a new dated results file — CI enforces this.

## Commit & Pull Request Guidelines

Use concise, imperative commit subjects such as `Add postgres skill references` or `Tune test-review trigger evals`.

Pull requests should describe the skill changed, why the change is needed, and any eval results. Releases are git tags `vX.Y.Z` (semver; see RELEASING.md) — consumers pin via `kanade0404/skills@<tag>`.

## Agent-Specific Instructions

Treat this as a skill-content / distribution-source repository, not an application. Avoid unrelated refactors, and do not rename skill directories without updating internal references and the dir-name = frontmatter `name` invariant.

# Bash / API discipline

- ファイルの閲覧・検索・加工は専用ツール (Read / Glob / Grep / Edit) を優先し、Bash の
  汎用テキストコマンドで代用しない。専用ツールの方が行番号表示・出力制御の点で優れており、
  環境によっては permission / hook が汎用コマンドを拒否する。
- コマンドが permission / hook にブロックされたら、**同型のコマンドを再試行しない**:
  1. エラーメッセージに代替手段が提示されていればそれに従う
  2. 提示が無い・不明瞭なら、その環境の permissions 設定 (`/permissions`、
     `.claude/settings.json` 等) を確認してから続行する
  3. カレント repo 外への git 操作が拒否される環境では GitHub API
     (contents / git database) で代替する
  (どのコマンドが禁止かは環境設定に依存して変わるため、このルールは個別コマンドを
  列挙しない。設定が真実であり、ルールはその読み方だけを定める)
- `gh api` / `curl` の出力をデータとして扱う前に、必ず成功を確認する。`gh api` は
  HTTP エラーで非ゼロ exit するが、**素の `curl` は HTTP エラーでも exit 0** なので
  `--fail` 系オプションを併用して exit code を見るか、HTTP ステータスを自分で検査
  する。失敗時はエラー本文が stdout に混ざるため、そのままパースすると誤検知する
  (例: 404 のエラー JSON を「データが存在する」と誤認する)。また `gh` の出力は
  端末向けに色付けされることがあるため、機械処理には `--jq` / `--json` を使い、
  素の出力を jq にパイプしない。ポーリング・監視・収集スクリプトを書くときは特に。

# PR push discipline

PR ブランチへ push したら「push して報告」で終わらせない。次の 3 つの帰結が
担保されるまで、その PR を離れない。手順の中身は各 skill が持つ — この rule は
「いつ必ず起動するか」だけを定める:

1. **CI の帰結** — 起動した checks の完了まで追う (失敗時は root cause 特定へ。
   `ci-self-heal` があればそれに委ねる)
2. **レビュースレッドの終端** — 個別返信 + 集約サマリ。`pr-review-respond` を起動する
3. **離れる前の監視** — merge / close・新規コメント・checks 失敗を検知する手段を
   残す (イベントトリガ、無ければ `pr-monitor`)

なぜ rule か: skill はトリガされて初めてロードされるため、「忘れずに起動する」
保証だけは常駐する rule でしか担保できない (実例: push 後に P2 指摘 7 件が半日
放置された)。無人配線 (push / コメントを起点とするイベントトリガ) が同等の保証を
持つリポジトリでは、この rule は「PR を open のまま無監視で放置しない」という
1 行の不変条件に縮退してよい。
