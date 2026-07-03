---
name: pr-review-respond
description: >-
  PR に投稿された自動レビュー (CodeRabbit / Devin)
  と人間レビュアーのコメントを取得し、各指摘の妥当性を検証したうえで対応するスキル。VALID は修正コミットを当てて該当スレッドに「Fixed in
  <SHA>」と返信、INVALID_PUSH は根拠付きの pushback コメントを残し resolve しない、VALID_DEFER は issue
  化して参照、DUPLICATE は既存対応スレッドを指す。最後に PR へ集約サマリコメントを 1 件投稿し「何を・どう対応した／なぜ対応しなかったか」を
  1 箇所で追えるようにする。`gh pr create` 直後・「レビュー対応して」「コメント見て対応して」「コードラビット対応」「Devin
  の指摘片付けて」「PR のコメント全部捌いて」のような要請、CodeRabbit / Devin / 人間レビュアーが新規コメントを残した時、PR を
  merge する前に未解決スレッドを確認したい時、いずれでも必ず起動すること。レビュアー判別はコメント author と本文を読んで行い、bot
  suffix のような表面的なルールは持たない。本スキルは「読む・直す・返信する・サマリ投稿する」までで、レビュー自体を実行する (CodeRabbit や
  Devin を呼び出す) ことはしない — 既にレビュー済みの PR に後追いで対応するスキル。GitHub API 呼び出しは同梱の単一エントリ
  `scripts/prr` (subcommand: `fetch` / `reply` / `resolve` / `summary` /
  `wait-ci`) に集約しており、`allowed-tools` で `Bash(bash *prr *)` を auto-grant するため
  consumer 側で permission を追加する必要は無い。
---
# PR Review Respond

CodeRabbit / Devin / 人間レビュアーが残したコメントを **盲信せず verify したうえで** 捌くスキル。完了時には PR を読み返した第三者が「何を直し、何を直さず、なぜか」を 1 コメントで追える状態にする。

設計の柱は 3 つ：

- **verify-before-implement** — 妥当性を AI が一次判定してから手を動かす。`receiving-code-review` 系の規律を取り込む。
- **pushback はコメントのみ** — INVALID と判定したものは根拠を書くだけで resolve しない。reviewer の最終判断余地を残す。
- **トレーサビリティは PR 集約コメント 1 本に集約** — ローカルログは作らない。後から PR を見れば全てわかる状態にする。

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
└── wait_ci.sh           # prr wait-ci
```

### Subcommand 一覧

すべて `bash "${CLAUDE_SKILL_DIR}/scripts/prr" <subcommand> <args>` で呼び出す:

| Subcommand | 役割 |
|---|---|
| `prr fetch <PR>` | 全 review thread + PR 一般コメントを GraphQL + REST で取得し、vendor 判定 (`coderabbit` / `devin` / `human`) と `self_replied` フラグを付けた正規化 JSON を stdout に出力 |
| `prr reply <PR> <comment-id> <body-file>` | 正しい `/repos/{O}/{R}/pulls/{PR}/comments/{id}/replies` エンドポイントで返信投稿。本文は file 経由で multi-line / 引用符事故を防ぐ |
| `prr resolve <PR> <comment-id> [body-file]` | `body + @coderabbitai resolve` を投稿し thread を resolve。VALID / VALID_DEFER / DUPLICATE 専用 (INVALID_PUSH では呼ばない) |
| `prr summary <PR> <body-file>` | 集約 Review Response Summary を **新規** issue comment として投稿 (毎回新規投稿、過去サマリは履歴として残す) |
| `prr wait-ci <PR> [interval]` | `gh pr checks --watch` をラップし全 check 完了まで block。失敗時は exit 非ゼロで呼出側に通知 (本スキルは retry しない) |

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
- `self_replied` フラグで「自分が既に返信済みのスレッド」を識別

呼出側 (本スキル本体) は得られた JSON から:

- `is_resolved == true` / `is_outdated == true` を除外
- 自分が投稿した集約サマリ (`Review Response Summary` ヘッダ) を `issue_comments` から除外
- `self_replied == true` のスレッドはスキップ (多重返信防止)

### Phase B — 妥当性 verify (triage)

各 thread / コメントを **4 値分類** する。判定は description ではなく **指摘本文 + 該当コード** を読んで行う。レビュアー名で重み付けしない。

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

### Phase C — 修正 (apply)

`VALID` のみ対象。

- **structural change** (純リファクタ・rename・抽出) は **behavioral change と commit を分ける**。`tidy-first` の規律を踏む。
- **behavioral change** は失敗テストを先に書く（`test-driven-development` の規律）。
- 各 commit message に該当スレッドの URL を `Refs:` で付ける：

```text
fix: handle empty result in foo()

Refs: https://github.com/<owner>/<repo>/pull/<n>#discussion_r<id>
```

これにより返信時に `<SHA>` を貼ればトレースが完結する。

**Devin の re-review は commit push に任せる**。`@devin` メンションでの再依頼はしない（push を検知して自動再評価するため）。

### Phase D — 返信 (reply)

inline thread への返信は GitHub REST の `/replies` エンドポイントを使う必要がある (top-level review comment への返信のみ可、reply-to-reply は不可)。これも `prr` wrapper に閉じ込める。

```bash
# 返信本文は file 経由 (multi-line / 引用符のエスケープ事故防止)
bash "${CLAUDE_SKILL_DIR}/scripts/prr" reply <PR> <root-comment-id> <body-file>

