# improvements/ledger.jsonl — スキーマと操作

改善台帳。**1 行 1 JSON オブジェクト** の JSON Lines で、リポジトリ root の
`improvements/ledger.jsonl` に置く。finding が PR になり merge / revert されるまでの
一生を 1 エントリで追跡し、「同じ改善を何回やり直しているか」を可視化する。

台帳を skill ディレクトリではなく repo root に置くのは、対象が複数 skill に跨るのと、
consumer 側が `rulesync fetch --features skills` で持っていくのは skill ディレクトリ
だけで、台帳は**この repo の運用データ**だからである。

## 台帳の書き込みは PR を通る

`ledger.py` の書き込みはファイルを丸ごと書き直す (一時ファイル + `os.replace`) ため、
1 回の書き込みが途中で壊れることは無い。「読んで書く」の区間は `<ledger>.lock` に対する
`flock` (advisory lock) で囲ってあり、重なった実行が互いの更新を上書きで消すことも
無い (`fcntl` の無い環境では素通しに落ちる)。実行そのものの直列化はさらに 2 段構えで、
workflow は `concurrency: skill-improver` で 1 実行ずつしか走らず、実行を跨いだ競合は
git が受け止める (台帳への追記は必ずブランチ + PR 経由で default branch に入るので、
衝突する追記は merge conflict として人間の目の前に出る)。

その帰結として、**台帳の更新が次の実行から見えるのは PR が merge された後**である。
実行中に書いた行はそのブランチにしか無く、次回の実行は default branch の台帳を読む。
この遅延は承認ゲートを PR レビューに置いたことの代償として受け入れる。

そのぶん、**まだ merge されていない改善 PR が持っている行も読む**必要がある
(SKILL.md Step 0)。open な `improve/*` PR の head ブランチから台帳を取り出し、
`--ledger` でそのファイルを指して `list` すればよい:

```bash
gh api "repos/<owner>/<repo>/contents/improvements/ledger.jsonl?ref=<branch>" \
  --jq '.content' | base64 -d > "$tmp/<branch>.jsonl"
uv run python3 skills/skill-improver/scripts/ledger.py list --ledger "$tmp/<branch>.jsonl" --json
```

ただし **head ブランチの台帳は default branch の履歴を丸ごと含む**。そこに載っている
という理由だけで pending 扱いにすると、過去に `merged` / `rejected` / `reverted` に
なった行が「対応済み」として効いてしまい、同じ skill で本当に再発した finding を
握り潰す。pending と数えるのは次の 2 つだけ:

| 行の状態 | 意味 |
|---|---|
| `status == "pr_open"` | その PR が現に開いている |
| `status == "proposed"` かつ `pr == null` | `add` 済みだが、まだ PR に紐付いていない |

この条件を満たす行と `target_skill` × finding クラスが一致する finding は
**処理済み (pending)** であり、新規候補にしない。これをしないと、レビュー待ちの
PR がある間じゅう同じ finding で PR を出し続ける。

`proposed` かつ `pr == null` の行に対応する open PR がある場合は、pending ではなく
**修復対象**である: PR 起票の後に `link-pr` の commit / push が落ちるとこの形で残る。
新しい候補として拾い直さず、そのブランチで `link-pr` → commit → push を実行して
紐付けを完成させる。

workflow 側の補償も同じ不変条件を守る: `link-pr` が落ちたとき、**台帳に
`link-pr` + `set-status --status rejected --notes "link-pr failed: ..."` を
push できたときだけ** PR を閉じる。台帳に書けなければ PR は open のまま残す —
「PR は closed、台帳は `proposed` / `pr == null`」という、PR からも台帳からも
辿れない状態を作らないため。

## エントリのフィールド

