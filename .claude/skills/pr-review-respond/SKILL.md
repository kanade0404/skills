---
name: pr-review-respond
description: >-
  Triages and answers review comments already posted on a PR by CodeRabbit,
  Devin, or human reviewers: verifies each finding, lands fix commits with
  「Fixed in <SHA>」 replies, leaves reasoned pushback unresolved where a finding
  is wrong, defers the rest to linked issues, and closes with one aggregate
  summary comment on the PR. Use right after `gh pr create`, **right after
  pushing to an existing PR branch (including a re-push made while responding to
  review)**, when new reviewer comments appear, before merging with threads
  still open, and for 「レビュー対応して」「コメント見て対応して」「コードラビット対応」「Devin の指摘片付けて」「PR
  のコメント全部捌いて」「push したのでスレッド対応して」. Never leave a PR with open threads without
  running it once. Not for: running a review or pre-PR code review
  (`code-review`), fixing CI (`ci-self-heal`), or writing tests. CodeRabbit
  fixes delegate to `coderabbit:autofix`.
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash(bash *prr *)
  - Bash(git add *)
  - Bash(git commit *)
  - Bash(git diff *)
  - Bash(git log *)
  - Bash(git push *)
  - Bash(git rev-parse *)
  - Bash(git status *)
  - Bash(jq *)
  - Task
---
# PR Review Respond

CodeRabbit / Devin / 人間レビュアーが残したコメントを **盲信せず verify したうえで** 捌くスキル。完了時には PR を読み返した第三者が「何を直し、何を直さず、なぜか」を 1 コメントで追える状態にする。

設計の柱は 3 つ：

- **verify-before-implement** — 妥当性を AI が一次判定してから手を動かす。`receiving-code-review` 系の規律を取り込む。
- **pushback はコメントのみ** — INVALID と判定したものは根拠を書くだけで resolve しない。reviewer の最終判断余地を残す。
- **トレーサビリティは PR 集約コメント 1 本に集約** — ローカルログは作らない。後から PR を見れば全てわかる状態にする。

---

## 実行環境前提

本スキルは 3 つの実行環境で起動しうる。**待機や中断の扱いが環境ごとに違う**ため、起動時にどの環境かを意識する:

| 環境 | 特徴 | 待機や行き詰まりの扱い |
|---|---|---|
| 対話ローカルセッション | 画面前に人間がいる | Phase E の `WAITING` verdict をそのまま人間に返してよい |
| ヘッドレス subagent (`shipping` 等からの dispatch) | 呼び出し元 skill/agent がいる | `WAITING` verdict を呼び出し元に返す。呼び出し元が再開の責任を持つ |
| CI / スケジュール起動 (無人実行) | `WAITING` を受け取る相手がいない | **`WAITING` で止めない**。`needs-human` ラベル付与 + 構造化コメント (`prr escalate`、後述) を必須のフォールバックとする |

いずれの環境でも共通の規律は Phase E で扱う「待機委譲時の end_turn 禁止」。

---

## 前提: 同梱スクリプトと権限

`gh api` / `gh pr ...` を毎回 inline で叩くと、実行のたびに permission prompt が発生して煩雑になる。本スキルは GitHub API 呼び出しを `scripts/` 配下に閉じ込め、**単一エントリーポイント `prr` 経由でのみ呼び出す** 設計にしている。これにより:

- `allowed-tools` の rule は `Bash(bash *prr *)` 1 行で全アクションをカバー (末尾 `*` のみで Claude Code permission engine の保証範囲内)
- consumer の `~/.claude/settings.json` への permission 追加は不要 (`allowed-tools` が auto-grant、workspace trust 受諾後に有効化)

### scripts/

```text
scripts/
├── prr                  # entry point (subcommand dispatcher)
├── fetch_threads.sh     # prr fetch
├── reply_thread.sh      # prr reply
├── resolve_thread.sh    # prr resolve
├── post_summary.sh      # prr summary
├── wait_ci.sh           # prr wait-ci
├── defer_issue.sh       # prr defer
└── escalate.sh          # prr escalate
```

### Subcommand 一覧

