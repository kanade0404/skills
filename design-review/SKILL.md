---
name: design-review
description: `design` スキルが出した scratchpad と (あれば) ADR ドラフトを、白紙の subagent にレビューさせて Critical / Important / Minor の三分類で findings を返すゲート用スキル。観点は responsibility 過剰、依存方向違反 (domain → infra への漏れ / 循環参照)、I/O 境界の漏れ、テスタビリティ欠如、可逆性 (one-way door の見落とし)、外部制約の前提崩れ、代替案検討の不足、慣例 (CLAUDE.md / 既存コード) との矛盾。`design` 完了直後・「設計レビューして」「この設計いい?」「ここの構造おかしくない?」「依存方向大丈夫?」のような要請・PR 起票前に設計判断のセカンドオピニオンが欲しい時、いずれでも必ず起動すること。本スキルはレビューのみ。設計修正は `design` に戻し、実装はしない。`requesting-code-review` 系のレビュー流儀 (severity 三分類 + Critical/Important が unresolved なら進めない) を設計フェーズに適用する。レビュアー subagent は新規 dispatch し、設計を書いた本人 (= 本スキル呼出側) には評価させない。
allowed-tools:
  - Read
  - Task
---

# Design Review

> **規律**: 設計を書いた主体に評価させない。常に **白紙の subagent** で再読して「書き手には自明、読み手には不明瞭」を炙り出す。

`design` フェーズの自己評価で済ませると、書き手の理解にバイアスされた評価になり、後で実装段階・運用段階で破綻が露呈する。本スキルはバイアスを排除する subagent dispatch ゲート。

---

## いつ起動するか

- `design` の Step 5 で呼ばれる (主経路)
- 既存の設計ドキュメント (ADR draft / scratchpad) のセカンドオピニオンが欲しい時
- 「この依存方向 / 構造選択でいいか」と聞かれた時

逆に **起動しない**:

- 実装後のコードレビュー (これは `code-review` の領域)
- 要件レビュー (これは `requirements-review` の領域)
- 単純な命名相談 (`tidy-first` Tidying の領域)

---

## ワークフロー

### Step 1 — Subagent への入力を整える

以下を 1 メッセージにまとめる：

- 設計の **採用案** (1-3 行)
- **却下案と理由** (最低 2 つ)
- 関連する **既存コード** へのファイルパス参照 (subagent に読ませるため)
- 設計対象が触る **I/O 境界 / 外部依存** の列挙
- 既存規約ファイル (`CLAUDE.md`, `docs/conventions.md`, ADR ディレクトリ) へのパス

### Step 2 — Subagent dispatch (白紙)

Task tool で `general-purpose` agent を新規起動。プロンプトに必ず含める:

- 「あなたは設計レビュアーです。この設計を初見で読みます」
- 「設計を書いた前提知識は持ち込まない」
- 観点リスト (下記)
- 出力フォーマット (severity 三分類)
- "performative agreement 禁止" の明示 (You're absolutely right! 禁止)

### Step 3 — レビュー観点 (subagent に渡す)

```markdown
## 観点

1. Responsibility — この変更で増える module/class/function は単一責務か / 既存責務と重複していないか
2. Direction of Dependencies — 不安定なもの (volatile) → 安定したもの (stable) の方向か / domain → infra になっていないか / 循環参照は無いか
3. I/O Boundaries — 純関数化できる部分が外部 I/O と混在していないか / Humble Object パターンが守れているか
4. Testability — test double 不要で書けるか / 直接 unit テスト可能か
5. Reversibility — one-way door (後で変えにくい) を two-way door に倒せないか
6. External Constraints — 設計の前提となる外部要因 (SLA / API 契約 / 法令 / パフォーマンス予算) が明示されているか / 前提が変わったときの再考 trigger が定義されているか
7. Alternatives — 却下案が最低 2 つあるか / 却下理由が「なんとなく」ではなく具体か
8. Convention — CLAUDE.md / 既存 ADR / 既存コードの慣例と矛盾していないか / 矛盾するなら ADR に正当化が書かれているか

## Severity

- Critical: 本設計のまま進めると重大な技術的負債を生む / 後で revert がほぼ不能 → ブロック
- Important: PR 前に対処すべき / 進めるならトレードオフを ADR に明記 → 対処を要求
- Minor: 改善余地はあるが PR 後でも良い / 取捨選択は実装者
```

### Step 4 — Findings の収集

subagent からの戻り値を以下の構造で受け取る：

```markdown
## Critical
- [<観点>] <issue 1 行>
  - Suggested action: <具体的な修正提案 1 行>

## Important
- ...

## Minor
- ...

## What's good
- ...
```

### Step 5 — 判定

| 状態 | 次の手 |
|---|---|
| Critical = 0 / Important = 0 | PASS — 実装フェーズへ進める |
| Critical = 0 / Important > 0 | PASS_WITH_FIXES — Important を対処してから or トレードオフを ADR に追記 |
| Critical > 0 | FAIL — `design` Step 2 に戻る |

呼出側 (`design`) に判定と findings をそのまま返す。本スキル内で再設計はしない。

---

## 出力フォーマット

```markdown
## Design Review: <design summary 1 行>

### Findings
- Critical: <n>
- Important: <n>
- Minor: <n>

### Critical (blocks)
- [<観点>] <issue> → <action>

### Important (fix or ADR 追記)
- ...

### Minor
- ...

### What's good
- ...

### Verdict
- PASS / PASS_WITH_FIXES / FAIL
- Next: 実装へ / Important 対処 / 再設計 (design へ戻る)
```

---

## 出力する成果物 / 出力しない成果物

### 出力する成果物

- **8 観点 × severity 三分類の findings リスト** (Critical / Important / Minor / What's good の固定構造)
- **Verdict** (PASS / PASS_WITH_FIXES / FAIL のいずれか + 次の手 1 行)
- **subagent dispatch (新規)** — Task tool 経由で white-slate review を 1 回起動

### 出力しない成果物

- **設計の修正差分**: findings のみ返し、修正版設計は `design` に差し戻して再起草させる。
- **実装コード**: Verdict が PASS でも本スキル経由の実装出力はない (`tdd` / 直接編集の領域)。
- **同一 subagent での再 review 結果**: 修正後の再レビューは新規 subagent dispatch、過去 agent の再利用出力は出さない。
- **要件レビュー / コードレビューの findings**: それぞれ `requirements-review` / `code-review` の領域。
- **subagent 指摘の盲信受容**: pushback 候補は明示し、INVALID 判定は根拠と共に出力に残す。

---

## 既知の限界

- **subagent の知識深度は session に依存**: 大規模リポの全慣例を 1 subagent が理解できない場合、findings が浅くなる。観点リストで補強。
- **「不安定 → 安定」依存方向の判定が文脈依存**: ライブラリの安定度はチーム依存。CLAUDE.md / プロジェクト規約に明記されていない場合は subagent に「判定不能」と返させ、Important 扱いにする。
- **Solo dev 向けの severity 三分類**: チーム運用では「ブロック権限」がレビュアーごとに違うため別の運用が必要。本スキルは solo 前提。
