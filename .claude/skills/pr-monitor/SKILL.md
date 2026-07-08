---
name: pr-monitor
description: >-
  自分が作成・出荷した PR を merge / close まで長期間ポーリングで監視し、放置中に起きる CI
  失敗と新規レビューコメントへの対応までを回すスキル。毎ポーリングで同梱スクリプト `prm status <PR>` により state / head
  SHA / 失敗・pending checks / 未解決レビュースレッド全量を 1 回で観測し、`checks.failing`
  に未対応の新規失敗があれば `ci-self-heal` を、`known_comment_ids` に無い新規の未解決レビュースレッドがあれば
  `pr-review-respond` を、それぞれ subagent (Task) で dispatch する。新規分の author が全て
  CodeRabbit なら、`pr-review-respond` への契約入力で修正適用を `coderabbit:autofix` に委譲する
  (plugin がある環境のみ)。呼出側 (`shipping` Phase 6 / main セッション) は本スキル自体も subagent で
  dispatch し、main を長時間監視で塞がない。ポーリング間隔は状態変化なしで指数バックオフ (60 秒起点、上限 1800 秒)、新規
  push・新規失敗・新規コメント・決着のいずれかがあれば 60 秒にリセットする。決着 (MERGED / CLOSED) を検出したら `retro`
  を自動起動する。待機手段は `/schedule` (cron) → `ScheduleWakeup` → 手動 `--check-only`
  の優先順で環境依存を吸収する。`shipping` 完了直後・`gh pr create` 直後・「PR
  監視して」「マージされるまで見張って」「マージ/クローズしたら振り返りまで回して」「CI
  とコメントも見張って対応まで回して」のような要請で必ず起動する。CI 完了までの短時間監視や修復の実体は `ci-self-heal`、コメント対応の実体は
  `pr-review-respond` (CodeRabbit 起因は `coderabbit:autofix`) が持ち、本スキルは検知と
  dispatch のループ制御に閉じる。PR の merge 操作そのものは行わない — 決着の事実を待つだけ。
allowed-tools:
  - Read
  - Write
  - Bash(gh pr view *)
  - Bash(gh pr list *)
  - Bash(git rev-parse *)
  - Bash(git branch *)
  - Bash(bash *prm *)
  - Bash(gh pr comment *)
  - ScheduleWakeup
  - Skill
  - Task
---
# pr-monitor — PR ライフサイクル終端監視 + 放置防止ループ

> **責務境界**: 本スキルの責務は PR の **決着 (merge / close) までの長時間監視** と、毎ポーリングでの **CI failure / 未解決レビュースレッドの検知と subagent dispatch のループ制御**。修復・対応の実体は持たない — CI 修復は `ci-self-heal`、レビューコメント対応は `pr-review-respond` (CodeRabbit 起因の修正適用はさらに `coderabbit:autofix` に委譲) が担う。PR の merge / close 操作そのものも行わない。

## いつ起動するか

- `shipping` が merge-ready で停止した直後 / `gh pr create` 直後
- 「PR 監視して」「マージされるまで見張って」「決着したら retro まで」
- 「CI とコメントも見張って対応まで回して」(検知 + dispatch まで含めた監視要求)

逆に **起動しない** (実行の実体は別スキルへ):
- CI 失敗の root cause 特定・修正そのもの (`ci-self-heal`)
- レビューコメントへの応答・修正コミットそのもの (`pr-review-respond`。CodeRabbit 起因の修正適用は `coderabbit:autofix`)
- PR の merge / close 操作自体 (人間または別自動化の仕事)
- 既に merge / close 済みの PR の事後対応 (直接 `retro`)

## 起動形態

merge / close までの監視は分〜時間〜日のオーダーで、main のターンを占有すると他の作業が進まない。そのため呼出側 (`shipping` Phase 6 の監視設置フェーズ、または対話セッションの main) は本スキルを **Task で subagent dispatch** して呼び、main を即座に解放する。

dispatch された subagent 内部でも常駐しない。1 回の起動で行うのは次のどちらかだけ:

- **cron 登録** (Step 3 の手段 1 が使える場合): `/schedule` に `pr-monitor <n> --check-only` を登録して即座に終了する。以降のポーリングは cron が都度この skill を再起動する。
- **check-only 1 回判定**: cron / 前回の `ScheduleWakeup` から `--check-only` で再入した場合、Step 4 の 1 回分の判定だけ行い、次の待機を予約 (cron 経路なら何もせず) して終了する。

