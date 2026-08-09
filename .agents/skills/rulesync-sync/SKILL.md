---
name: rulesync-sync
description: |
  この skill カタログ / rulesync 配布元リポジトリで、skill・rule・hook のソース
  (`skills/`, `rules/`, `rules-local/`, `hooks-local/`) を編集した後に、生成物
  (`.claude/`, `.agents/`, `.codex/`, root `AGENTS.md` / `CLAUDE.md`) を
  `node scripts/rulesync-sync.mjs` で再生成し `--check` で drift が無いことを
  検証する手順、および feature ディレクトリ (`skills/` `rules/` `rules-local/`
  `subagents/` `commands/` `hooks/` `hooks-local/`) の意味論と skill 標準レイアウト
  (`SKILL.md` / `references/` / `evals/` / `scripts/` / `assets/`) を説明するスキル。

  「rulesync-sync.mjs 実行して」「生成物を再生成して」「.claude/ を再生成して」
  「生成物と drift してる」「skill 直したから反映しといて」「CLAUDE.md / AGENTS.md
  (生成物側) が古い気がする / 更新したい」「この repo の配布構造 / feature
  ディレクトリはどうなってる?」「新しい rule / hook をどこに置けばいい?」のような
  要請、および skill / rule / hook のソースを編集した直後、いずれでも必ず起動する
  こと。

  範囲外: consumer 側リポジトリでの harness 導入・配布判断 (`harness-distribution`)、
  skill 本文の作成・trigger 調整 (`skill-builder`)、リリースタグ作成
  (RELEASING.md の手順)。
---
# rulesync-sync

> **原則**: ソース (`skills/` / `rules/` / `rules-local/` / `hooks-local/`) を編集し、
> 生成物 (`.claude/` / `.agents/` / `.codex/` / root `AGENTS.md` / `CLAUDE.md`) は
> **直接編集しない**。生成は必ず `node scripts/rulesync-sync.mjs` を経由する。

**例外**: `.claude/agents/` は rulesync の生成対象外で、repo-local な agent 定義
(例: built-in `Explore` の上書き) を手書き管理する場所。生成物直接編集禁止の
対象外であり、`node scripts/rulesync-sync.mjs` の再実行で巻き戻らない。

## いつ使うか / 使わない場面

**使う**: skill / rule / hook のソースを編集した後の再生成・sync、「生成物と drift
してる」「rulesync-sync.mjs 実行して」「.claude/ を再生成して」「CLAUDE.md /
AGENTS.md (生成物側) を更新したい」「配布構造 / feature ディレクトリはどうなってる?」
「新しい rule / hook をどこに置く?」のような要請。

**使わない** (成果物で判定):

| 要求される成果物 | 渡す先 |
|---|---|
| consumer リポジトリでの配布判定・導入 | `harness-distribution` |
| skill 本文の新規作成・trigger 調整 | `skill-builder` |
| リリースタグの作成 | RELEASING.md の手順を直読 |

## このリポジトリの位置づけ

