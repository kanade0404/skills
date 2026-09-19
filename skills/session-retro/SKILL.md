---
name: session-retro
description: |
  作業セッション (issue 駆動の自走実行・実装・出荷・リリース) の終端で、transcript /
  会話履歴から学びを抽出し、**skill-edit (既存 skill の改訂 / 新規 skill の提案 =
  feedforward) / sensor (hook・permissions・テスト・lint・CI チェック = 決定論的
  ハーネス = feedback) / issue (繰り越し作業、acceptance criteria 必須) / eval-case
  (golden set 候補)** の 4 分岐に振り分けて提案として出力する振り返り専用スキル。
  どちらの表現手段にも落とせなかった指示は終端分岐 **neither** として記録し、「構造的に
  再発しない (none)」とは書き分ける。シグナル源は「失敗した tool 呼び出し」「人間による
  訂正」「エスカレーション」の 3 つに限定し、全ログの漫然とした要約はしない。同じ失敗が
  2 回目なら issue でなく skill-edit/sensor に昇格させる。skill-edit 提案は特定の失敗に
  トレースできるものだけに絞り、追加と同時に既存記述の剪定候補も提示する。

  issue 対応・実装・出荷セッションの終端 (shipping / linear-issue-driven-development の
  完了直後)、release 後、「振り返りして」「retro」「レトロして」「このセッションの学びを
  まとめて」「教訓を残して」「二度と起きないようにして」「学びを skill に反映して」
  「詰まったことを issue 化しておいて」のような要請、Stop hook からの自動起動、いずれ
  でも必ず起動すること。

  範囲外: セッションの単純要約 (分岐が不要な依頼)、規約ファイル全体の監査・改善
  (claude-md-improver 等の専用系)、いま起きているバグの根本原因分析
  (systematic-debugging)、skill 本文のチューニング (skill-builder)、コードレビュー
  (code-review)。出力は全て **提案まで** — skill 改訂・golden set 追加・issue 起票は
  人間の承認を経る。
claudecode:
  allowed-tools:
    - Read
    - Grep
    - Glob
    - Bash
    - Write
    - Edit
---

# session-retro

> **規律 1**: 複利は issue の数ではなく skill/sensor の蓄積から生まれる。issue は「今やらない作業」の置き場であり、再発防止の置き場ではない。
> **規律 2**: 同じ問題が 2 回起きたら、それはもう issue ではない。feedforward (skill-edit) か feedback (sensor) を改善して再発確率を下げる。
> **規律 3**: 特定の過去の失敗にトレースできない skill 追記はノイズ。追加するたびに剪定候補を出す (具体的な 10 行は汎用的な 100 行に勝る)。
> **規律 4**: 指示の表現手段は **skill (起動条件を持つ状況依存の手順知識)** か **決定論的ハーネス (hook / permissions / CI / lint)** の 2 つだけ。どちらにも落とせなかったものは黙って捨てず `neither` に記録する。

セッションの終端で「このセッションが次のセッションを楽にするもの」を抽出して 4 分岐に振り分ける。振り返りを issue 化だけで終わらせると backlog は伸びるが同じ失敗は再発し続ける。防止は harness (skill-edit/sensor) に、作業は issue に、検証材料は eval-case に、それぞれ正しい置き場へ送るのが本スキルの仕事。どこにも置けなかった事実自体も、観測不能にしないために `neither` として残す。

---

## いつ使うか / 使わない場面

**使う**:

- issue 対応・実装・出荷セッションの終端 (shipping / linear-issue-driven-development の完了・エスカレーション直後)
- release 後の振り返り
- 「振り返りして」「retro」「学びをまとめて」「教訓を残して」「二度と起きないようにして」
- 「この学びを skill に反映して」(今セッションの学び由来の最小差分提案として)
- Stop hook / Routine からの自動起動。Stop hook からの自動起動は consumer 側の任意設定であり ([references/loop-ops-integration.md](references/loop-ops-integration.md))、本リポジトリは Stop hook を同梱しない

**使わない** (成果物で判定):

- セッションの**要約文**だけが欲しい依頼 → 通常の応答で足りる
- **規約ファイル全体の監査レポート** → claude-md-improver 等の専用系
- **いま起きている失敗の原因特定** → systematic-debugging (retro は事後、debug は渦中)
- **skill の description / 本文の改訂そのもの** → skill-builder (retro は「skill を直すべき」という提案までを出す)
- **コードの findings** → code-review

---

## 入力の解決

上から順に試し、最初に使えたものをシグナル源とする:

1. **transcript JSONL**: `$CLAUDE_SESSION_ID` が定義されていれば `~/.claude/projects/<cwd の / を - に置換した slug>/<session-id>.jsonl` を探す
2. **会話コンテキスト**: transcript が読めない環境 (cloud / headless / compaction 後) では、本セッションの記憶にある範囲で行う。compaction で古い履歴が失われている場合はその旨をレポートに明記する
3. **外部証跡**: PR のコメント・CI ログ・エスカレーションコメント (対象がわかっている場合のみ)

