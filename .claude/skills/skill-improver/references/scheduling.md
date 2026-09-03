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

この job は書き込み資格情報を持ったまま、第三者が書ける可能性のあるテキスト
(`agent-feedback` ラベルの issue / PR コメント) を読む。そのため:

- **読む対象を絞る**: ラベルが付いた item の、author association が
  `OWNER` / `MEMBER` / `COLLABORATOR` のコメントだけ (`references/feedback-intake.md`)
- **道具を絞る**: workflow の `claude_args` で `--allowed-tools` を allow-list にし、
  `--disallowed-tools` で default branch への直 push と merge を落とす。これは
  injection が通ったときの被害範囲を狭める浅いガードで、保証は上の ruleset 側
- **action は full commit SHA で固定する**: 可変タグのままだと、差し替えられた
  action が `CLAUDE_CODE_OAUTH_TOKEN` と `contents: write` ごと持っていける

### improve/* PR には repo の CI が来ない — だから検証してから起票する

**`GITHUB_TOKEN` 起点のイベントは `pull_request` ワークフローの run を作らない**
(`workflow_dispatch` / `repository_dispatch` を除く、GitHub の再帰実行防止仕様)。
そのため trigger-evals / rulesync drift / unittest の各 workflow は improve/* PR では
回らず、レビュアーには「チェックが 1 つも無い PR」が見える。

そこで **workflow モードでは PR を作るのは agent ではない**。agent は改善ブランチを
push し、環境変数 `MANIFEST` のファイルに 1 行 1 JSON
(`{"branch":..., "title":..., "body_file":...}`) を追記するところまで。`gh pr create` は
allow-list から外してある。後続ステップがブランチごとに checkout して
`python3 -m unittest discover tests` /
`python3 .github/scripts/check_trigger_evals.py` / `node scripts/rulesync-sync.mjs --check`
を実行し、**通ったブランチにだけ** `gh pr create` する。落ちたブランチは PR にならず、
失敗したコマンドが job summary に出て job が赤になる (ブランチは調査用に残る)。
CI runner は `trigger-evals.yml` と同じく `python3` を直接呼ぶ (`uv` が無い runner
前提) — ローカル / agent 実行 (Step 5 やこの下の Route 2) では `uv run python3` を使う。

`rulesync-sync.mjs` は引数なしだと生成物を**書き込む**。検証に使うのは `--check` の方で、
生成は Step 5 の前段として別に実行する。

(任意) PR 作成の資格情報を `GITHUB_TOKEN` ではなく GitHub App のトークンか PAT に
差し替えれば、通常どおり `pull_request` イベントが発火して repo の CI がそのまま
効くようになる。その場合も「検証してから起票する」順序は変えない。

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
2. まず Step 0 (reconcile): ledger.py list --status pr_open で未決着のエントリを
   列挙し、各 PR の現在の状態 (merged / closed / open) を確認して台帳を更新する。
   merged なら set-status --status merged と、取れる after メトリクスの
   record-metrics --phase after。closed (未 merge) なら rejected。そのうえで
   ledger.py report を読み、revert candidate があれば新規候補より先に報告する
3. 入力は 3 系統: (a) 直近 1 週の retro / session-retro finding のうち lever が
   skill edit / ept-handoff のもの (台帳には ept として保存される)
   (b) agent-feedback ラベルの issue / PR で、author association が
   OWNER / MEMBER / COLLABORATOR のコメントだけ。取得は
   `gh issue list --label agent-feedback --state all --limit 200` /
   `gh pr list --label agent-feedback --state all --limit 200`
   (既定の open / 30 件では closed の feedback と 31 件目以降が落ちる)
   (c) skills/*/evals/*-trigger-results-*.jsonl のうち F1 < 0.8 の skill
4. 候補ごとに ledger.py check-target を通す。exit 2 (メタスキル) なら編集せず
   improvements/ledger.jsonl に excluded_meta で記録して人間に上げる
5. 採用する候補は 1 finding = 1 PR = 1 テーマ。改善ブランチは毎回 default branch を
   最新化してから improve/<skill>-<finding-id> を切る (前の改善ブランチから枝分かれ
   させない)。default branch には push しない。merge もしない
6. skill のソースを触ったら node scripts/rulesync-sync.mjs で生成物を再生成し、
   そのうえで PR を開く前に score_triggers.py / .github/scripts/check_trigger_evals.py /
   uv run python3 -m unittest discover -s tests / node scripts/rulesync-sync.mjs --check
   を通す (検証に使うのは --check。引数なしは生成物を書き込むので検証にならない)。
   Routine は workflow の外なので、検証も PR 起票も自分で行う
7. 起票した PR・除外・見送り・revert candidate を SKILL.md の実行レポート形式で報告する
8. 候補が 0 件なら PR を作らず「今週は改善なし」と報告して終了する
```

## 手動起動 (対話セッション)

週次を待たずに回したいときは、そのまま依頼すればよい:

「skill 改善 PR 出して」「retro の finding を skill に反映して」
「agent-feedback を取り込んで」「改善ループ回して」

特定の finding だけ回すときは finding id (`IMP-20260910-4ce564`) か対象 skill 名を添える。

## 実行間隔を変えるときの判断材料

- **PR がレビューされずに溜まっている** → 間隔を延ばす (隔週)。未レビューの改善 PR が
  積み上がると、after メトリクスが取れず revert 判断もできない
- **同じ finding の `recurrence` が 3 以上に伸びる** → 間隔ではなく差分の質の問題。
  `ledger.py report` で再発クラスを確認し、原則への一般化ができているかを見直す。
  再発かどうかは `report --skill <skill>` の出力を読んで **agent が判断する** —
  台帳側の正規化が吸収するのは表記揺れだけで、言い換えは別クラスに落ちる。同じ問題だと
  判断したら `add --class <key>` で同じクラスキーを付けて束ねる