このリポジトリは skill カタログであり、[rulesync](https://github.com/dyoshikawa/rulesync)
の**配布元**でもある。consumer は次の 2 手順で取り込む:

```bash
rulesync fetch kanade0404/skills@<tag> --features skills,rules,...
rulesync generate
```

`rulesync fetch` はリポジトリルート直下の top-level feature ディレクトリを読む
(`.rulesync/` 配下ではない)。リリースは semver git タグ (`vX.Y.Z`)。手順は
RELEASING.md を参照し、本スキルではタグ運用そのものは扱わない。

## Feature ディレクトリの意味論

| ディレクトリ | 意味 | 配布対象か |
|---|---|---|
| `skills/<name>/` | Agent Skills。ディレクトリと `SKILL.md` frontmatter が inventory の source of truth | 配布 (`skills` feature) |
| `rules/` | 横断的な rule。**配布用** — consumer は中の全ファイルを fetch するため、frontmatter 付き rule ファイルのみを置く。README 禁止、repo-local な内容も禁止 (`tests/` で機械チェック) | 配布 (`rules` feature) |
| `rules-local/` | repo-local な rule (root rule `orchestration-policy.md` を含む)。`scripts/rulesync-sync.mjs` がこのリポジトリ自身の生成物に staging する | 配布対象外 |
| `subagents/` | 配布用の feature 枠。rulesync canonical 形式の subagent 定義を 1 ファイル 1 エージェントで置く（例: `problem-solver.md`）。**repo-local な内容を置かない** — consumer の `rulesync fetch --features subagents` は配下を丸ごと fetch する | 配布 (`subagents` feature) |
| `commands/` / `hooks/` | 配布用の feature 枠。現状 placeholder (README のみ)。**repo-local な内容を置かない** — consumer の `rulesync fetch --features hooks` 等は配下を丸ごと fetch する | 配布 (該当 feature) |
| `hooks-local/` | repo-local な hook。`claude-code-hooks.json` を `scripts/rulesync-sync.mjs` が読み、このリポジトリの生成 `.claude/settings.json` に merge する | 配布対象外 |

新しい rule / hook をどこに置くか迷ったら: 「他の consumer リポジトリにも配りたいか」
で分岐する。配りたい → `rules/` or `hooks/` (配布用 canonical 形式、判定自体は
`harness-distribution` が担う)。このリポジトリ自身の運用にしか関係ない → `rules-local/`
or `hooks-local/`。

third-party skill は原則ここに vendor しない — consumer は upstream リポジトリを
直接 `rulesync fetch` する。例外的に copy-in する場合は、その skill ディレクトリ内に
source と license 情報を記録する。

## Skill ディレクトリの標準レイアウト

```
skills/<name>/
  SKILL.md         # 必須。frontmatter + 本体指示
  references/*.md  # 詳細情報 (progressive disclosure)
  evals/*.{json,jsonl}  # trigger eval のケースと結果
  scripts/*        # 補助スクリプト
  assets/*         # テンプレート等の再利用素材
```

skill 本文の作成・改訂・trigger 調整自体は `skill-builder` が source of truth を
持つ。本スキルは「ソースを直したら生成物にどう反映するか」に閉じる。

## 生成 → 検証ワークフロー

1. **ソースを編集する**: `skills/`, `rules/`, `rules-local/`, `hooks-local/` のいずれか。
   生成物 (`.claude/`, `.agents/`, `.codex/`, root `AGENTS.md` / `CLAUDE.md`) は
   触らない — 触っても次の再生成で上書きされ、drift CI にも捕まる。
2. **再生成する**:
   ```bash
   node scripts/rulesync-sync.mjs
   ```
3. **drift が無いことを確認する**:
   ```bash
   node scripts/rulesync-sync.mjs --check
   ```
   `--check` は再生成結果と現状の生成物に差分が無いことを確認するモード。差分が
   あれば non-zero exit する — その場合は手順 2 に戻って再生成し、生成物側を
   手で直さない。
4. 生成物の差分を `git status` / `git diff` で確認してから次工程 (commit 等) に進む。

## 検証コマンド

- `uv run python3 -m unittest discover -s tests`: frontmatter 不変条件
  (`name` = ディレクトリ名、`description` ≤1024 文字 等) と CI checker の
  unit test。
- `uv run python .github/scripts/check_trigger_evals.py`: 全 skill の
  trigger-eval を一括スキャンする (既知の失敗は
  `.github/trigger-evals-known-failures.json` に記録されているものは許容)。

skill の frontmatter (trigger surface: `description` の文言・条件) を変更したら、
再測定して `skills/<skill>/evals/<skill>-trigger-results-YYYY-MM-DD.jsonl` を
新しい日付で追加する (CI が強制する — 未更新は drift として検出される)。trigger
surface に新しい文言・条件を追加/変更した場合は、その新 surface を直接狙う eval
case を `skills/<skill>/evals/<skill>-trigger.json` に 2-3 件足してから再測定する。
旧 case の再採点だけでは新 surface の誤発火/取りこぼしを検出できない (PR #74 で観測)。

bare `python3` / `python` (`uv run` を付けない形) は、このリポジトリに対して
作業する一部の開発環境で壊れた alias に解決することが複数の subagent で
独立に再発見されている。**必ず `uv run` 経由で呼ぶ**。

## 不変条件 (drift CI と `tests/` が機械チェック)

- skill ディレクトリ名は `SKILL.md` frontmatter の `name` と一致する (小文字英数字
  とハイフンのみ、64 文字以内)。
- `description` は 1024 文字以内 (超過すると他エージェントの skill loader が読み込みに
  失敗する)。
- `SKILL.md` は 500 行未満に収め、詳細は `references/<topic>.md` に逃がす。
- 生成物 (`.claude/` 等) は `node scripts/rulesync-sync.mjs` の出力と一致していなければ
  ならない (drift CI)。

## このスキルがやらないこと

- **consumer リポジトリでの導入判断**: 配布するか・どの feature 枠に置くかの判定は
  `harness-distribution` の成果物。
- **skill 本文の作成・trigger チューニング**: `skill-builder` の成果物。
- **リリースタグの作成・consumer への伝播**: RELEASING.md の手順を直接参照する。
- **生成物の手編集**: 次回の再生成 / drift CI で必ず巻き戻る。
