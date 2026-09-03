# 週次実行 (スケジューリング)

改善ループは**常時起動にしない**。finding が出るたびに PR が飛ぶと 1 finding = 1 PR の
規律がレビュー負荷で先に壊れ、before / after の比較期間も取れなくなる。週 1 回まとめて
収集し、その週で最も根拠の強い候補だけを PR にする。

## 経路 1 — GitHub Actions (このリポジトリの既定)

`.github/workflows/skill-improver.yml` が毎週月曜 00:00 UTC (月曜 09:00 JST) に
`anthropics/claude-code-action@v1` でこのスキルを起動する
(`claude.yml` / `claude-code-review.yml` と同じ action・同じ secret
`CLAUDE_CODE_OAUTH_TOKEN` を使う)。`workflow_dispatch` から手動起動もでき、
`focus` 入力で skill 名 / finding id / issue URL に絞れる。

権限は `contents: write` (改善ブランチの push) / `pull-requests: write` (PR 起票) /
`issues: read` (`agent-feedback` ラベルの読み取り) まで。default branch への push と
merge は**手順として**行わない — 承認ゲートは PR レビューに置く。

ただし `contents: write` は improve/* と default branch を区別できない。**手順は
約束であって強制ではない**ので、実効的な保証は default branch 側の設定に置く:

> **推奨セットアップ**: default branch に branch ruleset を作り、`GITHUB_TOKEN` /
> GitHub Actions からの push を拒否する (Rulesets → Restrict updates、bypass list に
> Actions を入れない)。これが無い限り、default branch へ push しない保証は
> 「workflow のプロンプトと本スキルの手順がそう書いてある」ことだけになる。

### improve/* PR には repo の CI が来ない

`GITHUB_TOKEN` で作成された PR は `pull_request` イベントを発火させない (再帰実行を
防ぐ GitHub の仕様)。そのため trigger-evals / rulesync drift / unittest の各 workflow は
improve/* PR では回らず、レビュアーには「チェックが 1 つも無い PR」が見える。

対策として workflow には agent 実行の後段に検証ステップを置き、agent が残した
ブランチのまま `python3 -m unittest discover tests` /
`python3 .github/scripts/check_trigger_evals.py` / `node scripts/rulesync-sync.mjs --check`
を実行して、赤なら job を落とす。PR 本文の Checks 欄にはその結果を書く。CI runner は
`trigger-evals.yml` と同じく `python3` を直接呼ぶ (`uv` が無い runner 前提) — ローカル /
agent 実行 (Step 5 やこの下の Route 2) では `uv run python3` を使う。

(任意) PR 作成の資格情報を `GITHUB_TOKEN` ではなく GitHub App のトークンか PAT に
差し替えれば、通常どおり `pull_request` イベントが発火して repo の CI がそのまま
効くようになる。その場合も上記の検証ステップは重複するだけで害はない。

`concurrency: skill-improver` で直列化しているのは、同時実行が同じ
`improve/<skill>-<finding-id>` ブランチを取り合うのを防ぐため。

手動起動:

```bash
gh workflow run skill-improver.yml                       # 全系統
gh workflow run skill-improver.yml -f focus=ci-self-heal # 対象を絞る
```

## 経路 2 — Anthropic Routine

Actions を使わない (使えない) 環境では、Routine に週次で登録する。cron は UTC で
解釈されるため、月曜 09:00 JST は `0 0 * * 1`。登録するプロンプト:

```text
kanade0404/skills で skill-improver を 1 周回してください。

1. skills/skill-improver/SKILL.md を読み、その手順に従う
2. 入力は 3 系統: (a) 直近 1 週の retro / session-retro finding のうち lever が
   skill edit / ept-handoff のもの (b) agent-feedback ラベルの issue / PR コメント
   (c) skills/*/evals/*-trigger-results-*.jsonl のうち F1 < 0.8 の skill
3. 候補ごとに ledger.py check-target を通す。exit 2 (メタスキル) なら編集せず
   improvements/ledger.jsonl に excluded_meta で記録して人間に上げる
4. 採用する候補は 1 finding = 1 PR = 1 テーマ。improve/<skill>-<finding-id> ブランチで
   作り、default branch には push しない。merge もしない
5. PR を開く前に score_triggers.py / .github/scripts/check_trigger_evals.py /
   uv run python3 -m unittest discover -s tests / node scripts/rulesync-sync.mjs を通す
6. 起票した PR・除外・見送り・revert candidate を SKILL.md の実行レポート形式で報告する
7. 候補が 0 件なら PR を作らず「今週は改善なし」と報告して終了する
```

## 手動起動 (対話セッション)

週次を待たずに回したいときは、そのまま依頼すればよい:

「skill 改善 PR 出して」「retro の finding を skill に反映して」
「agent-feedback を取り込んで」「改善ループ回して」

特定の finding だけ回すときは finding id (`IMP-0007`) か対象 skill 名を添える。

## 実行間隔を変えるときの判断材料

- **PR がレビューされずに溜まっている** → 間隔を延ばす (隔週)。未レビューの改善 PR が
  積み上がると、after メトリクスが取れず revert 判断もできない
- **同じ finding の `recurrence` が 3 以上に伸びる** → 間隔ではなく差分の質の問題。
  `ledger.py report` で再発クラスを確認し、原則への一般化ができているかを見直す。
  再発かどうかは `report --skill <skill>` の出力を読んで **agent が判断する** —
  台帳側の正規化が吸収するのは表記揺れだけで、言い換えは別クラスに落ちる。同じ問題だと
  判断したら `add --class <key>` で同じクラスキーを付けて束ねる