「pr-monitor という 1 プロセスが張り付いて監視し続ける」構造ではなく、**cron や ScheduleWakeup が短命な subagent を繰り返し起こす**構造にする。`ScheduleWakeup` 手段のときも 1 起床 = 1 subagent 終了に閉じ、session 内で foreground loop を回さない。

## 入力

| 引数 | 内容 |
|---|---|
| (省略) | 現在ブランチに紐づく PR を auto-detect |
| `<PR番号>` | 監視対象 PR を明示 |
| `--check-only` | cron / 再入時の 1 回判定モード (新規登録せず状態確認のみ。決着なら retro 起動) |

## 同梱スクリプト `scripts/prm`

`gh api` / `gh pr checks` を都度 inline で叩くと permission prompt が重なる上、未解決スレッド全量取得の cursor pagination や色付き `gh` 出力による `jq` 破壊 (`rules/bash-and-api-discipline.md` 参照) など落とし穴が多い。本スキルはこれらを `scripts/prm` に閉じ込め、**単一エントリポイントからのみ呼び出す** (`pr-review-respond` の `prr` と同じ設計)。呼び出しは常に:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/prm" <subcommand> <pr>
```

`allowed-tools` の `Bash(bash *prm *)` で auto-grant されるため、consumer 側の permission 追加は不要。

| subcommand | 役割 |
|---|---|
| `prm status <pr>` | state / head SHA / 失敗・pending checks / 未解決レビュースレッド全量を 1 つの安定 JSON で返す。毎ポーリングで呼ぶのはこれ 1 回だけ |
| `prm unresolved <pr>` | 未解決レビュースレッド全量のみ `{unresolved_count, threads}` |

`prm status` の出力スキーマ:

```json
{
  "pr": {"state": "OPEN", "merged_at": null, "url": "...", "branch": "...", "head_sha": "..."},
  "checks": {"failing": [{"name": "...", "bucket": "fail", "link": "..."}], "pending": ["..."]},
  "unresolved_count": 3,
  "unresolved_threads": [
    {"thread_id": "...", "is_outdated": false, "comment_id": 123, "author": "coderabbitai", "path": "...", "url": "...", "created_at": "...", "body_head": "..."}
  ]
}
```

決定性の担保 (`prr` と同じ規律):
- GraphQL `reviewThreads` は cursor pagination で最後まで取得する。1 ページ (最大 100) で打ち切ると未解決スレッドを見落とす
- `gh` の TTY 色付けはスクリプト冒頭で無効化する (`GH_FORCE_TTY=` `NO_COLOR=1` `CLICOLOR_FORCE=0`)。色付き出力はパイプ先の `jq` を静かに壊す
- 出力は `jq -S` でキー順を安定化し、Step 4 の `known_*` 差分検知が出力順のブレで誤動作しないようにする

## ワークフロー

### Step 1 — 対象 PR を特定

```bash
gh pr view <PR or 省略> --json number,state,url,headRefName -q '.'
```

PR 番号の auto-detect (引数省略時) は `gh pr view` のまま行う — `prm` は単一 PR 番号を要求する設計で、番号解決そのものは担わない。番号が判明したら以降 (Step 2〜4) は `prm` 経由に切り替える。

state が既に `MERGED` / `CLOSED` なら **Step 5 (retro 起動) へ直行** — 状態ファイルも待機手段も作らない (Step 2〜4 は監視が要るときだけ通る)。`OPEN` なら Step 2 へ継続。

### Step 2 — 状態を永続化

consumer 側の **gitignore 前提パス** `.claude/.pr-monitor/PR-<number>.yml` に記録する (リポを汚さない。配布先で `.claude/.pr-monitor/` を gitignore 推奨):

```yaml
pr_number: <n>
url: <url>
branch: <headRefName>
state: OPEN
created_at: <ISO8601>
last_checked_at: <ISO8601>
monitor_mode: <cron | wakeup | manual>   # Step 3 で採用した待機手段。再入時に何をすべきか判る
schedule_id: <cron/routine の id | null>  # cron 手段のとき。決着時の解除対象 (無いと何を消すか判らない)
origin_transcript: <当該 feature/ship を実際に行ったセッションの transcript パス>
known_comment_ids: [<comment_id>, ...]    # dispatch 済みコメント。多重 dispatch 防止
known_failing_checks: [<check name>, ...] # dispatch 済み失敗 check。同上
last_head_sha: <sha>                      # 前回観測 head。新 push 検知 (バックオフ reset と known_failing_checks クリアに使う)
poll_interval_seconds: <n>                # 指数バックオフの現在値
escalations: [{kind: ci-halted|review-stuck, key: <check名|comment_id>, at: <ISO8601>}]
                                           # needs-human コメントを投稿済みの事象。同じ (kind, key) への再投稿を防ぐ dedup 台帳