# CodeRabbit に「対応済み」を伝えて thread を resolve する場合 (VALID / VALID_DEFER / DUPLICATE のみ)
bash "${CLAUDE_SKILL_DIR}/scripts/prr" resolve <PR> <root-comment-id> [body-file]
# body-file を渡すとその内容 + 改行 + "@coderabbitai resolve" が投稿される
```

vendor 別の使い分け:

| 分類 | CodeRabbit | Devin | 人間 |
|---|---|---|---|
| `VALID` | `prr resolve` (body: 「Fixed in `<SHA>`」) | `prr reply` (Fixed in `<SHA>`) | `prr reply` (Fixed in `<SHA>`. Ready for re-review.) |
| `INVALID_PUSH` | `prr reply` (根拠のみ、resolve しない) | `prr reply` (根拠のみ) | `prr reply` (根拠 + 質問形式) |
| `VALID_DEFER` | `prr resolve` (body: 「Tracked in #`<issue>`」) | `prr reply` (Tracked in #`<issue>`) | `prr reply` (Tracked in #`<issue>`) |
| `DUPLICATE` | `prr resolve` (body: 「Already addressed by `<other-thread-url>`」) | `prr reply` (Already addressed by ...) | 同左 |

**重要**: `INVALID_PUSH` は **どのレビュアーに対しても resolve コマンドを発行しない** (`prr reply` のみ使用)。reviewer 側に「無視された」と取られる余地を消すため。

返信本文の最低構成 (INVALID_PUSH の例):

```text
本指摘は採用しません。理由: <YAGNI / 既存方針 / 前提誤り / トレードオフ のいずれか> — <1-2 文で具体>。
再考の余地があればコメントで詳細を教えてください。
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

| Reviewer | Total | Fixed | Pushback | Deferred | Duplicate |
|---|---|---|---|---|---|
| CodeRabbit | 8 | 5 | 2 | 1 | 0 |
| Devin | 3 | 2 | 1 | 0 | 0 |
| @<login> | 1 | 0 | 0 | 0 | 1 |

### Pushback (要 reviewer 判断)
- [<thread-url>] <1 行サマリ>: <根拠 1 行>
- [<thread-url>] <1 行サマリ>: <根拠 1 行>

### Deferred
- [<thread-url>] → #<issue>

### Fixed (commit)
- [<thread-url>] → `<SHA>`

各スレッドへの返信は thread 内に投稿済み。
```

最終 gate：

- 未解決スレッド総数 - サマリの (Fixed + Pushback + Deferred + Duplicate) = 0 を確認
- ローカル検証は **`verify-done` を呼んで** PASS を取る (`should/probably/seems` 系の語彙はそこで弾かれる)
- CI 完了待ちも `prr` 経由:

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/prr" wait-ci <PR>   # 全 check 完了まで block、fail なら ci-self-heal に渡す
```

---

## 出力フォーマット

ユーザへの最終報告は以下の構造で 1 メッセージ：

```markdown
# PR Review Response: #<n>

## Stats
- Threads processed: <total>
- Fixed: <n>  / Pushback: <n>  / Deferred: <n>  / Duplicate: <n>

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
- それ以外で script の判定が曖昧な場合 → **人間として扱う** (resolve コマンドを誤って発行しないため、より安全な側に倒す)

PR 作者本人 (= 自分) のコメントは fetcher 側ではフィルタしない。本スキルが「自分のコメント」「自分の集約サマリ」を識別して捌く。

---

## 出力する成果物 / 出力しない成果物

### 出力する成果物

- **集約サマリコメント 1 件** (`prr summary` 経由で PR の issue comment として投稿、毎回新規、過去サマリは履歴として残す)
- **inline thread への返信文字列** (`prr reply` / `prr resolve` 経由、vendor 別フォーマット)
- **修正コミット列** (commit message に `Refs: <thread-url>` を含む)
- **ユーザ向け最終報告** (Stats / Commits / Pushback / CI / Summary URL の固定構造)

### 出力しない成果物

- **新規レビュー実行結果**: CodeRabbit / Devin 自身を起動した出力は出さない (既存コメントへの後追い専用)。
- **ローカルログファイル**: `pr-review-response.md` 等のリポ内ファイルは作らない (トレースは PR 集約コメント 1 本のみ)。
- **構造変更を含む commit / テストコード**: それらは `tidy-first` / `tdd` 経由の出力で、本スキル内では呼び出しのみ。
- **`@devin` 再レビュー mention 文字列**: commit push を契機にした自動再評価に任せる。
- **`@coderabbitai resolve` コマンド (INVALID_PUSH 時)**: pushback 時は本文のみ、resolve 文字列は出さない。
- **既処理 thread への 2 度目の返信**: 自分が返信済みの thread には何も投稿しない。
- **既存集約サマリの編集差分**: サマリ更新は edit ではなく新規 issue comment として出す。

---

## 既知の限界

- **Devin protocol の表面追跡が必要**: Devin の出力フォーマットは更新される。本文判定の文字列マッチが滑ったら「人間扱い」に倒れるが、resolve 誤発行の害より対応漏れの害が小さいので意図通り。
- **GraphQL `reviewThreads.isResolved` への依存**: REST だけでは resolve 判定が取れないため GraphQL 併用。`gh` 認証スコープに graphql 必須。
- **`gh pr checks --watch` の長時間ブロック**: 大規模 CI で 30 分超を想定。バックグラウンド実行 + 通知に切り替える運用余地あり。
- **multi-PR 並走の分離**: 1 セッション内で複数 PR を同時に捌く運用は想定していない。PR ごとに 1 セッション。