| field | 型 | 内容 |
|---|---|---|
| `id` | string | `IMP-<YYYYMMDD>-<hash>` 形式 (例: `IMP-20260910-4ce5643613`)。`ledger.py add` が **内容から** 決める — 作成日 + `sha1(target_skill + "\n" + 再発クラスキー)` の先頭 10 桁 |
| `created` | string | `YYYY-MM-DD` (UTC) |
| `source` | enum | `retro` / `session-retro` / `agent-feedback` / `trigger-eval` |
| `evidence` | string[] | 証跡。PR / issue の URL、session id、`skills/x/evals/...` のようなファイルパス |
| `target_skill` | string | 改善対象の skill 名 |
| `finding` | string | **1 文**。長い説明は PR 本文に書く |
| `finding_class` | string | 再発クラスキー。空なら finding 本文の正規化で代用する。agent が「同じ問題の再発」と判断したときに `add --class <key>` で明示する |
| `lever` | enum | `skill-edit` / `ept` / `trigger`。上流 (`retro` / `session-retro`) の呼び名 `ept-handoff` も `add --lever` で受け付け、`ept` に正規化して保存する |
| `status` | enum | `proposed` / `pr_open` / `merged` / `rejected` / `excluded_meta` / `reverted` |
| `pr` | string \| null | PR URL |
| `before` / `after` | object | 指標。キーは全て省略可: `trigger_f1` / `ci_fix_iterations` / `review_cycles` / `escalations` |
| `recurrence` | int | 同一 `target_skill` × 同一 finding クラスの通算回数 (今回を含む)。`add` が自動計算する。`report` は台帳から数え直した値を正とし、保存値は数え直しより大きいときだけ tiebreak として採る |
| `notes` | string | 補足 (見送り理由、人間への申し送り等) |

`trigger_f1` だけ**大きいほど良い**。他の 3 つは小さいほど良い。この向きが
`report` の `improved` / `worse` 判定と revert candidate の検出に使われる。

**再発クラスは agent が決める**。`finding_class` が空のときスクリプトは finding 本文を
NFKC + casefold し、空白と記号を落としたものをクラスキーにする — 吸収できるのは
**表記揺れだけ** (`3 連続失敗` と `3連続失敗`、大文字小文字、句読点の有無)。日本語の
言い換えや語順違いは別クラスに落ちる。文字列一致で意味の同一性を判定しようとすると
無関係な finding を畳んで recurrence を水増しするため、そこは自動化しない。
`report --skill <skill>` を読んで同じ問題の再発だと判断したら、`add --class <key>` で
同じキーを渡して束ねる。

指標は**取れたものだけ書く**。観測できなかった値を 0 で埋めると、after 比較で
偽の「改善」が出る。`nan` / `inf` は数値に見えても比較が全て False に倒れ、悪化を
黙って見逃すため `record-metrics` が受け付けない。

## id が内容由来である理由

`add` は台帳の既存行を読まずに id を決める: `IMP-<作成日 YYYYMMDD>-<sha1(target_skill
+ 改行 + 再発クラスキー) の先頭 10 桁>`。連番にすると、1 実行で複数 finding を処理する
ときに枝ごとの `add` が「自分の見た台帳の最大値 + 1」を採り、台帳行が PR ごとに
append される以上必ず衝突する。内容由来なら、どの枝で採番しても同じ finding には
同じ id が付く。

その裏返しとして、**同じ日・同じ skill・同じクラスの 2 度目の `add` は同じ id になり、
重複として拒否される** (別 finding なら `--class` で別クラスキーを付ける)。`--id` を
手で渡す場合も形式検査と重複検査を通る — id は `set-status` / `link-pr` /
`record-metrics` の宛先そのもので、重複を許すと更新が「最初に一致した行」に当たって
別の finding を書き換える。

再発は日をまたいで起きるため、`recurrence` の計算 (同一 skill × 同一クラス) は
id の一意性と両立する。

### 例 (1 行に収める。ここでは可読性のため折り返している)