```

- `origin_transcript` は **初回登録時の現セッション transcript** を入れる (retro が解析すべきは「PR を生んだ作業」。後の check-only 監視セッションではない)。パス特定は `retro` Step 1 と同じ slug 規則 (`pwd` の `/` `.` を `-` 置換 → `~/.claude/projects/<slug>/` 最新 `*.jsonl`)。
- 初回登録時は `known_comment_ids: []` / `known_failing_checks: []` / `escalations: []` / `last_head_sha: <Step1 で観測した head_sha>` / `poll_interval_seconds: 60` で開始する。
- `--check-only` で再入した時はこのファイルを Read し、Step 4 の結果で `known_*` / `last_head_sha` / `poll_interval_seconds` / `last_checked_at` を更新する (`monitor_mode` / `schedule_id` / `origin_transcript` は保持)。

### Step 3 — 待機手段を優先順で選ぶ

登録前に**利用可能なものを確認**し、使えるものを上から選ぶ (環境で可否が変わる):

| 優先 | 手段 | 動作 | state に書く |
|---|---|---|---|
| 1 | `/schedule` (cron / routines) | `pr-monitor <n> --check-only` を定期実行する cron を登録し、**main を解放**。間隔変更のコストが高いため固定 30 分のままでよい (指数バックオフは効かない) | `monitor_mode: cron`, `schedule_id: <登録した id>` |
| 2 | `ScheduleWakeup` | cron が無ければ session 内で `delaySeconds` に state の `poll_interval_seconds` を渡して self-pace poll。起床ごとに Step 4 を実行し、未決着なら更新後の `poll_interval_seconds` で再度 `ScheduleWakeup` | `monitor_mode: wakeup`, `schedule_id: null` |
| 3 | 手動 | どちらも不可なら「`pr-monitor <n> --check-only` を後で再実行してください」と案内して終了 | `monitor_mode: manual`, `schedule_id: null` |

環境に `Monitor` ツール (条件監視) があれば、手段 2 の代わりにそちらへ委譲してよい — 使えるなら使う程度の位置づけで、`ScheduleWakeup` と同じ `poll_interval_seconds` を待機条件に使う。

`ScheduleWakeup` の `prompt` には `pr-monitor <n> --check-only` を渡し、次回起床で本スキルに戻れるようにする。採用した `monitor_mode` (と cron なら `schedule_id`) を **必ず state に書く** — 再入時の OPEN ブランチはこれを読まないと「次に wakeup を予約すべきか」「決着時に何の cron を解除するか」が判らない。

ポーリング間隔は state の `poll_interval_seconds` で管理する (基準 60 秒、上限 1800 秒)。この値の更新ルールは Step 4 の末尾で扱う。

### Step 4 — 状態判定 (毎ポーリング)

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/prm" status <n>
```

を 1 回叩き、返った JSON と state ファイルの前回スナップショットを突き合わせて、次の順で分岐する:

