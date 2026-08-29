# GitHub Conformist を採る (変換層を置かない)

Status: accepted (2026-08-29)

Driver: [-ilities 1 可監査性・回復可能性](0009-ility-priority-order.md) (人間が見ている面と設計の
語彙が一致すること) と、solo 運用での維持コスト。用語は [CONTEXT.md](../../CONTEXT.md) に従う。

## Context

DDD の文脈マッピングには、上流の文脈 (ここでは GitHub) のモデルをそのまま受け入れる Conformist
と、変換層を挟んで自分のモデルを守る腐敗防止層 (ACL) がある。どちらを採るかは、上流モデルの
品質と、抽象を維持できる体制で決まる。

前提として、[ADR 0007](0007-github-as-sole-durable-state.md) により耐久状態は GitHub のみに置く。
つまり GitHub のモデル (issue / sub-issue / dependencies / label / check / PR) が状態そのもので
あって、その裏に別の実装が来る可能性は無い。

外部制約として、GitHub は sub-issues と issue dependencies を GA として提供しており、親子関係と
依存関係をネイティブに表現できる。自前の語彙に置き換えなければ表現できない概念が、機能面では
存在しない。

体制の制約として、運用は solo である。抽象を設計し、上流の変化に追随させ、実態との乖離を直す人員
が 1 人しかいない。

## Decision

**GitHub Conformist を採る**。GitHub の語彙をそのままドメイン語彙とし、GitHub モデルと自前モデル
の間に変換層を置かない。sub-issues / dependencies / labels / checks をそのまま使う。

[CONTEXT.md](../../CONTEXT.md) の用語は GitHub の構造への**名付け**であって、別のモデルではない。

- タスク = 親 issue
- 実装 issue = sub-issue ([ADR 0003](0003-two-layer-task-and-implementation-issues.md))
- 着手可 = blocked-by の依存が全て決着
- 決着 = issue が closed (completed / not_planned)

## Considered Options

- **腐敗防止層 (自前のドメインモデルを定義し、GitHub をアダプタの向こうに置く)** — 抽象の維持
  コストが solo 運用に見合わない。GitHub 側の機能追加 (sub-issues の GA など) を取り込むたびに
  抽象を作り直すことになり、抽象は常に実態より遅れる。加えて、ACL の主な利得である「上流の置換
  可能性」は、[ADR 0007](0007-github-as-sole-durable-state.md) で GitHub を唯一の状態ストアと
  決めた時点で発生しない。守るべき下位実装が存在しない抽象は、コストだけを持つ。却下。
- **部分的 ACL (依存グラフだけ自前で持つ)** — GitHub の dependencies と自前グラフの二重管理に
  なり、[ADR 0007](0007-github-as-sole-durable-state.md) が避けたのと同じ乖離を局所的に再導入
  する。却下。
- **GitHub を使わず専用ツールを作る** — 人間の承認ゲートと機械レビュー層が GitHub 上にある
  ([ADR 0004](0004-two-human-approval-gates.md) / [ADR 0005](0005-dual-track-security-review.md))。
  -ilities 1 の「人間に見える」を GitHub 以外で満たす手段が無い。却下。

## Consequences

### Positive

- 変換層のコードと、その変換が正しいことを検証するテストが丸ごと不要になる。
- GitHub の新機能をそのまま使える。追随のために抽象を書き直す作業が発生しない。
- 人間が GitHub UI で見ているものと、設計上の語彙が一致する。読むときに翻訳が要らない。

### Negative

- **GitHub のモデル変更・仕様変更・上限がドメインに直接刺さる**。sub-issue 100 件 / 8 階層といった
  上限も、緩衝なしに設計の上限になる。GitHub 側の破壊的変更が来たときに、吸収する場所が無い。
- **GitHub 以外への移行が事実上不可能になる**。これは one-way door である。
- GitHub の語彙で表現できない概念 — 差し戻しの 4 分類 ([ADR 0006](0006-four-way-spec-rejection-sum-type.md))、
  証明義務 — は、ラベルとコメント規約という弱い型で表現するしかない。sum type の網羅性と排他性を
  GitHub は強制してくれないので、その検証は自前のチェックとして残る。型の恩恵を受けられない領域
  が確実に生じる。
- **ドメイン語彙がプラットフォームの都合に汚染される**。label の文字列長、`state_reason` が 2 値
  しかないこと、といった GitHub 側の事情が CONTEXT.md の用語定義に染み出す圧力が常にかかる。
  用語の意味をレビューし続ける規律が別途要る。

### Neutral

- ラベル語彙そのものは two-way door だが、稼働中の変更には二段階デプロイと既存 issue の移行掃引
  が必要になる。
