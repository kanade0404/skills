# 権威状態を専用の private state repo に置く

Status: accepted (2026-08-29)

Driver: [-ilities 1 可監査性・回復可能性](0009-ility-priority-order.md) を「例外領域ゼロ」で満たし、
かつ権威の分離を repo 境界という機構で表現できること。[ADR 0007](0007-github-as-sole-durable-state.md)
の下流で、その制約 (状態は GitHub のみ) の内側にある。用語は [CONTEXT.md](../../CONTEXT.md) に従う。

## Context

[ADR 0007](0007-github-as-sole-durable-state.md) は「耐久状態は GitHub のみ」を決めたが、GitHub の
**どこ**に権威レコードを置くかは決めていない。候補は 3 つあった。

- **S1**: 実装対象の code repo 内 (ファイル / コメント / ラベル)
- **S2**: 専用の private state repo
- **S3**: git ref (`refs/pl/*` などのカスタム ref、および `refs/heads` の fast-forward 制約)

2026-08-29 に実施した PoC (pl-substrate-poc) で、以下を実測した。

- **`refs/pl/*` の non-force 更新は fast-forward 制約を持たない** (last-write-wins)。また `refs/pl/**`
  は ruleset で保護できない。カスタム ref を権威に置く案はここで反証された (詳細は
  [ADR 0012](0012-write-authority-by-lease-and-sha-cas.md))。
- **Contents API の sha-CAS は non-default branch でも同一の挙動**を示し、10 回中 10 回「ちょうど
  1 人が勝つ」。409 は文書化された契約である。
- **競合検出は branch tip 単位**である。同じ branch なら、別のファイルへの書き込みでも 409 が返る
  (実測 A7)。lane (= issue) ごとに branch を分けなければ、無関係な lane どうしが競合する。lane 別
  branch にしたうえで 150 並列 write を流し、競合ゼロを確認した。
- **GitHub の ruleset は private repo でも enforcement が実効**である (パイロットの agegis で確認)。
- **PAT では bypass actor の粒度が「その PAT を持つアカウント」**になるため、実行体と人間の書き込みを
  分離できない。権威分離を token の層で表現するには GitHub App が要る
  ([ADR 0013](0013-role-separated-tokens-and-credentials.md))。
- **rule 違反も CAS 敗北も同じ 409 / 422 を返す** (実測 H)。status code では区別できず、message 本文で
  分類するしかない。
- 読みは ETag + `If-None-Match` が使え、304 は core のレート制限を消費しない。予算の観測は実レスポンス
  の `x-ratelimit-remaining` を使う (`/rate_limit` エンドポイントの値は不正確だった)。

これらから、権威を code repo に置くと、状態の書き込みが実装対象の履歴を汚すだけでなく、CAS の失敗
分類が code repo の ruleset (人間の merge ゲートを支えている保護規則) と混ざることが分かった。

## Decision

**専用の private state repo を 1 つ置き、パイプラインの全権威レコードをそこに集約する。** 実装対象の
code repo には権威を置かない。

権威面の所在は branch と path まで一意に定める。

| レコード | 置き場 (権威) | 書き手 | code repo / issue 面 |
|---|---|---|---|
| lease (取得 / renew / release) + epoch / incarnation | `lane/<issue>` branch / **`lease.json`** | worker のみ | — |
| 遷移事実 (transition / intent) | 同 `state.json` の events 配列 | worker のみ | ラベル・コメントは派生表示 |
| runtime (`thread_id` / `container_id`) | 同 `state.json` の runtime フィールド | worker のみ | — |
| lineage cap + checkpoint | `lineage/<lineage_id>` branch / `aggregate.json` | worker のみ | — |
| global 予算 (同時 thread / 日次 dispatch) | `global` branch / `budget.json` | worker のみ | — |
| heartbeat | `worker/<id>` branch / `heartbeat.json` | worker のみ | — |
| ac-report | `lane/<issue>` branch / `ac/<PR番号>.json` | worker のみ | PR コメントは派生表示 |
| タスク / 実装 issue 本文・blocked-by | code repo (GitHub native) | Fable (App) / worker | 契約ブロックのみ指示 |

- **lease を `lease.json` として `state.json` と別ファイルにしているのは、期限判定の対象を一意にするため**である
  ([ADR 0012](0012-write-authority-by-lease-and-sha-cas.md))。同居させると lane の通常活動の commit でも
  lease の見かけの鮮度が更新されてしまう。競合検出は branch tip 単位なので、ファイルを分けても同じ lane
  への書き込み同士は排他のままである。
- **runtime を権威面に置くのは、実行体の内部状態だけが GitHub から見えない例外領域になるのを防ぐため**
  である (-ilities 1 の「例外領域ゼロ」)。
- **heartbeat が worker 別 branch にあるのは意図的**で、branch tip 単位の競合検出により、worker が
  増えても heartbeat 同士が 409 で競合しない。ファイルは CAS 上書きなのでサイズも一定に保たれる。
- **ac-report は check 完了イベントの時点で worker が CI artifact から権威面へ転記する**。artifact の
  保存期限が切れて完了判定の根拠が消えることを防ぐ。
