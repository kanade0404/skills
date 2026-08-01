---
name: ci-self-heal
description: PR 作成後・push 後の CI が失敗した際に、ログから root cause を特定し、修正コミットを当てて再 push する自己修復ループを駆動するスキル。**root cause 不明なまま再試行しない** (NO FIXES WITHOUT ROOT CAUSE)。3 連続失敗で停止し、architecture を疑ってユーザに escalate (3-failure architecture gate)。`pr-review-respond` Phase E から呼ばれる主経路、`gh pr create` 直後で CI が回り始めた時、CI が赤になった時、「CI 直して」「ビルド失敗してる」「テストが落ちてる on CI」「pipeline 緑にして」のような要請、いずれでも必ず起動すること。本スキルは CI ログ取得 → root cause 仮説 → 修正 → 再 push → 再 watch のループ駆動と、停止判断を担う。修正コード自体は呼出側スキル (`tdd` / `tidy-first` / `code-review`) を経由する。flaky / 環境問題と判定したら retry-to-green は禁止 — 原因分類してユーザに返す。
claudecode:
  allowed-tools:
    - Read
    - Bash
    - Edit
    - Task
---

# CI Self-Heal

> **Iron Law**: NO FIXES WITHOUT ROOT CAUSE.
> retry-to-green / "もう一度走らせれば緑" / 推測修正の連投はしない。
>
> **3-Failure Architecture Gate**: 3 連続で同じ失敗を解消できなかったら停止して **architecture を疑う**。同じ層で粘らない。

Superpowers `systematic-debugging` の Iron Law を CI 失敗対応に適用したスキル。

---

## いつ起動するか

- `gh pr create` / `git push` 直後で CI が回り始めた時
- `pr-review-respond` Phase E で CI 緑待ちに入った時
- ユーザに「CI 直して」「ビルド失敗」「pipeline」「checks 落ちてる」と言われた時

逆に **起動しない**:

- ローカル test 失敗 (それは `verify-done` の手前の領域)
- CI が回っていない PR (push 前の話)
- 既に PR が merge 済み (post-merge は別経路)

---

## ワークフロー

### Step 1 — CI watch 開始 (wait_gate.sh — 待ちの form は実行文脈で決まる)

`gh pr checks --watch` を裸のフォアグラウンドで回さない (終端条件と deadline を持たないため制御が戻らない)。待ちは同梱スクリプト **`scripts/wait_gate.sh`** に一本化する — CI 終端をポーリングし、**終端 or deadline で必ず exit** して結果を exit code で機械分類する: `0` = 全 check 終端かつ緑 (fail / cancel が 0。skipping / neutral は非失敗) / `1` = 終端したが fail または cancel を含む / `2` = deadline 到達 (未終端) / `3` = gh の連続失敗。`length > 0` ガード (push 直後の空配列を「緑」と誤認しない)・終端 check の逐次 emit (緑でも沈黙しない coverage 規律) はスクリプト内に実装済み。

まず現状把握:

```bash
gh pr checks <PR>
```

次に wait_gate を呼ぶ。**呼び方は自分の実行文脈で決まる** (2026-08-01 の対照実験で確定した harness 制約):

- **subagent として実行中** (shipping からの dispatch が主経路): **foreground で blocking 呼び出しする**。

  ```bash
  <skill-dir>/scripts/wait_gate.sh <PR> 480 30   # deadline 480 秒 < ツール timeout 10 分
  ```

  これが subagent の**唯一の**待ち方である。subagent が起動した background タスク (Monitor / background Bash) は **subagent の return と同時に harness に回収され** (登録直後の消滅を対照実験で再現)、await 原語 (`TaskOutput`) も subagent には存在しない (「TaskOutput is not available inside subagents」)。exit `2` (deadline) なら wait_gate を呼び直す — 呼び直しごとに意思決定の機会が戻るため無限待ちにならない。**累計 32 分 (480 秒 × 4 回) を超えたら escalate**。