## ワークフロー

### Step 1 — シグナル抽出 (3 源限定)

全ログを均等に読まない。以下の 3 つだけを拾う:

| シグナル | transcript での見つけ方 | なぜ高シグナルか |
|---|---|---|
| **失敗した tool 呼び出し** | `"is_error": true` の tool_result | 環境・手順・前提の欠陥が集中する |
| **人間による訂正** | assistant の出力直後に方向修正・否定・やり直し指示をする user メッセージ | 「書き手には自明、agent には不明瞭」の証拠 |
| **エスカレーション** | `needs-human` ラベル付与、`loop-escalation` コメント、打ち切り宣言 | ループの限界点そのもの |

transcript がある場合の抽出例:

```bash
jq -r 'select(.type == "user") | .. | objects | select(.is_error == true) | .content' "$TRANSCRIPT" 2>/dev/null | head -30
```

シグナル 0 件なら「学びなし」で正常終了してよい (無理に絞り出さない。空振りの retro を量産すると形骸化する)。

### Step 2 — 分岐判定

各シグナルに以下を順に問う。1 シグナルが複数分岐に落ちてよい (例: 再発失敗 → skill-edit + eval-case):

1. **過去にも起きた failure か?** (過去 retro・issue 履歴・既存 skill に痕跡があるか)
   → yes なら **issue は禁止**。skill-edit か sensor に必ず昇格させる
2. **機械で検出・強制できるか?** (hook・permissions・テスト・lint・CI チェックで捕まえられるか)
   → yes なら **sensor** (決定論的ハーネス)。LLM の読解に頼る散文より常に優先する
3. **起動条件を定義できる状況依存の手順知識か?** (「いつ読むべきか」を description の
   trigger として書けるか)
   → yes なら **skill-edit**。既存 skill への最小差分が第一候補で、既存のどれにも属さない
     ときだけ新規 skill を `skill-builder` に提案する。ただし「特定の失敗にトレースできる
     具体文」で書けるときだけ。提案前に「個別ケースではなく原則に一般化できるか (理由を
     添えて)」も必ず問う
4. **今やらない作業として切り出すべきか?**
   → yes なら **issue**。acceptance criteria + 検証方法を必ず含める (下記フォーマット)
5. **再現可能な失敗ケースとして残す価値があるか?** (skill / loop の改訂を検証できるか)
   → yes なら **eval-case** (golden set 候補)
6. **構造的に再発するのに 2 も 3 も成立しなかったか?**
   → yes なら **neither** (終端分岐)。**skill の trigger でも決定論的ハーネスでも表現でき
     なかった指示**を、下記の記録フィールド付きで残す。**`none` (構造的に再発しないので
     何もしない) と混ぜない** — 別の結論として書き分ける。neither は「今回は表現手段が
     無かった」という観測であり、消えると「常時必要な横断指示が書けない」事例が観測不能に
     なる

### Step 3 — 剪定チェック (skill-edit を 1 件でも提案する場合は必須)

skill 本文は足す一方だと context rot でループ全体を劣化させる。追加提案と同時に:

- 対象 skill の既存記述を読み、**今回のセッションで一度も効いていない・現状と矛盾する・新しい提案と重複する**ものを最低 1 件、削除/統合候補として挙げる
- 候補が本当に無ければ「剪定候補なし」と明記する (省略しない)

### Step 4 — 出力 (提案として)

下記フォーマットで提示する。**この時点ではどこにも書き込まない。**

### Step 5 — 承認後の handoff

| 分岐 | 承認後のアクション | 実装の担当 |
|---|---|---|
| skill-edit (既存 skill 改訂) | `skill-improver` 経由で最小差分 PR 化し `improvements/ledger.jsonl` に記録 (メタスキルは除外) | `skill-improver` (構造改訂が要るなら skill-builder へ) |
| skill-edit (新規 skill) | 新しい skill の scaffold と trigger 設計 | `skill-builder` |
| sensor | hook / permissions / テスト / lint / CI チェックの実装 | tdd (behavioral) / tidy-first (structural) へ handoff |
| issue | `gh issue create` (下記ドラフトのまま) | 本スキル |
| eval-case | loop-ops `golden/cases/` への PR 起票 | 本スキル (merge は人間) |
| neither | レポートに記録として残すだけ (実装先は無い)。**記録は下記の固定形で書く** — `retro` の横断 sweep がログから `neither:` 行を拾って数え、**2 件以上で SessionStart hook の `additionalContext` 案を提案する** (ADR 0018 トリガ 2)。形が崩れると数えられず、トリガが原理的に発火しない | 人間 (`retro` の横断 sweep が計数し、閾値に達したら配信機構の設計判断へ) |