```json
{"id":"IMP-20260910-4ce5643613","created":"2026-09-10","source":"agent-feedback",
 "evidence":["https://github.com/kanade0404/skills/pull/123#issuecomment-1",
             "session_01ABC"],
 "target_skill":"ci-self-heal","finding":"3 連続失敗の停止条件が「同一エラー」に
 限定されて読まれ、別エラーで無限に再試行した","finding_class":"stop-condition",
 "lever":"skill-edit","status":"merged",
 "pr":"https://github.com/kanade0404/skills/pull/130",
 "before":{"ci_fix_iterations":6},"after":{"ci_fix_iterations":3},
 "recurrence":2,"notes":""}
```

## scripts/ledger.py

stdlib のみ。`uv run python3 skills/skill-improver/scripts/ledger.py <sub>` (追加の依存も
仮想環境も要らない)。台帳のパスは `--ledger` で上書きでき、既定は cwd から上方向に
`.git` を探して見つけた repo root の `improvements/ledger.jsonl`。

target skill の実在確認は**台帳と同じリポジトリ**で行う: `--ledger` を渡したときは
その 2 つ上 (`<repo>/improvements/ledger.jsonl` 規約) を root とみなす。渡さなければ
cwd から見た repo root。規約外の場所に台帳を置くときは `--skills-root <path>` で
明示する。cwd 固定にすると、別リポジトリの台帳を触りながら skill 名の検査だけ手元の
カタログで行い、存在しない skill を「既知」と判定してしまう。

| サブコマンド | 用途 |
|---|---|
| `add --source --target --finding --lever [--class] [--evidence ...] [--status] [--notes] [--id] [--created]` | finding を 1 件記録。`recurrence` を自動計算し、`id` を内容から決める。`--class` は再発クラスキーの明示。`--id` は形式 (`IMP-YYYYMMDD-xxxxxxxxxx`) と重複を検査する。対象がメタスキルなら `--status` を無視して `excluded_meta` で記録し、**exit 2** を返す |
| `set-status --id --status [--notes]` | status を更新 |
| `link-pr --id --pr [--keep-status]` | PR URL を紐付け、既定で `status=pr_open` にする |
| `record-metrics --id --phase before\|after --metric KEY=VALUE [--metric ...]` | 指標を記録。両相が揃うと delta を表示する。非有限値 (`nan` / `inf`) は拒否 |
| `list [--status ...] [--skill] [--missing-after] [--json]` | エントリを絞って列挙。Step 0 の突き合わせは `--status pr_open` と `--missing-after` (merged なのに `after` が空) の 2 本を起点にする |
| `report [--skill] [--json] [--fail-on-revert]` | skill 別の件数・**再発クラスキーとその件数**・status 内訳、before→after の delta、**merged without after metrics**、**revert candidate** を出力 |
| `verify-diff --head <file> [--base <file>] --mode candidate\|reconcile [--ledger-id ID]` | ブランチの台帳差分が許された変更だけかを検査する (workflow の `verify` job 用) |
| `check-target <skill>` | 改善対象にしてよいかの判定 |

### exit code 契約

| code | 意味 |
|---|---|
| 0 | 成功 / `check-target` が改善対象と判定 |
| 1 | 検査で不合格 (`check-target` が未知の skill / skill ディレクトリを解決不能、`report --fail-on-revert` が revert candidate を検出) |
| 2 | 対象が**メタスキル** (改善対象外)。`check-target` の判定、`add` の記録時、および `set-status` / `link-pr` / `record-metrics` のガード |

argparse は usage エラーでも 2 を返すため、呼出側は stdout 1 行目の
`classification: <ok|unknown|excluded_meta|unresolved>` で曖昧さを解消する
(usage エラー時はこの行が出ない)。

`check-target` は skill を `skills/`, `.claude/skills/`, `.agents/skills/` の順に
探す (配布元カタログ形式と consumer 生成先の両方で動かすため)。どれも無い環境では
`unresolved` で **exit 1** — 「見つからない = 対象外」と黙って扱わず fail-closed にする。