- **セッション main として直接実行中**: 同じスクリプトを `run_in_background: true` で起動してよい。main 所有の background タスクは「exit → 完了通知 → 再起動」が機能する (実測済みの唯一の main 側経路)。通知で戻ったら exit code を読んで下記の分岐へ。

**`Monitor` ツールは使わない**: 満了通知の不達が実測されており (PR #96, 2026-07-30: 30 分 timeout の Monitor が満了通知なしに約 8 時間放置)、subagent 内では上記のとおり return 時に回収されて最初から存在しない。wait_gate は deadline で必ず exit するため、通知配送に依存する待ちがそもそも発生しない。

exit code の分岐: `0` → 緑 (修復ループ中なら完了判定へ)。`1` → 失敗として Step 2 へ (`cancel` を緑と誤認しない)。`3` → 完了判定せずユーザに escalate (silent spin 防止)。`2` の累計超過 → escalate。また **自動レビュー (CodeRabbit 等) の完了待ちを CI watch の完了条件に含めない** — 本スキルの終端は checks の終端 bucket のみで判定する (レビュー対応は `pr-review-respond` の領域で、待ち合わせると review 側を starve する)。

### Step 2 — 失敗 check の特定

```bash
gh pr view <PR> --json statusCheckRollup
gh run list --branch <branch> --limit 5
gh run view <run-id> --log-failed       # 失敗ステップのログ
```

複数 check が失敗している場合は **独立な失敗** か **連鎖した失敗** を判定。連鎖なら最初の失敗から潰す。独立なら並列対応可能だが、本スキルでは順次対応 (混乱を避ける)。

### Step 3 — Root cause 仮説 (systematic-debugging 4-phase)

各失敗について以下の順で潰す。**順序を飛ばさない**：

1. **Investigation** — ログを最後から読む。エラー種別・stacktrace・該当ファイル/行を特定。再現可能か (ローカルで再現するか) 判定。
2. **Pattern** — 同じ失敗が過去にあるか (`git log --grep` / 既知の flaky 一覧)。同パターンの修正履歴を引く。
3. **Hypothesis** — 1 つだけ仮説を立てる。「~が原因で ~が起きている」と書く。複数仮説があるなら最も高確度の 1 つに絞る。
4. **Implementation** — 仮説に対する修正を当てる。修正は最小単位。

**禁止**:

- 仮説なしで修正を試す ("とりあえずキャッシュクリア")
- 同時に複数の修正を当てる (どれが効いたか分からない)
- ログを読まずに「再実行」する

### Step 4 — 失敗カテゴリ分類

| カテゴリ | 例 | 対応 |
|---|---|---|
| **Code error** | typecheck fail / test 失敗 / build fail | 修正コミットを当てる |
| **Spec drift** | 既存テストが新仕様で落ちる | 仕様 vs 実装の整合確認、設計フェーズに戻る判断 |
| **Flaky** | 同じテストが時々落ちる、network race / time race | retry しない、原因分類して `test-review` §6 に従って修正 |
| **Environment** | runner の OS / version / secret 不在 | 環境設定の問題、CI yaml / secrets の修正 |
| **Dependency** | npm / pip install fail / version conflict | lockfile / 依存ツリーの修正 |
| **Infra** | runner outage / GitHub status incident | retry を許可するが、原因 (incident URL) を記録 |

flaky / environment / infra は **コード修正で直さない**。retry-to-green は禁止。原因をユーザに返す。

### Step 5 — 修正コミット

修正は呼出側スキル経由：

- 振る舞い変更 → `tdd` (RED → GREEN)
- 構造変更のみ → `tidy-first` で commit
- 設定 / yaml / lockfile → 直接編集

commit message には **試行回数と原因仮説** を含める：

```text
fix(ci): handle null in parser (attempt 2)

Hypothesis: parser receives undefined when input is empty,
which breaks the regex match introduced in commit <SHA>.

Refs: <run URL>
```

push したら Step 1 に戻り、再 watch。

### Step 6 — 3-Failure Architecture Gate

**同じ失敗が 3 連続で解消されなかったら停止する**。試行カウンタを保持：

```text
- attempt 1: hypothesis A → fail
- attempt 2: hypothesis B → fail
- attempt 3: hypothesis C → fail
→ STOP. Architecture を疑う。
```

停止時のユーザへの報告：

```markdown
## CI Self-Heal: HALTED (3-failure gate)

### 試行履歴
1. <hypothesis> → <result>
2. <hypothesis> → <result>
3. <hypothesis> → <result>

### Architecture 仮説
- <現在の構造の何が問題と思われるか / 1-2 行>

### 推奨
- 設計フェーズ (`design`) に戻る
- このコンポーネントの境界 / 依存方向を再考
- ユーザ判断: 続行する場合は明示的に方針指示
```

3 失敗以降は **ユーザの明示指示なしに修正を続けない**。

### Step 7 — 緑になったら

`gh pr checks <PR>` で全 pass を確認したら、`verify-done` を呼んで最終 gate を通す。緑判定の証拠 (run URL) を最終報告に含める。

---

## 出力フォーマット

```markdown
## CI Self-Heal: <PR #n>

### Scope
- PR: <URL>
- Base: <branch>
- Initial failures: <n checks>

### Attempt log
1. [<check>] <hypothesis> → <fix commit SHA / category> → <pass/fail>
2. ...

### Final state
- Status: <PASS / HALTED / IN_PROGRESS>
- Last run: <URL>
- Pass: <n> / Fail: <n>

### Categorization
- Code error: <n>
- Flaky: <n> (recorded, not retried)
- Environment: <n>
- Infra: <n>

### Next
- (PASS) → verify-done → 完了報告
- (HALTED) → ユーザ判断要 (architecture 再考 / 続行指示)
```

---

## 出力する成果物 / 出力しない成果物

### 出力する成果物

- **attempt log** (1 行 = 1 試行: hypothesis + fix commit SHA + result + category)
- **修正 commit 列** (commit message に `Refs: <run URL>` と `attempt N` を含む)
- **Final state レポート** (PASS / HALTED / IN_PROGRESS + Categorization 内訳)
- **HALTED 時の Architecture 仮説** (3-failure gate に到達した場合のみ、再考対象を 1-2 行で出す)

### 出力しない成果物

- **retry-only commit / 再実行リクエスト**: 「再実行すれば通るかも」を根拠とする出力は出さない。
- **複数仮説を同時に当てる commit**: 1 attempt 1 hypothesis 1 fix の単位を超えた commit は出さない。
- **4 attempt 目以降の自動修正 commit**: 3-failure gate で停止し、ユーザ明示指示があるまで追加 commit を出さない。
- **flaky テストの skip / xfail 化 diff**: 隠蔽は出さない。原因分類または `flaky` ラベル化までで止める。
- **環境問題をコード修正で誤魔化す diff**: 環境 / インフラ問題は CI yaml / secrets / runner 設定への修正のみ出す。
- **同一仮説で paint だけ変えた修正 commit**: 仮説が外れたら仮説自体を捨て、別仮説の commit を出す。

---

## 既知の限界

- **CI 完了待ち**: Step 1 の `wait_gate.sh` に委ねる (subagent 実行時は foreground blocking、main 実行時は `run_in_background` — Step 1 の契約参照)。deadline 480 秒 × 4 回 = 累計 32 分を超える CI は escalate する。さらに長い CI はユーザが deadline / 呼び直し回数の引き上げを判断する。
- **Infra 問題の判定**: GitHub status / runner outage の判定は外部情報依存。本スキル単体では完璧に分類できない。incident と思われる場合はユーザに確認。
- **3-failure gate の数値**: Beck の "rule of three" に倣ったが、変更規模やシステム複雑度で適切な閾値は変わる。本スキルは 3 を default とし、ユーザ指示で上書き可能。
- **ログ末尾だけでは root cause 不明な場合**: stacktrace の中ほどに情報があるケースは、`gh run view --log` で全ログを取得して読む必要がある。本スキルは末尾優先だが、`grep -B 50 -A 5 'Error\|FAIL\|panic'` 等で広く取る判断もある。
