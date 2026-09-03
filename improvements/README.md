# improvements/ — 改善台帳

`skill-improver` が回す改善ループ (finding → 最小差分 PR → merge / revert) の記録。
手順の canonical source は [`skills/skill-improver/SKILL.md`](../skills/skill-improver/SKILL.md)、
スキーマと CLI の詳細は
[`skills/skill-improver/references/ledger.md`](../skills/skill-improver/references/ledger.md)。

## ファイル

| path | 内容 |
|---|---|
| `ledger.jsonl` | 1 行 1 JSON オブジェクトの JSON Lines。**現在エントリ 0 件** (実運用の finding だけを入れる。例示は本 README に置く) |

台帳がここ (repo root) にあって skill ディレクトリの中に無いのは、対象が複数 skill に
跨るのと、consumer が `rulesync fetch --features skills` で持っていくのは skill
ディレクトリだけで、台帳は**この repo の運用データ**だからである。

## エントリの形 (worked example)

以下は形式を示すための例であり、`ledger.jsonl` には入っていない。実ファイルでは
1 エントリ = 1 行 (改行なし):

```json
{"id":"IMP-20260910-4ce564","created":"2026-09-10","source":"agent-feedback","evidence":["https://github.com/kanade0404/skills/pull/123#issuecomment-1","session_01ABC"],"target_skill":"ci-self-heal","finding":"3 連続失敗の停止条件が「同一エラー」限定と読まれ、別エラーで再試行が続いた","finding_class":"stop-condition","lever":"skill-edit","status":"merged","pr":"https://github.com/kanade0404/skills/pull/130","before":{"ci_fix_iterations":6},"after":{"ci_fix_iterations":3},"recurrence":2,"notes":""}
```

- `id`: `IMP-<作成日 YYYYMMDD>-<sha1(target_skill + 改行 + 再発クラスキー) の先頭 6 桁>`。
  台帳の既存行を読まずに決まるので、複数の finding を別ブランチで並行に処理しても
  採番が衝突しない (連番だと枝ごとに同じ番号を採る)。同じ日・同じ skill・同じクラスの
  2 度目の登録は同じ id になり、`add` が重複として拒否する
- `source`: `retro` / `session-retro` / `agent-feedback` / `trigger-eval`
- `finding_class`: 再発クラスキー (省略可)。空なら finding 本文の正規化で代用するが、
  それが吸収するのは表記揺れだけ。同じ問題の再発だと判断したら `add --class <key>` で
  明示して束ねる
- `lever`: `skill-edit` / `ept` / `trigger` (上流の `ept-handoff` は `ept` に正規化して保存)
- `status`: `proposed` / `pr_open` / `merged` / `rejected` / `excluded_meta` / `reverted`
- 指標キー (すべて省略可): `trigger_f1` (大きいほど良い) / `ci_fix_iterations` /
  `review_cycles` / `escalations` (小さいほど良い)。**観測できなかった値は書かない** —
  0 で埋めると after 比較で偽の改善が出る

## 手で触らない

編集は同梱スクリプト経由で行う (`id` の採番と `recurrence` の計算がスクリプト側にある):

```bash
uv run python3 skills/skill-improver/scripts/ledger.py list --status pr_open
uv run python3 skills/skill-improver/scripts/ledger.py report
uv run python3 skills/skill-improver/scripts/ledger.py check-target <skill>
```

`list` は status で絞ってエントリを並べる (改善ループの各回は、まだ `pr_open` のまま
残っているエントリの突き合わせから始まる)。`report` は skill 別の再発回数・再発クラス
キーと before→after の delta を出し、**merge 済み**のエントリのうち after が before より
悪化したものを **revert candidate** として並べる。悪化を見つけたら改善を重ねる前に
revert PR を提案する。

## メタスキルは対象外

`retro` / `session-retro` / `skill-builder` / `empirical-prompt-tuning` /
`skill-improver` / `model-policy` / `harness-distribution` / `rulesync-sync` を対象と
する finding は `status: excluded_meta` で記録するだけで、自動編集も PR 起票もしない
(理由は SKILL.md「メタスキル除外の理由」節)。人間が読んで手で直す。