1. `pr.state` が `MERGED` / `CLOSED` → **決着**。状態ファイルの `state` を更新し、`monitor_mode: cron` なら `schedule_id` の cron を解除 (one-shot で消さないと check-only が鳴り続け `retro` が再起動し続ける)。**ここで判定終了** (以下は評価しない) — Step 5 へ。
2. `pr.head_sha` が state の `last_head_sha` と異なる → 新規 push を検知。`known_failing_checks` を **クリア** する (CI が新しい head で再走するため、前回の失敗名を引き継ぐと新 CI 上の再失敗を見落とす)。この判定を失敗 check の評価 (4) より **前** に行う — push と同時に来た新規失敗が、クリア前の古い `known_failing_checks` と比較されて同一ポーリング内で「既知」と誤判定されるのを防ぐ (M1)。
3. `known_failing_checks` を **prune** する: 現在の `checks.failing` に載っている名前との積集合に絞る (2 の全クリアは、新 push 時に積集合を取るまでもなく丸ごと消せるという、この prune の特殊形)。回復して `checks.failing` から消えた check 名は積集合から自然に落ち、後日再失敗した際に (4) で「新規」として再検知される。ただし `ci-self-heal` が `HALTED` を返し続けている check は `checks.failing` に居座り続けるため prune で落ちず、再 dispatch されないまま「エスカレーション分岐」(後述) の状態を維持する。

   同じタイミングで、次の 3 つも合わせて prune する。dedup 台帳が失効しないと、同じ名前・同じスレッドの **別インシデント** が二度と通知されなくなり、エスカレーション (無監視放置の防止) の存在意義が長期運用で崩れるため:
   - `escalations` の `kind: ci-halted` エントリ: `key` (check 名) が現在の `checks.failing` に **無ければ** 削除する (= 回復した。次に同名 check が失敗したら新インシデントとして再検知・再エスカレーション可能になる)。
   - `known_comment_ids` を、現在の `unresolved_threads` の `comment_id` 集合との積集合に絞る (state ファイルの無限肥大防止 + resolve 済みスレッドが後で再オープンされた際に新規スレッドとして検知できるようにするため)。
   - `escalations` の `kind: review-stuck` エントリ: `key` (comment_id) が現在の `unresolved_threads` に **無ければ** 削除する (= スレッドが resolve された)。
4. `checks.failing` の中に (3 で prune 済みの) `known_failing_checks` に無い名前がある → 新規失敗。`ci-self-heal` を使う subagent を Task で dispatch し (契約は次項)、対象 check 名を `known_failing_checks` に追記する。
   - 返った `verdict` が `HALTED` (3-failure architecture gate / flaky / env / infra) → 「エスカレーション分岐」(後述) に従う。`known_failing_checks` への追記はそのまま行い (再 dispatch させないため)、次回以降のポーリングでも 3 の prune で落ちない限り「新規」扱いにしない。
5. `unresolved_threads` の `comment_id` のうち `known_comment_ids` に無いものがある → 新規の未解決レビュースレッド。`pr-review-respond` を使う subagent を Task で dispatch し (新規分の author が全て CodeRabbit なら、契約入力に「修正適用は `coderabbit:autofix` skill に委譲する。plugin がある環境のみ、無ければ `pr-review-respond` 通常経路」と明記する)、対象 `comment_id` を `known_comment_ids` に追記する。
   - dispatch した subagent が **終端分類 (VALID / INVALID_PUSH / VALID_DEFER / DUPLICATE) できないコメントを残した** (handback に未終端コメントの報告がある、`WAITING` のまま返った 等) → 「エスカレーション分岐」(後述) に従う。`known_comment_ids` への追記はそのまま行う (再 dispatch しないため)。
6. 4 と 5 の dispatch は **同一ポーリング内で逐次** (`ci-self-heal` → `pr-review-respond` の順)。同一 PR ブランチを共有し双方が push しうるため並列にしない。
7. 2・4・5 のいずれかに該当した (状態に変化があった) 場合、`poll_interval_seconds` を 60 に **リセット** する。いずれにも該当しなかった場合は現在値を 2 倍 (上限 1800) にする。
8. `last_head_sha` を今回の `head_sha` に、`last_checked_at` を現在時刻に更新して state ファイルへ書き戻す。

state ファイルの `monitor_mode` で OPEN 時の次アクションを分岐する (再入時は `--check-only` 引数だけでは手段が判らないため。`MERGED` / `CLOSED` は分岐 1 で判定終了済み — Step 5 へ):

| state | 次の手 |
|---|---|
| `OPEN` | Step 4 の 2〜8 を実施後、`monitor_mode: cron` なら何もせず終了 (次回 cron 起床に任せる)、`monitor_mode: wakeup` なら更新後の `poll_interval_seconds` で再度 `ScheduleWakeup`、`manual` なら手動再実行を案内 |

#### エスカレーション分岐 (`ci-self-heal` HALTED / `pr-review-respond` 終端未達)

4 または 5 で dispatch した subagent が上記の条件 (HALTED / 終端未達) を返した場合、**対象の check / comment_id は `known_*` に残したまま再 dispatch しない**。ただし **監視自体は継続する** — merge / close 検知 (分岐 1) は止めず、Step 4 の残り (7・8) やバックオフ・待機手段の予約も通常どおり行う。

