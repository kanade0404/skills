---
name: shipping
description: 実装が上流スキル (`design`/`software-design` → `tdd`/`tidy-first`) で GREEN になった後の **出荷専用ターミナルステージ**を、各フェーズを fresh subagent に dispatch するオーケストレータスキル。品質ゲート (`code-review`) → 完了ゲート (`verify-done`) → PR materialize (open PR が無ければ `commit-commands:commit-push-pr`、あれば push) → CI 緑化 (`ci-self-heal`) と自動レビュー対応 (`pr-review-respond`、CodeRabbit/Devin/Copilot/人間、CodeRabbit 修正は `coderabbit:autofix` へ委譲) を、CI 全 pass かつ全コメント終端まで回し、行き詰まったら escalate する。収束後は SHIPPED 前に `pr-monitor` を subagent dispatch し監視設置を確認する。コード修正は behavioral→`tdd` / structural→`tidy-first` の subagent にルーティングし、本スキルはコードを書かずループ制御と収束/escalation 判定だけを main で持つ。「ship して」「実装できたから後は全部やって PR 出して CI もレビュー対応も全部通してマージできる状態にして」「commit-push-pr の検証付き版で」「赤と指摘を全部潰して merge-ready に」のような実装後に出荷まで丸ごと任せる要請で必ず起動すること。commit だけは `commit-commands:commit`、検証ループ不要の commit→push→PR だけは `commit-push-pr`、コードレビューだけは `code-review`、既存 PR のコメント対応だけは `pr-review-respond`、CI 修復だけは `ci-self-heal`、完了確認だけは `verify-done`、実装そのもの (設計/コーディング/未 GREEN/WIP) は上流が担い範囲外。PR は merge せず merge-ready で停止する。
claudecode:
  allowed-tools:
    - Read
    - Agent
    - Bash(gh pr view *)
    - Bash(gh pr list *)
    - Bash(gh pr checks *)
    - Bash(git status *)
    - Bash(git diff *)
    - Bash(git log *)
    - Bash(git rev-parse *)
    - Bash(git push *)
---

# Shipping

> **Iron Law**: GREEN な実装を、CI 全 pass かつ全レビュー指摘が終端状態になるまで「出荷」しきる。途中で完了宣言しない。行き詰まったら回し続けず escalate する。
> **構成原則**: 各フェーズは fresh subagent に dispatch する。orchestrator (本スキル) は main セッションに残り、ループ制御・収束 / escalation 判定・git/gh 観測だけを持つ。コードは書かない。

`commit-commands:commit-push-pr` が「commit → push → PR」を 1 ショットで行うのに対し、本スキルは **その前段に品質ゲートを挟み、後段に CI 緑化とレビュー対応の収束ループを足した出荷ターミナルステージ**。実体は各フェーズの subagent が持ち、本スキルは順序とループ制御だけを持つ。

---

## 開発フローの中での位置

本スキルは開発の **終端ステージ**。上流が GREEN を作り、本スキルがそれを出荷しきる。実装そのものは駆動しない。

```text
design / software-design   →   tdd / tidy-first   →   shipping (本スキル)
   (設計を ADR に蒸留)          (実装し GREEN 化)        (出荷収束)
```