すべて `bash "${CLAUDE_SKILL_DIR}/scripts/prr" <subcommand> <args>` で呼び出す:

| Subcommand | 役割 |
|---|---|
| `prr fetch <PR>` | 全 review thread + PR 一般コメントを GraphQL + REST で取得し、vendor 判定 (`coderabbit` / `devin` / `human`)・`self_replied` フラグ・`last_self_reply` (自分の最終返信本文、無ければ null) を付けた正規化 JSON を stdout に出力 |
| `prr reply <PR> <comment-id> <body-file>` | 正しい `/repos/{O}/{R}/pulls/{PR}/comments/{id}/replies` エンドポイントで返信投稿。本文は file 経由で multi-line / 引用符事故を防ぐ |
| `prr resolve <PR> <comment-id> <classification> <vendor> [body-file]` | vendor (`coderabbit`/`devin`/`human`、**必須・省略不可**) を明示したうえで、GraphQL `resolveReviewThread` mutation で**対象スレッドだけ**を直接 resolve し、**resolve 成功 (`isResolved == true`) を確認してから** body-file の返信本文を投稿する (mutation 失敗時に「Fixed in …」等の成功を示す返信だけが残る事故を防ぐための順序)。**`@coderabbitai resolve` はどの vendor にも併記しない** — CodeRabbit 側で PR 全スレッドの一括 resolve として作用し、INVALID_PUSH スレッドまで巻き込む (実測: PR #96)。`classification` は `VALID` / `VALID_DEFER` / `DUPLICATE` のみ許可。**`INVALID_PUSH` を渡すと非ゼロ exit で拒否する** (誤 resolve ガード、後述)。vendor を省略・誤指定すると usage を表示して非ゼロ exit で拒否する |
| `prr summary <PR> <body-file>` | 集約 Review Response Summary を **新規** issue comment として投稿 (毎回新規投稿、過去サマリは履歴として残す) |
| `prr wait-ci <PR> [interval]` | `gh pr checks --watch` をラップし全 check 完了まで block。失敗時は exit 非ゼロで呼出側に通知 (本スキルは retry しない) |
| `prr defer <PR> <thread-url> <title> <body-file>` | `VALID_DEFER` 判定のフォロー issue を作成し、`<issue-number> <issue-url>` を stdout に出力。本文に元スレッド URL と PR URL を自動付記する |
| `prr escalate <PR> <reason> <body-file>` | 無人実行で `WAITING` を返す相手がいない時のフォールバック。PR に `needs-human` ラベルを付け、`body-file` を構造化コメントとして投稿する |

スクリプト本体は最小依存 (`gh`, `jq`, `bash`) のみ前提。Python / Node 等は使わない。

---

## ワークフロー

### Phase A — 取得 (fetch)

inline review threads + PR 一般コメントを 1 コマンドで取得・正規化する。`gh api` は `prr` wrapper 経由で呼び出して毎回の許可確認を不要にする。

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/prr" fetch <PR>   # → 正規化 JSON を stdout
```

スクリプトは:

- GraphQL の `reviewThreads` を cursor pagination で全取得
- `pulls/<PR>/comments` 相当の inline thread を root + 履歴付きで返す
- `issues/<PR>/comments` 相当の PR 一般コメントも同梱 (CodeRabbit のサマリ・walkthrough や Devin のレビュー総評)
- 各 root comment に `vendor` フィールドを付与 (`coderabbit` / `devin` / `human`、author login と本文から判定、bot suffix のような表面ルールは持たない)
- `self_replied` フラグで「自分が既に返信済みのスレッド」を識別し、`last_self_reply` でその返信の終端形 (どの分類として終端させたか) を復元可能にする

呼出側 (本スキル本体) は得られた JSON から:

- `is_resolved == true` / `is_outdated == true` を除外
- 自分が投稿した集約サマリ (`Review Response Summary` ヘッダ) を `issue_comments` から除外
- `self_replied == true` のスレッドはスキップ (多重返信防止)。**ただしスキップ前に resolve 取りこぼしを修復する**: `self_replied == true` かつ未 resolve で、`last_self_reply` が resolve 前提の終端形 (「Fixed in」/「Tracked in #」/「Already addressed by」) の場合、それは過去 run で返信投稿後に `resolveReviewThread` mutation だけが失敗した残骸 — `self_replied` 単独では意図的残置 (Pushback / Withdrawn) と区別できないため、この判別は必ず `last_self_reply` の終端形で行う。body-file 無しの `prr resolve <PR> <id> <分類> <vendor>` (返信を重複させず resolve のみ) を再試行して終端させる (分類は終端形から復元: Fixed → `VALID` / Tracked → `VALID_DEFER` / Already addressed → `DUPLICATE`)。意図的残置としてスキップしてよいのは `last_self_reply` が Pushback 終端形 (⏳ 定型行) または Withdrawn 終端形の場合のみ

### Phase B — 妥当性 verify (triage)

各 thread / コメントを **4 値分類** する。判定は description ではなく **指摘本文 + 該当コード** を読んで行う。レビュアー名で重み付けしない。なお**撤回検出** (CodeRabbit の `<review_comment_withdrawn>` 等、レビュアー自身による指摘の撤回) は 4 値 triage の対象外で、第 5 の終端 **Withdrawn** に直行する (扱いは Phase D「レビュアーが指摘を撤回した場合」を参照。分類表は 4 値のまま)。

| 分類 | 定義 |
|---|---|
| `VALID` | 指摘通り、本 PR スコープ内で修正する |
| `INVALID_PUSH` | 技術的に不適切 / 既存方針と矛盾 / YAGNI / 文脈不足。根拠を返してそのまま残す |
| `VALID_DEFER` | 妥当だが本 PR スコープ外。issue を切って参照する |
| `DUPLICATE` | 同 PR 内の他スレッドで既に対応済み |

判定の際の禁則：

- **performative agreement 禁止**。`"You're absolutely right!"` / 「おっしゃる通り」式の同意のみで実装に進まない。**指摘内容を自分の言葉で要約できないなら VALID と判定しない**。
- **レビュアー権威での自動 VALID 化禁止**。CodeRabbit / Devin / Senior 人間のいずれであっても、根拠が薄ければ INVALID_PUSH を恐れない。
- **逆も禁止**。AI レビューだから INVALID と決め打ちしない。

`INVALID_PUSH` の正当化は次のいずれかに該当することを 1 文で書けること：

- YAGNI（指摘の抽象化に必要な呼出元が現状 1 箇所しかない、等）
- 既存方針との矛盾（プロジェクト規約 / 他コンポーネントの先例と整合しない）
- 指摘の前提が誤り（コード読み違え、context window の境界で見えていない情報がある）
- トレードオフの選択（パフォーマンス vs 可読性、等の意識的な選択）

### 人間フィードバックの取り込み

メンテナが agent 起票の PR/issue に「期待していたこと・その理由」を書いたコメントを
残した場合、`agent-feedback` ラベルはそれを週次実行の `skill-improver` 向けにマークする
だけの合図。本スキル自身はそのラベルを見てもセッション内で対応しない (対応は
`skill-improver` の責務)。

### Phase C — 修正 (apply)

`VALID` のみ対象。

**CodeRabbit 起因の指摘は `coderabbit:autofix` への委譲を許容する**: CodeRabbit が投稿した `VALID` 判定の指摘について、coderabbit plugin が導入されている環境では、修正適用そのものを per-change approval 付きで安全に適用する専用スキル `coderabbit:autofix` に委譲してよい。委譲した場合でも、triage (Phase B)・返信 / resolve (Phase D)・集約サマリ (Phase E) を本スキルが持つ分担は変わらない。plugin が無い環境では従来どおり本 Phase の経路 (structural → `tidy-first` / behavioral → `tdd`) で修正する。commit への `Refs:` 付与と Phase C 終端の push 規律 (後述) は、委譲した場合も適用される。

- **structural change** (純リファクタ・rename・抽出) は **behavioral change と commit を分ける**。`tidy-first` の規律を踏む。
- **behavioral change** は失敗テストを先に書く（`test-driven-development` の規律）。
- 各 commit message に該当スレッドの URL を `Refs:` で付ける：

```text
fix: handle empty result in foo()

Refs: https://github.com/<owner>/<repo>/pull/<n>#discussion_r<id>
```

これにより返信時に `<SHA>` を貼ればトレースが完結する。

- 修正が **既存テストの assertion / 期待値そのものを書き換える**場合 (新規テスト追加ではなく、緩い・誤った assertion の訂正)、Phase D で「Fixed in `<SHA>`」を返信する **前に** `test-mutation-gate` を必ず通す。レビュー起点のテスト修正が本当に検出力を持つかを機械的に裏取りするため。BLOCK なら修正をやり直し、返信しない。

**Devin の re-review は commit push に任せる**。`@devin` メンションでの再依頼はしない（push を検知して自動再評価するため）。

### Phase C 終端 — push (省略禁止)

commit を当てただけでは GitHub 上の PR は古い HEAD のままで、CI もレビュー bot もそれを見ている。**Phase D / E に進む前に必ず push する**:

```bash
git push origin <branch>
git rev-parse HEAD   # push した SHA を記録し、以降の "Fixed in <SHA>" 返信に使う
```

- push を省略すると `prr wait-ci` が古い HEAD の CI 結果を見て「完了」と誤判定する。Devin の自動 re-review も push が trigger のため起動しない。
- push が rejected / diverged で失敗したら、原因 (force-push 済みの remote など) を解消してから再 push する。**push が成功したことを確認した SHA でのみ** Phase D 以降に進む。
- 複数 commit をまとめて 1 回だけ push してよい。commit ごとの push は必須ではない。

### VALID_DEFER — フォロー issue 作成

`VALID_DEFER` は「妥当だがスコープ外」の判定であり、返信 (Phase D) で `Tracked in #<issue>` と書く以上、その issue は **返信より前に実在していなければならない**。

```bash
# body-file には指摘の要約 (自分の言葉で) + スコープ外と判断した理由を書く
bash "${CLAUDE_SKILL_DIR}/scripts/prr" defer <PR> <thread-url> "<title>" <body-file>
# stdout: "<issue-number> <issue-url>"
```

- **タイトル規約**: 指摘内容を要約した命令形 1 行 (例: `Extract retry policy into shared helper`)。skill 名等のプレフィックスは付けない。
- **本文必須項目**: 指摘の要約、スコープ外と判断した理由 (1 文)。元スレッド URL と PR URL は `prr defer` が自動で付記する。
- 生成された issue 番号を Phase D の返信 (`Tracked in #<issue>`) と Phase E のサマリ (`[<thread-url>] → #<issue>`) の両方に使う。

### Phase D — 返信 (reply)

inline thread への返信は GitHub REST の `/replies` エンドポイントを使う必要がある (top-level review comment への返信のみ可、reply-to-reply は不可)。これも `prr` wrapper に閉じ込める。

```bash
# 返信本文は file 経由 (multi-line / 引用符のエスケープ事故防止)
bash "${CLAUDE_SKILL_DIR}/scripts/prr" reply <PR> <root-comment-id> <body-file>

# 対応済みスレッドを resolve する場合 (VALID / VALID_DEFER / DUPLICATE のみ)。
# vendor は coderabbit/devin/human から必須指定 (4 番目の引数。省略・誤指定は
# usage 表示 + 非ゼロ exit で拒否 — 暗黙デフォルトは廃止)
bash "${CLAUDE_SKILL_DIR}/scripts/prr" resolve <PR> <root-comment-id> <classification> <vendor> [body-file]
# classification は VALID / VALID_DEFER / DUPLICATE のいずれか。
# INVALID_PUSH を渡すとスクリプトが非ゼロ exit で拒否する (誤 resolve ガード)。
# 全 vendor: GraphQL mutation で resolve し、成功 (isResolved == true) を
#   確認してから body-file 内容を返信投稿する (順序は resolve が先 —
#   mutation 失敗時に成功を示す返信を残さないため)。
#   body-file を省略した場合は返信を送らず resolve のみ行う。
#   body-file を明示指定したのに内容が空 (空白のみ) の場合は
#   呼び出し側のミスとして exit 2 で拒否する (黙って返信を落とさない)。
# resolve は GraphQL resolveReviewThread mutation で対象スレッドだけを直接 resolve する。
# `@coderabbitai resolve` は出さない (PR 全スレッド一括 resolve として作用するため)。
```

vendor 別の使い分け:

| 分類 | CodeRabbit | Devin | 人間 |
|---|---|---|---|
| `VALID` | `prr resolve` vendor=coderabbit (body: 「Fixed in `<SHA>`」) | `prr resolve` vendor=devin (body: 「Fixed in `<SHA>`」) | `prr resolve` vendor=human (body: 「Fixed in `<SHA>`. Ready for re-review.」) |
| `INVALID_PUSH` | `prr reply` (根拠 + 末尾に定型行 ⏳、resolve しない) | `prr reply` (根拠 + 末尾に定型行 ⏳) | `prr reply` (根拠 + 質問形式 + 末尾に定型行 ⏳) |
| `VALID_DEFER` | `prr resolve` vendor=coderabbit (body: 「Tracked in #`<issue>`」) | `prr resolve` vendor=devin (body: 「Tracked in #`<issue>`」) | `prr resolve` vendor=human (body: 「Tracked in #`<issue>`」) |
| `DUPLICATE` | `prr resolve` vendor=coderabbit (body: 「Already addressed by `<other-thread-url>`」) | `prr resolve` vendor=devin (body: Already addressed by ...) | `prr resolve` vendor=human (同左) |
| Withdrawn (triage 対象外の終端) | `prr reply` (body: 撤回確認の終端返信。resolve しない) | `prr reply` (同左) | `prr reply` (同左) |

対応済み (修正 commit 済み / issue 化済み / 重複参照済み) のスレッドは vendor を問わず resolve し、PR の未解決スレッド数を実態に一致させる。これは `pr-monitor` の `prm` が持つ `unresolved_count` (`isResolved == false` の全スレッド数) が収束判定の前提にしている値そのものであり、CodeRabbit 以外のスレッドを resolve せず放置すると、対応済みでも `unresolved_count` が減らず収束ループが成立しない。

**重要**: `INVALID_PUSH` は **どのレビュアーに対しても resolve コマンドを発行しない** (`prr reply` のみ使用)。reviewer 側に「無視された」と取られる余地を消すため。この規律は運用 (書き手の注意) だけに頼らず、`resolve_thread.sh` 自身が `classification` 引数に `INVALID_PUSH` を渡された時点で非ゼロ exit するガードとして実装されている。

未 resolve のまま残す以上、**意図が UI から読めること**が必須 — GitHub の unresolved カウンタは「未対応」と「意図的な残し」を区別せず、maintainer が放置と誤認して問い合わせ・強制 resolve に至る (実測: PR #96)。そのため:

- pushback 返信の**末尾に定型行を必ず含める**: `⏳ maintainer 判断待ち — 規律により self-resolve しません`
- Phase E の集約サマリ**冒頭**に「未 resolve n 件 (意図的な残し: 自返信済み Pushback / Withdrawn — 過去 run の残置を含む。outdated 未解決は含まず別掲)」を明記する (n の定義は Phase E 最終 gate を参照)

**レビュアーが指摘を撤回した場合** (CodeRabbit の `<review_comment_withdrawn>` 等): スレッドの resolve 操作は**しない** (勝手に閉じない — 撤回の事実確認は maintainer に残す)。ただし終端分類上は **resolve 相当 (争点消滅) として扱い**、未終端カウントに数えず、集約サマリに「撤回により終端 (スレッドは未 resolve のまま)」と記載する。加えて、`prr reply` で撤回確認の終端返信を**必ず投稿する** (例: 「本指摘はレビュアーにより撤回されたため終端とします (スレッドは規律により未 resolve のまま)」)。この自返信が Phase E gate (ii') の `self_replied == true` 要件を満たし、次 run の Phase A で自動的に除外される (返信を省くと gate が恒久不通過になり、毎 run 同スレッドを再処理し続ける)。監視側 (`pr-monitor`) は、そのコメントを一度 `known_comment_ids` に claim した後は再 dispatch しない (新設監視の初回 poll では claim 前のため 1 回 dispatch されうる — その場合も本スキルが撤回済みと判定して終端するだけで、実害は冗長 dispatch 1 回に留まり軽微)。

返信本文の最低構成 (INVALID_PUSH の例):

```text
本指摘は採用しません。理由: <YAGNI / 既存方針 / 前提誤り / トレードオフ のいずれか> — <1-2 文で具体>。
再考の余地があればコメントで詳細を教えてください。

⏳ maintainer 判断待ち — 規律により self-resolve しません
```

### Phase E — 集約サマリ投稿 + 最終 gate

PR の **issue comment** として、以下のサマリを **新規 1 件** で投稿する (既存サマリの更新ではなく毎回新規投稿、古いサマリは残して履歴にする)。投稿は `prr` wrapper 経由:

```bash
# サマリ本文を temp file に書き出してから投稿
bash "${CLAUDE_SKILL_DIR}/scripts/prr" summary <PR> <body-file>
```

サマリ本文テンプレ (`<body-file>` の中身):

```markdown
## Review Response Summary (<YYYY-MM-DD HH:MM JST>)

未 resolve <n> 件 (意図的な残し: 自返信済み Pushback / Withdrawn — 過去 run の残置を含む。outdated 未解決は含まず別掲。リンクは下記各セクション)

| Reviewer | Total | Fixed | Pushback | Deferred | Duplicate | Withdrawn |
|---|---|---|---|---|---|---|
| CodeRabbit | 8 | 5 | 2 | 1 | 0 | 0 |
| Devin | 3 | 2 | 1 | 0 | 0 | 0 |
| @<login> | 1 | 0 | 0 | 0 | 1 | 0 |

### Pushback (要 reviewer 判断 — 過去 run の意図的残置もここに列挙し、その旨を付記)
- [<thread-url>] <1 行サマリ>: <根拠 1 行>
- [<thread-url>] <1 行サマリ>: <根拠 1 行> (過去 run の残置)

### Deferred
- [<thread-url>] → #<issue>

### Fixed (commit)
- [<thread-url>] → `<SHA>`

### Withdrawn (該当時のみ)
- [<thread-url>] 撤回により終端 (スレッドは未 resolve のまま)

### Outdated (該当時のみ・別掲 — 冒頭 n には含めない)
- [<thread-url>] outdated のため処理対象外 (未 resolve のまま残置)

各スレッドへの返信は thread 内に投稿済み。
```

最終 gate — **(i) の件数一致 + gate 時点の集合検査** で確認する：

- **(i) 分類の完全性 — Phase A fetch 時点**: Phase A で fetch した処理対象スレッド数 (resolved / outdated / self_replied 除外後) = サマリの Fixed + Pushback + Deferred + Duplicate + Withdrawn の和。処理漏れをここで検出する。
- **(ii') 各分類スレッドの終端状態 — gate 時点**: gate 時点で `prr fetch` を再実行し、本 run で分類した各スレッドの終端状態を **1 件ずつ個別に** 確認する:
  - Fixed / Deferred / Duplicate → resolve 済み (`is_resolved == true`) であること
  - Pushback / Withdrawn → 未 resolve (`is_resolved == false`) かつ自返信済み (`self_replied == true`) であること。ただし maintainer / レビュアー側で既に resolve されていた場合は終端として許容し (unresolve はしない)、サマリ冒頭 n から除外して「第三者 resolve 済み」として別掲する
  - 不一致があれば、そのスレッドを **URL で名指しして Phase D に差し戻す** (gate 不通過)
- **(iii') 分類外の未解決スレッド — gate 時点**: 同じ再 fetch で「本 run の分類に無い、未解決かつ非 outdated のスレッド」が残っていた場合。`self_replied` 単独で意図的残置と判定してはならない (返信成功後に resolve mutation だけが失敗したスレッドも `self_replied == true` になる) — 必ず `last_self_reply` の終端形まで見る:
  - `self_replied == true` かつ `last_self_reply` が Pushback 終端形 (⏳ 定型行) または Withdrawn 終端形 → 過去 run の意図的残置。許容し、サマリ冒頭の n に**含めて**列挙する
  - `self_replied == true` だが `last_self_reply` が resolve 前提の終端形 (「Fixed in」/「Tracked in #」/「Already addressed by」) → resolve 取りこぼし。body-file 無しの `prr resolve` (返信なし・resolve のみ) を再試行して終端させる (gate 不通過、再試行後に再 fetch で確認)
  - `self_replied == false` → 新規コメントまたは本 run の取りこぼし。**Phase A に再入して処理する** (gate 不通過)
- **サマリ冒頭 n の定義**: gate 時点で未解決かつ非 outdated のスレッドのうち、意図的な残置 (自返信済みで、かつ `last_self_reply` が Pushback / Withdrawn の終端形) の総数。本 run の Pushback / Withdrawn も gate 時点では自返信済みのため、「gate 時点の `self_replied == true` かつ終端形が Pushback / Withdrawn である未解決・非 outdated 数」がそのまま n になる (本 run 分 + 過去 run の残置分。同一スレッドを二重計上しない)。gate の合格条件は「gate 時点で未解決のうち、意図的な残置が n 件、それ以外 (`self_replied == false`、および resolve 取りこぼし) が 0 件であること」。

> 補足: 旧 gate の件数一致式「gate 時点の未解決・非 outdated 数 = 本 run の Pushback + Withdrawn」は**廃止**。過去 run の pushback 残置は Phase A で self_replied として除外され本 run の分類に入らないため、run を跨ぐとこの等式は恒久的に不成立になる。
- ローカル検証は **`verify-done` を呼んで** PASS を取る (`should/probably/seems` 系の語彙はそこで弾かれる)
- CI 完了待ちも `prr` 経由:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/prr" wait-ci <PR>   # 全 check 完了まで block、fail なら ci-self-heal に渡す
```

### 待機委譲時の規律 (end_turn 禁止)

`wait-ci` はブロッキング呼び出しだが、長時間 CI を `Monitor` 等のバックグラウンド監視に委譲したくなる場面がある。**委譲した直後に end_turn してはならない** — 誰も再開しないまま放置される実例が起きている (待機委譲後 50 分放置)。

- 同一ターン内で `wait-ci` (または委譲した監視) の完了 (pass/fail) まで確認できるなら、そのまま最終報告に進む。
- 同一ターン内で完結できない場合、最終報告の代わりに **`WAITING` verdict を明示的に返す**:
  - 現在までの進捗 (Fixed / Pushback / Deferred / Duplicate / Withdrawn の内訳)
  - 何を待っているか (CI の残り check / 追加レビュー等)
  - 再開条件 (checks 完了、新規コメント等) と再開方法 (呼び出し元がポーリングするか、`pr-monitor` 等に引き継ぐか)
  - `WAITING` を返したターンで end_turn してよいのは、「実行環境前提」表の対話ローカル / ヘッドレス subagent のように **`WAITING` を受け取る相手が存在する場合のみ**
- 受け取る相手がいない (CI / スケジュール起動の無人実行) 場合は `WAITING` で止めない。代わりに次を実行してから終える:

```bash
# body-file は loop-escalation:v1 形式 (issue-driven-development skill と共通の規約):
# 自由文の状況説明 + <!-- loop-escalation:v1 --> に続く JSON
#   {"reason": "...", "detail": "...", "attempts": <n>, "session_id": "...", "next_action_hint": "..."}
# reason は budget-exceeded / max-turns / ci-3-fail / review-5-rounds / no-progress /
# ambiguous-issue / repo-unresolvable / conflict / security-block / other から選ぶ
bash "${CLAUDE_SKILL_DIR}/scripts/prr" escalate <PR> <reason> <body-file>
```

---

## 出力フォーマット

ユーザへの最終報告は以下の構造で 1 メッセージ：

```markdown
# PR Review Response: #<n>

## Stats
- Threads processed: <total>
- Fixed: <n>  / Pushback: <n>  / Deferred: <n>  / Duplicate: <n>  / Withdrawn: <n>

## Commits
- `<SHA>` <message>
- ...

## Pushback (理由)
- [<thread-url>] <分類根拠 1 行>

## CI
- <pass/fail/pending> (<URL>)

## Summary comment posted
<URL>
```

---

## レビュアー判別

`fetch_threads.sh` が `vendor` フィールドを 1 次判定として返す (author login が `coderabbit*` で始まるなら `coderabbit`、`devin*` または `devin-ai-*` を含むなら `devin`、それ以外は `human`)。

本スキルは script 結果を起点に、本文構造でさらに補正する:

- 本文構造が CodeRabbit walkthrough / nitpick markup を含む → `coderabbit` で固定
- 本文に Devin 特有のシグネチャ / Confidence 表記 → `devin` で固定
- それ以外で script の判定が曖昧な場合 → **人間として扱う** (安全側デフォルト。resolve 自体は vendor によらず GraphQL mutation で行い、ディレクティブはどの vendor にも出さないため、この判定が影響するのは返信の文言だけ)

PR 作者本人 (= 自分) のコメントは fetcher 側ではフィルタしない。本スキルが「自分のコメント」「自分の集約サマリ」を識別して捌く。

---

## 出力する成果物 / 出力しない成果物

### 出力する成果物

- **集約サマリコメント 1 件** (`prr summary` 経由で PR の issue comment として投稿、毎回新規、過去サマリは履歴として残す)
- **inline thread への返信文字列** (`prr reply` / `prr resolve` 経由、vendor 別フォーマット)
- **修正コミット列 + push** (commit message に `Refs: <thread-url>` を含み、Phase C 終端で push 済み)
- **フォロー issue** (`VALID_DEFER` 判定時のみ、`prr defer` 経由で作成)
- **ユーザ向け最終報告** (Stats / Commits / Pushback / CI / Summary URL の固定構造、または `WAITING` verdict)
- **`needs-human` ラベル + エスカレーションコメント** (無人実行で `WAITING` の受け手がいない場合のみ、`prr escalate` 経由)

### 出力しない成果物

- **新規レビュー実行結果**: CodeRabbit / Devin 自身を起動した出力は出さない (既存コメントへの後追い専用)。
- **ローカルログファイル**: `pr-review-response.md` 等のリポ内ファイルは作らない (トレースは PR 集約コメント 1 本のみ)。
- **構造変更を含む commit / テストコード**: それらは `tidy-first` / `tdd` 経由の出力で、本スキル内では呼び出しのみ。
- **`@devin` 再レビュー mention 文字列**: commit push を契機にした自動再評価に任せる。
- **`@coderabbitai resolve` ディレクティブ**: どの分類・どの vendor でも出さない (PR 全スレッド一括 resolve として作用するため。対象スレッドの resolve は GraphQL mutation が担う)。第 1 防御は**スクリプト自身の body ガード** — `prr reply` / `prr resolve` / `prr summary` は body にこのディレクティブが含まれていたら API 呼び出し前に非ゼロ exit で拒否する (配布に同梱され、consumer 環境でも機能する)。ディレクティブ文字列を本文で言及したい場合は言い換える (例: 「CodeRabbit の一括 resolve ディレクティブ」と書く)。第 2 防御が本 repo の hooks-local の deny hook (consumer には配布されない)。
- **既処理 thread への 2 度目の返信**: 自分が返信済みの thread には何も投稿しない。
- **既存集約サマリの編集差分**: サマリ更新は edit ではなく新規 issue comment として出す。

---

## 既知の限界

- **Devin protocol の表面追跡が必要**: Devin の出力フォーマットは更新される。本文判定の文字列マッチが滑ったら「人間扱い」に倒れるが、resolve 誤発行の害より対応漏れの害が小さいので意図通り。
- **GraphQL `reviewThreads.isResolved` への依存**: REST だけでは resolve 判定が取れないため GraphQL 併用。`gh` 認証スコープに graphql 必須。
- **`resolveReviewThread` mutation は書き込み権限が必要**: 読み取り専用の `gh` 認証や外部フォークからの実行では失敗する。自分の PR / write 権限のあるリポジトリで動かす前提。
- **`gh pr checks --watch` の長時間ブロック**: 大規模 CI で 30 分超を想定。バックグラウンド実行 + 通知に切り替える運用余地あり。
- **multi-PR 並走の分離**: 1 セッション内で複数 PR を同時に捌く運用は想定していない。PR ごとに 1 セッション。