- 対応する `key` (`ci-halted` は check 名、`review-stuck` は `comment_id`) が state の `escalations` に既に同じ `kind` で記録されている場合、**同じ事象への 2 度目のコメント投稿はしない** (dedup)。
- 未記録なら:
  1. `gh pr comment <n>` で needs-human 向けの構造化コメントを 1 件投稿する。最低構成:

     ```markdown
     ## needs-human: <ci-halted | review-stuck>

     - What: <HALTED になった check 名 / 終端分類できなかったコメントの URL>
     - Handback: <dispatch した subagent の handback 要点 1-2 文>
     - Next: <人間が取るべき次の一手 1 文 (例: architecture 再考 / 該当スレッドへの直接判断)>

     pr-monitor は監視を継続します。対応後の新しい push で該当 check が prune されれば自動的に再検知されます。
     ```

  2. state の `escalations` に `{kind: ci-halted|review-stuck, key: <check名|comment_id>, at: <ISO8601>}` を追記する。

人間が対応した後の新 push で `known_failing_checks` が (2 の全クリア、または 3 の prune で) 落ちれば、次のポーリングで自然に再検知・再 dispatch される。

`review-stuck` の回復パスはスレッドの **resolve / 再オープン** であり、スレッド内への追記ではない — `prm` は `comments(first: 1)` でルートコメントのみ取得するため、スレッド内で人間が返信しても `comment_id` は変わらず、追記だけでは新規検知のトリガにならない。観測可能な回復シグナルは次の 2 つ:
- 人間が当該スレッドを **resolve** すれば `unresolved_threads` から消え、3 の prune により `escalations` の `review-stuck` エントリと `known_comment_ids` の両方から失効する。
- そのスレッドが後で **unresolve (再オープン)** されれば、`known_comment_ids` は既に prune 済みのため (5) で新規スレッドとして再検知され、`pr-review-respond` へ再 dispatch される。

#### dispatch 契約 (簡略)

`shipping` の Subagent 起動契約と同型。Task で新規 subagent を 1 つ起動し、次だけ渡し、次だけ返させる:

- 入力: 対象 PR 番号 / 対象 (`ci-self-heal` なら failing checks の名前列、`pr-review-respond` なら新規 `unresolved_threads` の `thread_id`・`comment_id`・`author`・`url`・`body_head`) / (該当時) CodeRabbit 委譲の明記
- 返す構造: `verdict` (`ci-self-heal` は PASS/HALTED、`pr-review-respond` は未終端 n→m)、`pushed_commits` (この task で push した SHA 列 / none)、`handback` (呼出側が次に判断するのに要る最小ブロック)

本スキルは `verdict` / `pushed_commits` / `handback` だけを読み、`known_*` へ追記して次ポーリングへ戻る。dispatch 先が例外・timeout で失敗した場合は `known_*` への追記が起きないため、次回ポーリングで同じ check / thread が「新規」として再検知・再 dispatch される (取りこぼしより重複 dispatch を許容する設計)。`verdict` が HALTED / 終端未達の場合は上記「エスカレーション分岐」に従う。

### Step 5 — 決着したら retro

`Skill(retro)` を起動し、「PR #<n> が <merged/closed> した」コンテキストと **state の `origin_transcript` パス** を渡す。これにより retro は「最新の transcript」ではなく **PR を生んだ元セッション** を解析する (check-only の監視セッションを誤って解析しない)。`origin_transcript` が未記録 (Step 1 直行など) のときだけ retro 既定の最新 transcript 選択にフォールバックする。retro が改善提案 (提案のみ) を出して pr-monitor は完了。

## 出力フォーマット