skill 名は `Path(...).name` に落として casefold した形で比較し、**その正規化した名前で
台帳に保存する**。メタスキル判定も既知判定も同じ正規化を通るので、`Retro` /
`skills/retro` のような書き方でも除外され (`--allow-unknown-target` でも外れない)、
`skills/tdd` と `tdd` が別クラスに割れて recurrence を数え損ねることもない。除外は `add` だけでなく `set-status` / `link-pr` / `record-metrics` にも
かかる — 入口ひとつでしか効かないガードは、後から台帳を進めるだけで迂回できるため。
メタスキルのエントリに許すのは `status` を `excluded_meta` / `rejected` にする更新
だけ (記録と却下は除外と矛盾しない)。

### 典型的な流れ

```bash
LEDGER="uv run python3 skills/skill-improver/scripts/ledger.py"
$LEDGER list --status pr_open --json                   # Step 0: 未決着の PR を突き合わせる
$LEDGER check-target ci-self-heal                      # exit 2 ならここで終了
$LEDGER add --source agent-feedback --target ci-self-heal --lever skill-edit \
  --finding "..." --evidence "https://.../pull/123#issuecomment-1"
$LEDGER record-metrics --id IMP-20260910-4ce5643613 --phase before --metric ci_fix_iterations=6
$LEDGER link-pr --id IMP-20260910-4ce5643613 --pr https://.../pull/130
$LEDGER set-status --id IMP-20260910-4ce5643613 --status merged
$LEDGER record-metrics --id IMP-20260910-4ce5643613 --phase after --metric ci_fix_iterations=3
$LEDGER report                                         # 再発と revert candidate を確認
```

## ブランチの台帳差分を検査する (`verify-diff`)

workflow の allow-list は `improvements/ledger.jsonl` を**ファイル単位**で許す。
それだけだと「自分の 1 行を足すついでに、他の行 (別 skill の `merged` 記録など) を
書き換える」経路が残るので、**行の粒度でも検査する**。判定は id をキーにした
added / removed / modified の 3 分類で行い、モードごとに許す形が違う:

| モード | 許す差分 |
|---|---|
| `candidate` (改善ブランチ) | **追加 1 行だけ**。削除・既存行の変更は不可。追加行は `--ledger-id` と一致する id を持ち、`status` が `proposed`、`pr` が `null` であること |
| 両モード共通 | **base / head のどちらかに id の重複があれば不合格**。重複があると id をキーにした差分計算が 2 行目以降を落とすので、同じ id を 2 行書くだけで「追加は 1 行だけ」の検査をすり抜けられる。base 側の重複は台帳自体が壊れている状態なのでこちらも通さない |
| `reconcile` (突き合わせブランチ) | 削除は不可。既存行で変えてよいのは `status` / `pr` / `after` / `notes` だけで、`status` の遷移は `pr_open → merged`\|`rejected`、`merged → reverted`、`proposed` (pr 未設定) `→ pr_open`\|`rejected` (修復経路) のみ。追加行があれば `proposed` の形であること |

`verify` job は **base (default branch) 側の `ledger.py`** でこの検査を実行する —
候補ブランチのコピーを使ったら検査にならないため。違反があれば内容を並べて
exit 1 し、そのブランチは PR にならない。

## revert candidate の扱い

`report` は before / after 双方に値がある指標を比較し、1 つでも悪化していれば
その エントリを revert candidate として並べる。**対象は `status: merged` のものだけ** —
`rejected` / `reverted` は既に取り消し済みで、並べ続けると毎回無効な revert を要求する
ことになる (delta 自体は status を問わず表示する。観測結果は隠さない)。
悪化を見つけたら、追加の改善を重ねる
前に **その差分の revert PR を提案する** (`retro` の roll-back 規律と同じ)。revert したら
`set-status --status reverted` にして、同じ finding が次に来たときに `recurrence` が
「1 度失敗している」ことを伝えるようにする。
