# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

自作 Skill のカタログ。配布は [microsoft/apm](https://github.com/microsoft/apm) を想定し、各プロジェクトの `apm.yml` から `kanade0404/skills/<name>` で参照される。

このリポジトリ自体にビルド・テスト・lint パイプラインは無い。コードを書く場所ではなく **SKILL.md を編集する場所**。

**サードパーティ製 skill は vendor しない**。consumer 側 `apm.yml` で upstream を直接参照する（例: `planetscale/database-skills/skills/postgres`）。

## レイアウト

```
<skill-name>/
├── SKILL.md            # 必須。frontmatter (name, description, allowed-tools) + 本文
├── references/*.md     # 詳細レイヤ（progressive disclosure 第3層）
├── evals/*.{json,jsonl}# trigger 評価ケース・結果
├── scripts/*.py        # スコアラー等の補助ツール
└── assets/*            # テンプレ等
```

収録 skill: `skill-builder`, `test-review`, `empirical-prompt-tuning`, `research-practices`, `product-discovery`, `pr-review-respond`, `verify-done`, `tidy-first`, `tdd`, `design`, `design-review`, `adr-writer`, `code-review`, `ci-self-heal`（いずれも自作・日本語 description・Anthropic skill best-practices 準拠）。

## SKILL.md 編集時の規約

`skill-builder/SKILL.md` に詳細があり、ここで管理される全 skill の規範になる。要点:

- **frontmatter** — `name` ≤ 64 char / 小文字+数字+ハイフンのみ / 予約語（`anthropic`, `claude`）禁止。`description` ≤ 1024 char、三人称、「何を」「いつ使うか」両方を含める。
- **本文** — ≤ 500 行。越えるなら `references/<topic>.md` に切り出して本文は「いつ参照しに行くか」だけ書く。
- **`if`/`while` で動作分岐させない**（読み手が動作を予測できなくなる）。
- **negative space を成果物で定義** — 「やらないこと」を動詞ではなく成果物で書く（`evals/failure-patterns.md` の `dual-meaning-verb-by-action` 参照）。

## skill-builder の評価ループ

`skill-builder/scripts/score_triggers.py` で trigger eval をスコアリングする。

```bash
# evals/<skill>-trigger.json の cases と evals/<skill>-trigger-results-<date>.jsonl の予測を突き合わせ
uv run python skill-builder/scripts/score_triggers.py \
  --cases <skill>/evals/<skill>-trigger.json \
  --preds <skill>/evals/<skill>-trigger-results-2026-04-29.jsonl
```

`run_harness.py` は `claude -p` を別プロセスで叩いて trigger を観測するハーネス（重量側）。

スコア解釈と次手の処方は `skill-builder/SKILL.md` の Mode B (`tune-trigger`) を参照。

## 新しい skill を足すとき

1. `<name>/SKILL.md` を作る。雛形と self-review チェックリストは `skill-builder/SKILL.md` Mode A。
2. `README.md` の「概要」表に 1 行追加する。
3. APM 配布対象なので、`<name>/` のディレクトリ名 = `name` frontmatter にする。