```markdown
# pr-monitor: PR #<n> (<branch>)

## 監視
- state: <OPEN→…→MERGED/CLOSED>
- 手段: <cron / ScheduleWakeup / 手動>
- poll_interval_seconds: <n>
- last_checked_at: <ISO8601>

## 観測 (直近ポーリング)
- checks.failing: <件数 (名前列) / なし>
- checks.pending: <件数 / なし>
- unresolved_threads: <unresolved_count 件>

## dispatch 履歴
- <ISO8601> ci-self-heal dispatch (<check 名>) → verdict: <PASS/HALTED>
- <ISO8601> pr-review-respond dispatch (<comment_id 列>, coderabbit:autofix 委譲: <あり/なし>) → verdict: <未終端 n→m>

## エスカレーション
- <escalations 件数 (kind/key 列, 新規投稿分には needs-human コメント URL) / なし>

## 決着
- <MERGED <SHA> / CLOSED / 監視中 (次回 <手段>, interval <n>s)>
- Next: <retro 起動済み / 次ポーリング予定 / 手動再実行案内>

verdict: <MONITORING (<cron|wakeup|manual>) / SETTLED (<MERGED|CLOSED>) / ESCALATED>
```

`verdict` は次の 3 トークンに固定する (`shipping` Phase 6 がこの report を dispatch 結果として読む契約。`skills/shipping/SKILL.md` の Phase 6 verdict 表記と完全一致させる):

- `SETTLED (<MERGED|CLOSED>)`: Step 4 分岐 1 (または Step 1) で決着を検知したポーリング。
- `ESCALATED`: このポーリングで新規のエスカレーション (needs-human コメント投稿) が発生した、または既存の未解消エスカレーション (`escalations` に記録済みで対象がまだ prune で落ちていない) を抱えたまま終了するポーリング。**決着ではない** — 監視自体は継続し Step 5 (retro) へは進まない。
- `MONITORING (<mode>)`: 上記いずれでもない通常の監視継続。

### 出力する成果物

- **状態ファイル** `.claude/.pr-monitor/PR-<n>.yml` (consumer gitignore 前提、`known_*` / `escalations` / `last_head_sha` / `poll_interval_seconds` 込み)
- **監視サマリ** (state 遷移 + 採用した待機手段 + 観測値 + dispatch 履歴 + エスカレーション + 次アクション)
- **CI 失敗検知時の `ci-self-heal` subagent dispatch** (検知と起動のみ。修復自体は `ci-self-heal` の成果物)
- **新規未解決レビュースレッド検知時の `pr-review-respond` subagent dispatch** (検知と起動のみ。返信・修正コミットは `pr-review-respond` / `coderabbit:autofix` の成果物)
- **エスカレーション時の needs-human コメント** (`gh pr comment` 経由、同一事象で 1 回のみ投稿)
- **決着時の retro 起動**

### 出力しない成果物
- **PR の merge / squash / close 操作**: 決着は人間または別自動化。本スキルは事実を待つだけ。
- **CI ログ取得 / 修復そのもの**: `ci-self-heal` の領域。本スキルは `prm status` の `checks.failing` という観測値だけ見て dispatch する。
- **コメントへの返信・修正コミットそのもの**: `pr-review-respond` (CodeRabbit 起因の修正適用は `coderabbit:autofix`) の領域。本スキルは `unresolved_threads` という観測値だけ見て dispatch する。
- **foreground の長時間 sleep / watch**: main をブロックしない。cron / ScheduleWakeup / Monitor に委ねる。
- **リポ追跡されるログ**: 状態は gitignore パスのみ。

## 既知の限界
- **cron の可否は環境依存**: `/schedule` が無い環境では ScheduleWakeup (session 生存中のみ) か手動にフォールバックする。
- **cron モードでは指数バックオフが効かない**: `/schedule` 登録は固定間隔 (30 分) のため、`poll_interval_seconds` の伸縮は state に記録されても待機間隔には反映されない。バックオフの実効果は `ScheduleWakeup` / `Monitor` 手段でのみ現れる。
- **session 終了で ScheduleWakeup は途切れる**: 長期 (日単位) 監視は cron 手段が前提。手段 2 は session が生きている間だけ。
- **dispatch 先 subagent の失敗は次ポーリングで再検知される**: `known_failing_checks` / `known_comment_ids` への追記は dispatch 呼び出しの後に行うため、dispatch 自体が例外・timeout で失敗すると追記が起きず、次回ポーリングで同じ check / thread が再 dispatch される。取りこぼしより重複実行を許容する設計だが、subagent が起動だけして完了しなかった場合の重複コストは残る。
- **逐次 dispatch 前提でレイテンシが伸びる**: `ci-self-heal` と `pr-review-respond` を同一ブランチ push 競合回避のため並列にしない分、1 ポーリングあたりの所要時間は両方が完了するまで伸びる。
- **マルチモデル未検証**: trigger eval は本セッションのモデルのみ。