- issue のラベルとコメントは**非権威の派生表示**である。派生の再同期は event id を派生コメントに埋めて
  冪等化する (既存なら skip)。2 repo をまたぐ部分障害の解決規則は「**state repo が正、派生は tick で
  自己修復**」。
- **在庫上限: active な lane ≤ 30**。超過したら新規 dispatch を止める (流量の上限とは別の在庫上限)。
  決着から 30 日で `archive/lane/<issue>` へ rename する。列挙は prefix 付きの refs list、読みは ETag。
- lane の `state.json` はサイズ上限 256KB。超過前に旧 events を `lane/<issue>/archive-<n>.json` へ
  退避する (append-only は維持)。

権威の**機構** — 誰がいつ書いてよいか — は本 ADR では決めない。
[ADR 0012](0012-write-authority-by-lease-and-sha-cas.md) に委ねる。

## Considered Options

- **S1: code repo 内に権威を置く** — 3 つの理由で却下。(a) 状態の書き込みが実装対象の履歴を汚す。
  (b) code repo の ruleset は人間の merge ゲートのためにあり、そこへ権威用の bypass を張ると merge
  ゲート自体が緩む。(c) ruleset 違反と CAS 敗北の status code が同じで、code repo の保護規則が増える
  ほど誤分類の余地が増える。却下。
- **S3: `refs/heads` の fast-forward 制約を権威に** — FF 制約自体は効くが、ref の DELETE が無条件なので
  release を保護できず、状態の本体を ref に載せる手段も無い。「state を伴わずにちょうど 1 人だけ通したい」
  補助用途に限定する。権威としては却下。
- **`refs/pl/*` のカスタム ref** — 実測で反証。FF 制約を持たず、ruleset で保護もできない。却下。
- **権威を issue のコメントに置く (append-only プロトコル)** — コメントは編集・削除が可能で、成長に
  上限が無く、勝者規則も二義的になる (詳細は [ADR 0012](0012-write-authority-by-lease-and-sha-cas.md)
  の Considered Options)。却下。
- **外部 DB / 外部 KV** — [ADR 0007](0007-github-as-sole-durable-state.md) の再掲で、状態が GitHub の
  外に出て -ilities 1 に違反する。却下。

## Consequences

### Positive

- 権威面が repo 境界として表現できるため、token のスコープだけで「誰が権威を書けるか」を切れる
  ([ADR 0013](0013-role-separated-tokens-and-credentials.md))。約束ではなく機構で守れる。
- code repo の履歴と ruleset が状態機械の都合で汚れない。人間の merge ゲートを支える保護規則を、権威の
  要求と独立に設計できる。
- lane 別 branch により、branch tip 単位の競合検出の粒度が lane の分離とちょうど一致する。
- 全権威レコードが 1 箇所に集まるので、「例外領域ゼロ」を repo 単位で点検できる。
- state repo も GitHub なので、[ADR 0007](0007-github-as-sole-durable-state.md) の制約の内側に留まる。

### Negative

- **管理対象の repo が 1 つ増える**。作成・ruleset 設定・バックアップ・lane branch の GC (archive
  rename) が、いずれも新たな運用作業である。
- **権威面が issue の UI から一段遠くなる**。人間は state repo の JSON を読むか、派生表示を信じるかに
  なり、派生の同期遅延が見える。-ilities 1 の「人間に見える」を、見え方の質としては下げている。
- **2 repo をまたぐ部分障害が新しい失敗モードとして生まれる**。派生が古いまま人間が判断する窓が構造的に
  存在し、「state repo が正」という規則はその窓を消しはしない。
- branch 数が lane 数に比例して増える。在庫上限 30 と 30 日での archive rename は、この増加を抑えるため
  だけに存在する運用規則であり、本質的な要求ではない。
- **失敗分類が message 文字列に依存する**。status code では rule 違反と CAS 敗北を区別できないため、
  GitHub 側の文言変更で分類が壊れる。fail closed が安全網だが、壊れれば全て needs-human に倒れる。
- 権威と実装対象が別 repo なので、**人間が 1 つの画面で全体を追えない**。調査のたびに 2 repo を突き合わ
  せることになる。
- **外部 DB / 外部 KV の却下は「一旦」ではなく条件付きの決定である**。GitHub を substrate に選んだ代償
  (レイテンシ・API 予算・lane 上限 30) は、規模が変われば割に合わなくなる。**再考のトリガを 3 条件で
  明文化する** — ① active lane の上限 30 が実運用で不足する ② tick 周期の短縮 (60s 未満) が要求される
  ③ API 予算の超過が常態化し、縮退運転が例外でなくなる。**このいずれかが観測されたら、substrate の
  選択を再審理する**。逆に言えば、3 条件が観測されない限り「速そうだから」を理由に KVS を持ち込むこと
  は本 ADR の再審理に当たらない。条件を書かずに「一旦 GitHub で」と書くと、再考は永久に来ないか、根拠
  なくいつでも来るかのどちらかになる。

### Neutral

- one-way door に近い。運用開始後に置き場を移すと、蓄積した lane branch の移送が必要になる。
- 本 ADR は [ADR 0009](0009-ility-priority-order.md) が「未起票の下流決定」として挙げていたもののうち、
  書き込み権威の置き場に相当する。