**skill-edit / sensor の宛先はクラウド実行にも届く配布層を優先する** (配布元 skills
リポジトリの `skills/` `hooks/`、`permissions`)。ローカル専用ファイル (`~/.claude/*`,
`settings.local.json`) はそのマシンでしか効かないため、配布層が無い場合の最後の選択肢と
する。宛先の判定自体は `harness-distribution` が持つ。

計測イベント (`agent_run` 等) の送信は本スキルの仕事ではなく実行ラッパー / Stop hook の仕事。配線方法は [references/loop-ops-integration.md](references/loop-ops-integration.md) を参照する (loop-ops 連携がある環境でのみ)。

---

## 出力フォーマット

```markdown
# Session Retro: <セッションの一言要約>

## シグナル
| # | 種別 (tool失敗/訂正/エスカレーション) | 何が起きたか (1 行) | 再発? |
|---|---|---|---|

## 分岐
### skill-edit (feedforward)
- 提案: <対象 skill の SKILL.md / 新規 skill 名> に「<具体文>」を追加
  - trace: シグナル #N
  - 起動条件: <この指示が読まれるべき trigger。書けないなら skill-edit ではない>
  - 剪定候補: <既存記述の削除/統合案、無ければ「なし」と明記>
### sensor (feedback / 決定論的ハーネス)
- 提案: <hook / permissions / テスト / lint / CI チェックの具体案> — trace: #N
### issue (deferred work)
- <下記ドラフト形式> — trace: #N
### eval-case (golden set 候補)
- <下記ドラフト形式> — trace: #N
### neither (表現手段が無かった — 終端分岐)
- <下記の記録フォーマット> — trace: #N

## 見送り (none)
- <シグナルだが構造的に再発しないと判断したもの + 理由>。**neither とは別** — none は
  「再発しないので何もしない」、neither は「再発するのに表現手段が無い」

## 承認待ちアクション
- [ ] skill-edit handoff / [ ] sensor handoff / [ ] issue 起票 / [ ] eval-case PR

neither 記録 (記録そのものが成果物、実装先なし): <n> 件
```

### neither 記録フォーマット (終端分岐)

skill でも決定論的ハーネスでも表現できなかった指示は、次のフィールドを埋めて残す。**承認後の
実装先は無く、記録そのものが成果物である**:

**1 行目は固定形で書く** — `neither: ` で始まり、その後に指示の一文を置く。`retro` の横断
sweep はセッションログからこの接頭辞で過去の記録を拾って件数を数え、**2 件以上で
SessionStart hook の `additionalContext` 案を提案する** (ADR 0018 トリガ 2)。両 skill とも
提案のみで永続ストアを持たないため、**この出力そのものが唯一の記録媒体**であり、形を崩すと
grep で見つからず件数に入らない (= 観測不能になり、トリガが原理的に発火しない)。

```markdown
- neither: <書きたかった指示の一文>
- trace: シグナル #N (どの失敗・訂正・エスカレーション由来か)
- why-not-skill: <なぜ起動条件 (trigger) として書けなかったか。「常時適用」「skill の外側が
  対象」等>
- why-not-harness: <なぜ hook / permissions / CI / lint で強制・検出できなかったか>
- 影響: <表現できなかったことで何が担保されないままか>
```

### issue ドラフト形式 (acceptance criteria 必須)

自走ループの成否は issue の入口品質で決まる。以下を欠く issue は起票しない:

```markdown
title: <動詞で始まる 1 行>
body:
## 背景
<なぜやるか。retro のどのシグナル由来か>
## Acceptance Criteria
- [ ] <機械 or 人間が判定可能な条件。「いい感じ」禁止>
## 検証方法
<どのコマンド / テスト / 操作で満たしたと判定するか>
```

### eval-case ドラフト形式

loop-ops `golden/cases/<id>.md` の形式に合わせる:

```markdown
---
id: <kebab-case>
type: canary | should-escalate | regression
source: <この retro の対象セッション / PR の URL>
target_repo: <repo>
expected_outcome: <merged | escalated:<reason> | ...>
verifier: <合否の機械判定方法>
runs: 3
---
<agent に渡す issue 本文>
```

---

## このスキルがやらないこと

- **規約ファイル / skill カタログの全体監査レポート** (今セッション由来の最小差分提案のみ)
- **skill 本文の改訂そのもの** (「直すべき」という提案と根拠まで。実装は skill-builder)
- **テスト / CI チェックの実装** (sensor の仕様提示まで。実装は tdd / tidy-first)
- **golden set への直接コミット** (PR 提案まで。merge は人間 — 改善対象の agent が自分の合格基準を書ける状態にしないため)
- **計測イベントの送信** (実行ラッパー / Stop hook の責務。references 参照)
- **承認前のあらゆる書き込み**

## リファレンス

- [references/loop-ops-integration.md](references/loop-ops-integration.md) — loop-ops (計測データストア) 連携: Stop hook での agent_run 送信、eval-case PR の出し方、エスカレーション形式。loop-ops を使う環境でのみ参照する
