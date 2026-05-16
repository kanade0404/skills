# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

自作 Skill のカタログ兼 **rulesync 配布元リポジトリ**。配布は
[rulesync](https://github.com/dyoshikawa/rulesync) を用い、各プロジェクトで
`rulesync fetch kanade0404/skills@<tag> --features skills,...` → `rulesync generate`
で Claude Code / Codex 等の設定を生成して使う（旧 APM 方式から移行済み）。

このリポジトリ自体にビルド・テスト・lint パイプラインは無い。コードを書く場所ではなく
**SKILL.md を編集する場所**。

**サードパーティ製 skill は vendor しない**。consumer 側で upstream を直接
`rulesync fetch`（例: `planetscale/database-skills`）。

## レイアウト

rulesync の `fetch` はルート直下のトップレベル feature ディレクトリを読む。

```text
skills/<skill-name>/
├── SKILL.md            # 必須。frontmatter (name, description, allowed-tools) + 本文
├── references/*.md     # 詳細レイヤ（progressive disclosure 第3層）
├── evals/*.{json,jsonl}# trigger 評価ケース・結果
├── scripts/*.py        # スコアラー等の補助ツール
└── assets/*            # テンプレ等

subagents/  commands/  hooks/  rules/   # 配布 feature 枠（現状 placeholder）
```

収録 skill: `skill-builder`, `test-review`, `empirical-prompt-tuning`, `research-practices`, `product-discovery`, `pr-review-respond`, `verify-done`, `tidy-first`, `tdd`, `design`, `software-design`, `design-review`, `adr-writer`, `code-review`, `ci-self-heal`（いずれも自作・日本語 description・Anthropic skill best-practices 準拠）。

## SKILL.md 編集時の規約

`skills/skill-builder/SKILL.md` に詳細があり、ここで管理される全 skill の規範になる。要点:

- **frontmatter** — `name` ≤ 64 char / 小文字+数字+ハイフンのみ / 予約語（`anthropic`, `claude`）禁止。`description` ≤ 1024 char、三人称、「何を」「いつ使うか」両方を含める。
- **本文** — ≤ 500 行。越えるなら `references/<topic>.md` に切り出して本文は「いつ参照しに行くか」だけ書く。
- **`if`/`while` で動作分岐させない**（読み手が動作を予測できなくなる）。
- **negative space を成果物で定義** — 「やらないこと」を動詞ではなく成果物で書く（`skills/skill-builder/evals/failure-patterns.md` の `dual-meaning-verb-by-action` 参照）。

## skill-builder の評価ループ

`skills/skill-builder/scripts/score_triggers.py` で trigger eval をスコアリングする。

```bash
# skills/<skill>/evals/<skill>-trigger.json の cases と
# skills/<skill>/evals/<skill>-trigger-results-<date>.jsonl の予測を突き合わせ
uv run python skills/skill-builder/scripts/score_triggers.py \
  --cases skills/<skill>/evals/<skill>-trigger.json \
  --preds skills/<skill>/evals/<skill>-trigger-results-2026-04-29.jsonl
```

`run_harness.py` は `claude -p` を別プロセスで叩いて trigger を観測するハーネス（重量側）。

スコア解釈と次手の処方は `skills/skill-builder/SKILL.md` の Mode B (`tune-trigger`) を参照。

## 新しい skill を足すとき

1. `skills/<name>/SKILL.md` を作る。雛形と self-review チェックリストは `skills/skill-builder/SKILL.md` Mode A。
2. `README.md` の「収録 skill」表に 1 行追加する。
3. 配布対象なので、`skills/<name>/` のディレクトリ名 = `name` frontmatter にする。
4. リリースは git タグ。consumer は `kanade0404/skills@<tag>` で固定取得する。
