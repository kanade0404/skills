---
name: adr-writer
description: 設計検討から出てきた決定について「これは ADR (Architecture Decision Record) に値するか」を判定し、値する場合のみ Michael Nygard 形式の ADR を `docs/adr/<NNNN>-<slug>.md` に生成するスキル。**コードを読めばわかる決定は ADR にしない**。値する基準は (1) 将来「なぜこうした?」と疑問になりうる (2) 容易に変更できない one-way door (3) 別の選択肢があり却下した のいずれか。番号は既存 ADR の最大 + 1 を採番。`design` Step 4 から呼ばれる主経路、設計判断を残したい時、「これ ADR にして」「決定記録残して」「architecture decision」「設計判断のドキュメント」のような要請、いずれでも必ず起動すること。本スキルは ADR 単体の生成と判定までで、設計検討自体や実装には関与しない。詳細仕様や API ドキュメントの代わりに ADR を使うことは推奨しない (ADR は「決定」の記録、「使い方」のドキュメントではない)。
allowed-tools:
  - Read
  - Write
  - Bash
---

# ADR Writer

> **規律**: コードを読めばわかる決定を ADR にしない。**過去の自分が悩んだ理由** だけを残す。

ADR (Architecture Decision Record) を Michael Nygard *Documenting Architecture Decisions* (2011) の形式で生成するスキル。

ADR を残す目的は **将来の自分 (or 後任) が「なぜこれを選んだか」を再構築できなくなるのを防ぐ** こと。実装やテストから読める情報は ADR に書かない。

---

## いつ起動するか

- `design` Step 4 から呼ばれる (主経路)
- 既存コードベースに大きな技術選択を入れる時 (ライブラリ採択、protocol 決定、データ境界変更)
- 既存方針を覆す決定を下した時 (ADR を「Superseded by NNNN」で繋ぐ)
- ユーザに「ADR にして」「決定記録」「Architecture Decision」と言われた時

逆に **起動しない**:

- API ドキュメント / 使用方法ガイドの代わり
- 1 関数の実装方針 (コード comment で十分)
- 一過性の判断 (型を 1 つ変えた等)

---

## ワークフロー

### Step 1 — ADR 値判定

以下の **3 基準のいずれか** を満たすか判定。1 つも該当しなければ「ADR 不要」と返す。

| 基準 | 判定の問い |
|---|---|
| **Question** | 1 年後の自分が「なぜこうした?」と疑問になりうるか |
| **One-way door** | 後で変えるのに大きなコストがかかるか (DB schema / 公開 API / 永続化形式 / 採用言語等) |
| **Alternatives existed** | 別の選択肢が複数あり、意識的に却下したか |

判定の禁則：

- **「念のため残しておく」で ADR を作らない**。3 基準のいずれも満たさない場合は Step 1 で停止。
- **使用方法の記述は ADR にしない**。それは README / docstring の領域。
- **コードを音読しただけの決定** ("we use TypeScript") を ADR にしない。それは慣例化していて疑問にならない。

### Step 2 — 番号採番

```bash
ls docs/adr/*.md 2>/dev/null \
  | sed -E 's|.*/([0-9]+)-.*|\1|' \
  | sort -n | tail -1
```

最大 + 1 を 4 桁ゼロ埋め (`0001`, `0042`)。`docs/adr/` が存在しなければユーザに作成許可を取って作る。

### Step 3 — Slug 生成

タイトルから kebab-case slug を作る。30 字以内に収める。

例:
- "Use Postgres for primary store" → `0042-use-postgres-for-primary-store.md`
- "Adopt Functional Core / Imperative Shell" → `0043-functional-core-imperative-shell.md`

### Step 4 — Nygard 形式で起草

固定テンプレ：

```markdown
# <NNNN>. <Title (動詞句)>

Date: <YYYY-MM-DD>

## Status

Proposed | Accepted | Deprecated | Superseded by [NNNN](NNNN-...)

## Context

<コードからは読めない事実 / 制約 / 前提を 1-3 段落>
- 外部要因: (SLA / 法令 / 他チーム契約 / パフォーマンス予算)
- 既存の関連決定: (ADR への相互参照)
- 制約条件: (リソース / 時間 / スキル)

## Decision

<採用した決定を 1-2 段落、命令形で>

## Consequences

### Positive
- <この決定が解く問題 / 得られる性質>

### Negative
- <この決定で生じる制約 / 受け入れるトレードオフ>

### Neutral
- <注記事項。後続決定の trigger になる前提>

## Alternatives Considered

### <案 A>
- 概要: <1 行>
- 却下理由: <1-2 行>

### <案 B>
- 概要: <1 行>
- 却下理由: <1-2 行>
```