- **上流からの handoff**: 「実装が GREEN」かつ「設計判断が済んでいる」状態を受け取る。未 GREEN / WIP / 設計未確定なら受けず、上流スキルに戻す (前提条件で弾く)。
- **修正ルーティング**: pipeline が要するコード変更 (Phase 1 の Important / Phase 4 の CI 修正・VALID 修正) は **本スキルが書かず**、dev/fix スキルの subagent に出す:

  | 変更種別 | dispatch 先 subagent |
  |---|---|
  | behavioral change (振る舞いが変わる) | `tdd` (RED → GREEN) |
  | structural change のみ (リファクタ / rename / 抽出) | `tidy-first` |
  | Critical / 設計レベルの破綻 | escalate して `design` / `software-design` に戻す |

  `ci-self-heal` / `pr-review-respond` は内部で既にこのルーティングをするため、本スキルは Phase 1 の差し戻し分だけ自分でルーティングする。

  修正 subagent の dispatch prompt には **`Skill(tdd)` / `Skill(tidy-first)` の起動指示を明示的に含める** — 自由文の「tdd の規律で」だけだと skill 本文がロードされず、無名 subagent の直編集に流れて Skill 発火の traceability が失われる (実測: PR #96 セッションで修正 3 件が全て未発火)。テスト基盤が無い差分 (markdown / 設定等) では RED→GREEN が成立しないため、代わりに**再現→修正→再現ケース消失の確認**など代替検証手段を prompt で明記させる。

---

## 構成方式: subagent dispatch

各フェーズは Task で **新規 subagent** を 1 つ起動して回す。理由: フェーズごとに context を隔離でき (長い CI 待ちで main を汚さない)、各 subagent が現在状態を読み直すので CI / コメントの再評価が常に新鮮になる。

- **使い回さない**: サイクルごとに fresh subagent を立てる。読了済み subagent の再利用は状態の陳腐化とバイアスを生む (`skill-builder` Mode C と同じ規律)。
- **逐次 (並列にしない)**: 1 サイクル内の `ci-self-heal` と `pr-review-respond` は **同一 PR ブランチを共有**し、双方が修正 commit を push しうる。並列 dispatch は commit の競合・交錯を生むので順次に回す (`dispatching-parallel-agents` の shared-state 規律)。順序は ci-self-heal → pr-review-respond (reviewer が緑の PR を見る形にする)。
- **subagents/ には書かない**: 配布リポの `subagents/` は placeholder。本スキルは Task dispatch で構成し、`subagents/` にファイルを作らない。

### Subagent 起動契約 (テンプレ)

各フェーズ subagent にこの構造で渡し、この構造だけを返させる:

```text
あなたは <skill 名> を使う実行者です。実装者の前提知識は持ち込まない。
## 入力
- 対象 PR: #<n> (無ければ「未作成」) / base: <branch> / scope: <変更ファイル or 差分範囲>
- (修正系のみ) 対応すべき findings / failure / thread: <...>
## 使うスキル
<skill 名> を起動し、その規律に厳密に従う。
## 返す構造 (これだけ返す)
- verdict: <skill 固有 — 下表の「読む値」>
- pushed_commits: <この task 中に push した SHA / none>
- handback: <呼出側が次に判断するのに要る最小ブロック (findings 要約 / architecture 仮説 / 未終端コメント URL)>
```

本スキルは `verdict` / `pushed_commits` / `handback` だけを読む。subagent 本体出力は再掲しない。

| フェーズ | dispatch 先 | 読む verdict |
|---|---|---|
| 整形 (Phase 1a) | **Task 契約 dispatch** (named skill ではない。`tidy-first` 規律の品質専用クリーンアップ契約。`Skill(simplify)` は存在しないので呼ばない) | SIMPLIFIED / NO_CHANGE |
| 品質ゲート (Phase 1b) | `code-review` | PASS / PASS_WITH_FIXES / FAIL |
| 完了ゲート | `verify-done` | PASS / FAIL (+ Verification ブロック literal) |
| PR 作成 | `commit-commands:commit-push-pr` | PR URL / number |
| CI 緑化 | `ci-self-heal` | PASS / HALTED |
| レビュー対応 | `pr-review-respond` | 未終端コメント n→m |
| 修正 | `tdd` / `tidy-first` | pushed_commits |
| 監視設置 (Phase 6) | `pr-monitor` | `MONITORING (<mode>)` / `SETTLED (<MERGED\|CLOSED>)` / `ESCALATED` |

---

## いつ起動するか

実装が **上流スキルで GREEN になった後**、そこから先 (レビュー・CI・PR 化) を最後まで通したいときの単一エントリ。

- 「ship して」「出して」「これ出して全部通して」
- 「コミットして PR 作って CI もレビュー対応も全部やってマージできる状態にして」
- 「commit-push-pr の検証付き版で」「実装できたから後は全部おまかせ」
- 「赤と指摘を全部潰して merge-ready にして」

逆に **起動しない** (名指しで別スキルに渡す):

| ユーザの要求 | 渡す先 |
|---|---|
| commit だけ | `commit-commands:commit` |
| 検証ループ不要の commit→push→PR だけ | `commit-commands:commit-push-pr` |
| コードレビューだけ | `code-review` |
| 既存 PR のコメント対応だけ | `pr-review-respond` |
| CI 失敗の修復だけ | `ci-self-heal` |
| 完了確認だけ | `verify-done` |
| 設計 / コーディング / 未 GREEN / WIP | 上流 `design` / `software-design` / `tdd` / `tidy-first` |

---

## 前提条件

起動時に次を確認する。満たさなければパイプラインに入らず、足りない条件を名指しで返す:

- 実装対象が一段落し **上流スキルで GREEN** (WIP の途中分割点ではない)
- 設計判断が確定している (未確定なら `design` / `software-design` に戻す)
- 作業ブランチが default ブランチ (`main` / `master`) ではない
- `git status` に修正と無関係な巻き込み変更が無い

---

## パイプライン

6 フェーズを順に通す。各フェーズは上記契約で subagent を 1 つ dispatch し、返った `verdict` を下表で読む。**本スキルは差し戻し先のコードに手を入れない** — 修正は dev/fix subagent が行う。

### Phase 1 — 品質ゲート (1a simplify → 1b code-review)

実装直後の差分をまず **整形 (simplify)** し、整えた差分を `code-review` に通す (整えてから bug 観点で読む)。1a / 1b は各々 fresh subagent、逐次。

**Phase 1a — simplify**: fresh subagent に「**reuse / simplification / efficiency / altitude の品質専用クリーンアップのみ**。振る舞いは変えない。バグ探索・仕様変更・新規実装はしない (それは `code-review` / 上流の領域)」契約で dispatch。整形は structural change なので `tidy-first` の規律に従い behavioral change と混ぜず、**structural commit を local に積む** (push は Phase 3)。verdict: `SIMPLIFIED` (差分を commit した) / `NO_CHANGE`。`SIMPLIFIED` なら以降の Phase は整形後の差分を対象にする。

**Phase 1b — code-review**: 整形後の差分に `code-review` を使う subagent を dispatch。

| code-review verdict | 次の手 |
|---|---|
| PASS | Phase 2 へ |
| PASS_WITH_FIXES | Important を「開発フローの中での位置」の修正ルーティング表 (behavioral→`tdd` / structural→`tidy-first`) に従い fresh 修正 subagent に渡し、修正後差分で fresh `code-review` subagent を再 dispatch |
| FAIL (Critical>0) | パイプライン中断。handback の findings を添え BLOCKED 報告。Critical 対処は上流 (`design` / 実装) の仕事 |

`code-review` を 3 巡しても PASS に到達しないなら個別 finding 潰しをやめ、実装方針を疑い escalate (後述)。

### Phase 2 — 完了ゲート

`verify-done` を使う subagent を dispatch。subagent は検証コマンドを実行し **Verification ブロックを literal で** handback に返す (新鮮さが監査可能なため要約は受けない)。

| verify-done verdict | 次の手 |
|---|---|
| PASS | Phase 3 へ |
| FAIL | 落ちた検証を Phase 1 の修正ルーティングで直し、差分が変わるため Phase 1 から再開 |

### Phase 3 — PR materialize

作業ブランチに対し open PR があるか観測する (本スキルが直接):

```bash
gh pr list --head "$(git rev-parse --abbrev-ref HEAD)" --state open --json number,url
```

| PR の状態 | 次の手 |
|---|---|
| open PR 無し | `commit-commands:commit-push-pr` を使う subagent を dispatch (commit + push + PR 作成) |
| open PR あり | ローカル commit を PR ブランチに `git push` (本スキルが直接)。PR は既存を再利用 |

PR number / URL を確保して Phase 4 へ。

### Phase 4 — 収束ループ

push 後、以下を **1 サイクル**として回す。(a)(b) は逐次:

- **(a) CI**: `ci-self-heal` を使う subagent を dispatch。CI watch → root-cause → 修正 (内部で `tdd`/`tidy-first`) → 再 push → 再 watch を内部で回し、緑なら `PASS`、3-failure / flaky / env / infra なら `HALTED` を返す。**(a) の完了条件は CI checks の終端のみ** — 自動レビュー (CodeRabbit 等) の完了待ちを含めない。既に checks が全て終端し**緑** (fail / cancel が 0) なら watch を張らずそのまま (b) へ進む — この「終端済みか」の判定は **(a) の `ci-self-heal` subagent が初回観測で行う** (orchestrator は「もう緑だろう」と推測して (a) の dispatch 自体を省略しない)。終端でも `fail` / `cancel` を含むなら緑扱いで素通りせず、watch 不要でも `ci-self-heal` の修復ループ (root-cause → 修正 → 再 push → 再 watch) は踏む (レビュー完了を待ち合わせると、既存レビュースレッドの対応 (b) が starve する。実測: PR #96 でレビュー 7 件が約 8 時間放置)。
- **(b) レビュー**: `pr-review-respond` を使う subagent を dispatch。契約の入力で **fetch / triage 対象に CodeRabbit / Devin / Copilot / 人間を含めるよう明示**する。Copilot は `pr-review-respond` のベンダー判定上 `human` に分類され、VALID / VALID_DEFER / DUPLICATE は他 vendor と同じく GraphQL `resolveReviewThread` で直接 resolve される (ディレクティブ併記なし)。resolve されず残るのは INVALID_PUSH (根拠付き reply のみ) と Withdrawn (レビュアー撤回 — スレッド未 resolve のまま終端)。**CodeRabbit 起因の指摘への修正適用は、`coderabbit` plugin が入っている環境では `coderabbit:autofix` (per-change approval 付き) に委譲する**ことを契約入力で明示する — plugin が無い環境では従来どおり `pr-review-respond` 内の修正経路 (`tdd` / `tidy-first` ルーティング) を使う。VALID は修正 commit、INVALID_PUSH は根拠付き pushback、VALID_DEFER は issue 化、DUPLICATE は参照。
- **(c) 状態判定**: このサイクルで PR に新規 push があったかを、サイクル開始前後の PR リモート head (`gh pr view <PR> --json headRefOid`) の比較で確認する。ローカル `git rev-parse HEAD` の前後比較は判定基準にしない — commit 作成後に push が失敗していても変化してしまい (GitHub に無い SHA を「新規 push」と誤認して CI watch を始める)、別プロセスによる push は見逃す。(a)(b) が報告した `pushed_commits` は `headRefOid` の変化と突き合わせ、`headRefOid` が変わっていないのに `pushed_commits` が非空なら push 失敗として stall 扱いで escalate する。

(a) `ci-self-heal` が `HALTED` を返したら、**同サイクルの (b) を dispatch せず即 escalate** する (HALTED は終端。先へ進めない)。

**待機の所有 (「待ち = 起こし」)**: 待ちは呼び出し元が所有し、**subagent に「background の待ち」を持たせない**。前提となる harness 制約 (2026-08-01 の対照実験で確定): 同期 dispatch した subagent が起動した background タスク (Monitor / background Bash) は **subagent の return と同時に harness に回収される** (登録直後の消滅を再現)。subagent には await 原語 (`TaskOutput`) も存在しないため、subagent 内で意味を持つ待ちは **foreground blocking のみ**。この制約の下で本スキルの待機設計は 2 層で完結する:

1. **orchestrator の待ち = (a)(b) の同期 dispatch そのもの**。subagent の return まで blocking で自然に戻る。第 2 の wake 経路・`Monitor`・`TaskStop` 撤収はいずれも不要 — background タスクを一切作らないから。orchestrator 自身が subagent として実行されている場合 (例: セッション main が本スキルを model 指定で background dispatch した場合) も同じで、同期 dispatch の連鎖は全段 blocking で戻る。
2. **subagent 内の外部イベント待ち (CI 等) = `ci-self-heal` の `scripts/wait_gate.sh` の foreground blocking 呼び出し** (deadline ≤ 8 分で必ず exit し、exit code `0`/`1`/`2`/`3` で機械分類。deadline 超過は呼び直しで継続し、呼び直しごとに意思決定が戻るため無限待ちにならない)。

orchestrator の time-box fallback (allowed-tools 内の直接観測 — `gh pr checks` / `git rev-parse`) は、dispatch 自体が返らない異常への最終保険としてのみ残す。ただし到達性を明確にする: 同期 dispatch 中の orchestrator は blocking しており、fallback を自力では発火できない (harness に同期 dispatch の caller 側 timeout 原語は無い)。同期経路での「dispatch は必ず返る」保証は **callee 側の有界待ち**が担う — subagent 内の外部待ちは `wait_gate.sh` に限定され (deadline ≤ 8 分 / 回で必ず exit、`gh` 呼び出し自体も 60 秒 cap で有界化、累計 32 分で escalate)、無限 blocking は設計上発生しない。fallback を実際に発火できるのは、生きた turn を持つ待ちの所有者がいる構成のみである。セッション main が本スキルを background dispatch して離れる場合、main 側の deadline は **main 所有の** background タスクで張る — main 所有の「exit → 完了通知 → 再起動」だけが実測で機能した経路であり (PR #96 の merge 27 秒検知と同構成)、subagent 所有の待ちは全て実測で消滅した (Monitor 満了通知の不達で約 8 時間 / subagent 内二重 wake の回収で数十分、の stall 2 件)。

サイクル終了時の遷移:

| サイクル結果 | 次の手 |
|---|---|
| このサイクルで新規 push があった | CI が再走しコメントも付き直すため、次サイクルへ |
| 新規 push なし **かつ** CI 緑 (fail / cancel が 0。skipping / neutral は非失敗) **かつ** 未終端コメント = 0 | **収束**。Phase 5 へ |
| `ci-self-heal` が HALTED | escalate (後述) |
| 新規 push なし **だが** CI 赤 or 未終端コメント > 0 | stall。escalate (同じ状態を回し続けない) |

ループの駆動因子は **このサイクルで新規 commit が push されたか** の 1 点。判定に使う 2 値を固定する:

- **未終端コメント数**: `pr-review-respond` が終端分類のいずれにも終端化していないコメント数。VALID (修正 commit 済) / INVALID_PUSH (根拠付き pushback) / VALID_DEFER (issue 化) / DUPLICATE (参照) / 撤回 (Withdrawn — レビュアーが指摘を撤回し争点消滅、スレッドは未 resolve のまま) は **すべて終端 = 0 算入**。triage 済みの低価値 nitpick は返信のみで終端化し新規 VALID commit を生まないため未終端に数えない。
- **新規 push の有無**: 本スキルがサイクル前後の PR `headRefOid` (`gh pr view <PR> --json headRefOid`) を比較して観測する (リモート head が真実。ローカル HEAD は push 失敗を区別できない)。

この定義から「CI 緑 **かつ** `pr-review-respond` が新規 commit を生まなかった」サイクルが 1 回取れれば自動的に未終端 0 / 新規 push 無しとなり収束する (nitpick を無限に追う特例ルールは不要)。**INVALID_PUSH を「対応済み」に数える**のは盲従しないための `receiving-code-review` 規律 — 根拠を残せば収束を妨げない。

### Phase 5 — 最終ゲート

収束したら `verify-done` を使う subagent を再 dispatch し、最終の完了宣言用 fresh evidence (Verification ブロック literal) を取る。PASS を取ってから Phase 6 へ。**PR の merge / squash / ブランチ削除はしない** — 最終状態は「人間が merge できる状態」で停止する。

### Phase 6 — 監視設置

**なぜ**: `rules/pr-push-discipline.md` の帰結 3 (「離れる前の監視」) は常駐 rule で「いつ必ずやるか」を定めるが、rule 単体では「SHIPPED に飛びつく」完了報告直前の慣性を止められない。本スキルの終端フェーズとして構造的ゲートにすることで、rule (規範) と skill (手続き) の二重化で初めて帰結 3 が実効化する。**SHIPPED は監視設置を確認してから報告する** — Phase 5 の PASS だけでは報告しない。`pr-monitor` が cron も `ScheduleWakeup` も使えず手動フォールバック (`monitor_mode: manual`) に倒れた場合、後続の CI 失敗・新規レビューコメント・merge/close を検知する常駐プロセスは何も残らない — 帰結 3 が求める「手段を残す」を満たさないため、`manual` は監視設置の成功に数えない。

`pr-monitor` を使う subagent を Task で dispatch する (fresh subagent。「Subagent 起動契約 (テンプレ)」に従う。main は監視を待たない):

- **契約入力**: 対象 PR 番号 / `origin_transcript` パス (「PR を生んだ本セッション」の transcript。retro が解析すべき対象であり、後の `--check-only` 監視セッションではない)
- **subagent 内の振る舞い**: `pr-monitor` は cron 登録 (`/schedule`) または `ScheduleWakeup` self-pace poll、どちらも不可なら手動再実行案内のいずれかを選び、**subagent 自体は常駐しない** (main を塞がない)。拡張後の `pr-monitor` は merge / close の検知に加え、新規未解決レビュースレッド・checks 失敗も `prm status` で検知し、`ci-self-heal` / `pr-review-respond` / (CodeRabbit 起因なら) `coderabbit:autofix` の subagent dispatch まで担う (`pr-monitor` 側の責務。本スキルは dispatch するだけ)。
- **読む verdict**: `MONITORING (<mode>)` (`mode` は cron / wakeup / manual のいずれか。**`manual` は監視プロセスが何も残らないため監視設置の成功には数えない** — cron / wakeup のみ成功扱い) / `SETTLED (<MERGED|CLOSED>)` (dispatch 時点で既に merge / close していた場合) / `ESCALATED` (dispatch した poll で `ci-self-heal` HALTED または `pr-review-respond` 終端未達によるエスカレーションが発生し、needs-human コメントを投稿した場合。監視自体は `pr-monitor` 側で継続する)

| Phase 6 verdict | 次の手 |
|---|---|
| `MONITORING (cron)` / `MONITORING (wakeup)` | 監視設置済みとして SHIPPED 報告 |
| `MONITORING (manual)` | **監視設置とはみなさない**。cron も `ScheduleWakeup` も使えない環境では、後続の CI 失敗・新規レビューコメント・merge/close を検知するプロセスが何も残らない。SHIPPED にせず Verdict を **`MONITOR_UNAVAILABLE`** とし「MONITOR_UNAVAILABLE: 監視プロセスが残せない環境。手動 `pr-monitor <n> --check-only` の定期実行が必要」と明記してユーザーに引き継ぐ |
| `SETTLED (<MERGED\|CLOSED>)` | 追加監視は不要。SHIPPED 報告に「dispatch 時点で既に決着済み」を明記 |
| `ESCALATED` | 監視は継続中だが needs-human コメント投稿済み。**SHIPPED ではなく ESCALATED 報告** — needs-human コメント URL と対象 (check 名 / comment_id) を明記して報告を終える |
| dispatch 失敗 / 上記いずれでもない verdict | **SHIPPED と報告しない**。Verdict を BLOCKED とし「監視未設置」を明記した上で、手動 `pr-monitor <n> --check-only` の実行を案内して報告を終える |

---

## Escalation gate (回し続けない安全装置)

次のいずれかで **ループを止めてユーザに返す**:

| トリガ | 報告 |
|---|---|
| `ci-self-heal` subagent が 3-failure architecture gate / flaky / env / infra で HALTED | handback の architecture 仮説を添えて ESCALATED |
| `code-review` が 3 巡しても PASS しない | 実装方針再考が必要として ESCALATED (`design` / `software-design` に戻す提案) |
| `pr-review-respond` が終端分類できないコメントを残した (本 PR スコープを超える設計判断が要る等) | 当該コメント URL と理由を添えて ESCALATED |
| 修正 subagent (`tdd`/`tidy-first`) が修正しきれず差分を返せない | 当該 findings と subagent handback を添えて ESCALATED |
| 1 サイクルで状態変化が無いのに未収束 (stall) | 直近サイクルの CI 状態と未終端コメントを添えて ESCALATED |

escalate 後は **ユーザの明示指示があるまで追加 dispatch / push をしない**。

---

## 出力フォーマット

ユーザへの最終報告は 1 メッセージ、この固定構造:

```markdown
# Shipping: <branch> → PR #<n>

## Pipeline (各フェーズ = 1 subagent dispatch; Phase 1 は 1a/1b の 2 dispatch)
- Phase 1a simplify: <SIMPLIFIED / NO_CHANGE>
- Phase 1b code-review: <PASS / PASS_WITH_FIXES×k → PASS / FAIL>
- Phase 2 verify-done: <PASS / FAIL>
- Phase 3 PR: <created <URL> / reused <URL>>
- Phase 4 収束ループ: <k サイクル>
- Phase 5 verify-done(final): <PASS>
- Phase 6 pr-monitor: <MONITORING (mode) / SETTLED (<MERGED|CLOSED>) / ESCALATED>

## Cycle ledger
1. ci-self-heal=<PASS/HALTED> / pr-review-respond=<未終端 n→0> / push=<直近 short SHA / none>
2. ...

## Verdict
- SHIPPED / BLOCKED / ESCALATED / MONITOR_UNAVAILABLE
- CI: <全 pass / 赤 n> (<run URL>)
- 未終端コメント: <0 / n>
- Next: <人間 merge 待ち / pr-monitor 監視中 (<mode>) / 手動 pr-monitor <n> --check-only の定期実行が必要 (MONITOR_UNAVAILABLE) / Critical 対処 / architecture 再考 …>

## Artifacts (各 subagent の handback への参照)
- code-review findings: <handback 参照>
- ci-self-heal attempt log: <handback 参照>
- pr-review-respond summary comment: <URL>
- verify-done Verification: <Phase 5 subagent の Verification ブロック literal>
```

各フェーズ subagent の本体出力 (findings 表 / attempt log / triage 表) は **再掲しない**。`handback` の参照だけ載せる。Cycle ledger の push SHA は本スキル自身の `git rev-parse HEAD` 観測値 (ループ制御状態) であり subagent 出力の再掲には当たらない。Verdict 行の数値の出所も固定する: `CI 赤 n` は `ci-self-heal` handback 引用値、`未終端 m` は `pr-review-respond` handback 引用値 (本スキル直接観測は push 有無のみ)。Verification ブロック literal を載せるのは **最終ゲート (Phase 5) のみ**。Phase 2 等それ以外のフェーズの Verification は handback 参照に留め、Phase 5 未到達でも昇格させない。

**早期終了時 (BLOCKED / ESCALATED)**: テンプレは全フェーズ完走を前提にしない。未到達フェーズ行は `未到達 (Phase N で <verdict>)` と書く。(a) `ci-self-heal`=HALTED で (b) を skip したサイクルは Cycle ledger 行を `pr-review-respond=未実行 ((a) HALTED で同サイクル skip)` と書く。Artifacts の `verify-done Verification` 行は、Phase 5 未到達なら `Phase 5 未到達のため記載なし` とする。BLOCKED / ESCALATED は例外ではなく Verdict の定義済み出口であり、この体裁で必ず 1 メッセージにまとめる。

**`MONITOR_UNAVAILABLE`**: BLOCKED / ESCALATED と異なり全 Phase (1〜6) は完走している — Phase 6 の `pr-monitor` dispatch が `MONITORING (manual)` を返した場合の縮退 Verdict であり、未到達フェーズ扱いにはしない。Pipeline ログの Phase 6 行はそのまま `MONITORING (manual)` と書き、その上で Verdict 行を `MONITOR_UNAVAILABLE` とする。

---

## 出力する成果物 / 出力しない成果物

成果物ベースで境界を定義する (動詞ではなく出力物で語る規約)。

### 出力する成果物

- **Pipeline ログ** (Phase 1–6 の判定 1 行ずつ、固定構造)
- **Cycle ledger** (1 行 = 1 サイクル: ci-self-heal verdict + pr-review-respond 未終端数 + push SHA)
- **Verdict 1 行** (SHIPPED / BLOCKED / ESCALATED / MONITOR_UNAVAILABLE + CI 状態 + 未終端数 + Next)
- **監視設置** (Phase 6: `pr-monitor` subagent dispatch の verdict — `MONITORING (<mode>)` / `SETTLED (<MERGED|CLOSED>)` / `ESCALATED`)
- **subagent handback への参照** + Phase 5 Verification ブロック literal

### 出力しない成果物

- **修正コード差分**: 本スキルは書かない。修正は `tdd` / `tidy-first` (Phase 1) / `ci-self-heal` / `pr-review-respond` (Phase 4) の subagent 出力。
- **code-review findings / CI attempt log / triage 表 / Verification ブロックの再掲表**: 各 subagent の handback が持つ。本スキルは参照のみ (Phase 5 Verification literal を除く)。
- **`subagents/` 配下のファイル**: 配布リポの placeholder 方針を破らない。構成は Task dispatch のみ。
- **実装そのもの (設計 / コーディング)**: 上流 `design` / `software-design` / `tdd` / `tidy-first` の領域。本スキルは未 GREEN を受けない。
- **PR の merge / squash / branch 削除**: 最終状態は merge-ready で停止、merge は人間に残す。
- **ローカル trace ファイル**: トレースは PR コメント側 (pr-review-respond 集約) に集約。リポ内ログは作らない。
- **再利用 subagent の出力**: サイクルごとに fresh dispatch。読了済み subagent の再利用出力はない。
- **escalate を越えた追加 dispatch / push**: HALTED / stall 後はユーザ明示指示が無い限り出さない。

---

## 既知の限界

- **subagent の context 制限**: 大規模 diff / 長い CI ログは 1 subagent で読み切れない場合がある。各フェーズ skill 側の分割運用に従う。本スキルは 1 フェーズ 1 dispatch を前提。
- **逐次コスト**: 1 サイクル内 ci-self-heal → pr-review-respond を逐次に回すため並列より遅い。shared-branch の正しさを優先した意図的選択。
- **収束は「新規 commit が出なくなった」で判定**: nitpick 無限ループは「triage 済み nitpick は未終端 0 算入」の定義で吸収する (特例ルールを置かない)。重要リリースで追加往復を望む場合はユーザが明示指示できる。
- **Copilot vendor 判定**: `pr-review-respond` の fetcher は Copilot を安全側に倒し `human` 扱いにする。本スキルは契約入力で Copilot を triage 対象に必ず含めるよう明示する。VALID / VALID_DEFER / DUPLICATE は他 vendor 同様 GraphQL `resolveReviewThread` で resolve され、resolve されず残るのは INVALID_PUSH (返信のみ) と Withdrawn (レビュアー撤回)。
- **single PR 前提**: 1 セッション 1 PR。複数 PR 並走の出荷は想定しない。
- **マルチモデル未検証**: trigger eval は本セッションのモデルのみ。Haiku / Sonnet での発火は未確認。