書き方の規律：

- **Context は事実のみ書く**。「~したい」「~であるべき」は書かない。それは Decision に書く。
- **Decision は命令形 1 段落**。"We will use X" / "X を採用する"。条件付き決定は Decision に書かず、別 ADR に切る。
- **Consequences は Positive / Negative 両方書く**。トレードオフのない決定は ADR に値しない。
- **Alternatives は最低 2 つ**。「他案を検討していない」と書くくらいなら ADR を作らない。

### Step 5 — Status 管理

新規作成時は基本 `Accepted` (proposed → review → accepted を経ているなら直接 Accepted)。

既存 ADR を覆す場合：

1. 旧 ADR の Status を `Superseded by [NNNN](NNNN-...).md` に書き換える
2. 新 ADR の Status を `Accepted` にし、Context に「supersedes ADR-NNNN. 旧 ADR の前提が変わった理由は...」と書く
3. **旧 ADR を削除しない**。歴史を残す。

### Step 6 — Index 更新 (任意)

`docs/adr/README.md` がある場合は新規 ADR の行を追加：

```markdown
| NNNN | Title | Status | Date |
| --- | --- | --- | --- |
| 0042 | Use Postgres for primary store | Accepted | 2026-05-04 |
```

存在しなければ作らない。本スキルは index を強制しない。

---

## 出力フォーマット

```markdown
## ADR Decision

### 値判定
- 該当基準: Question / One-way door / Alternatives existed (該当のみ列挙)
- 値する: Yes / No

### Yes の場合
- File: docs/adr/<NNNN>-<slug>.md
- Status: <Accepted / Proposed / Superseded by ...>
- Title: <タイトル>
- Supersedes: <NNNN if any>

### No の場合
- 理由: <1 行>
- 推奨: コメント / README / 何も書かない のいずれか
```

---

## 出力する成果物 / 出力しない成果物

### 出力する成果物

- **`docs/adr/<NNNN>-<slug>.md` 1 ファイル** (Nygard 形式: Status / Context / Decision / Consequences / Alternatives Considered)
- **値判定結果** (Yes / No + 該当した基準 + 理由 1 行)
- **既存 ADR の Status 行更新** (Superseded by [NNNN](...) への書き換えのみ)

### 出力しない成果物

- **使用方法 / API ドキュメント文字列**: README / docstring / コード comment 領域、ADR には書かない。
- **複数決定を含む 1 ADR**: 1 ADR = 1 決定。関連決定の統合 ADR は出さない。
- **「仕様書」としての ADR**: 仕様は実装が真実の源、本スキルは決定の記録のみ出す。
- **古い ADR の本文編集差分** (Status 行を除く): 歴史を残すため、Decision / Context / Consequences の事後編集は出さない。
- **自動 supersede 判定結果**: 新決定が旧 ADR を覆すかの判断は人間 / `design` の責任、本スキルは指示された supersede 関係を反映するのみ。
- **`docs/adr/README.md` の自動編集差分**: index 更新は推奨提示までで、自動 commit は出さない。

---

## 既知の限界

- **値判定の主観**: 3 基準は曖昧さを残す。判定が割れたらユーザに尋ねる。**過剰に ADR を残すよりは少ない方を選ぶ** (rot 防止)。
- **採番の競合**: 並行作業で同番号を採番するリスク。本スキルは「現在の `git ls-files`」を見るだけで、PR ベースの番号競合 (両方 0042 を使った PR が同時にマージされる) は手動解決必要。
- **MADR 形式は採用しない**: より構造化された MADR (Markdown Architectural Decision Records) を選択することも可能だが、本スキルは Nygard を default とする。要望があれば `references/madr.md` を切り出す余地。
- **言語非依存**: コード言語に関係なく適用可能だが、言語固有の ADR (TypeScript の strict mode 採否、Python の type hint 規約等) は当該プロジェクトの判断に委ねる。
