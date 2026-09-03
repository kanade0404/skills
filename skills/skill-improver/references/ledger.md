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

取り出し先のファイル名には **PR 番号**を使う。ブランチ名 (`improve/...`) には `/` が
入るので、`"$tmp/<branch>.jsonl"` はその名前の親ディレクトリへのリダイレクトになり、
`mktemp -d` の直下にそんなディレクトリは無い — 台帳を 1 件も読めないまま「pending
無し」と読んでしまう:

```bash
gh api "repos/<owner>/<repo>/contents/improvements/ledger.jsonl?ref=<branch>" \
  --jq '.content' | base64 -d > "$tmp/pr-<PR 番号>.jsonl"
uv run python3 skills/skill-improver/scripts/ledger.py list --ledger "$tmp/pr-<PR 番号>.jsonl" --json
```

ただし **head ブランチの台帳は default branch の履歴を丸ごと含む**。そこに載っている
という理由だけで pending 扱いにすると、過去に `merged` / `rejected` / `reverted` に
なった行が「対応済み」として効いてしまい、同じ skill で本当に再発した finding を
握り潰す。pending と数えるのは次の 2 つだけ:

| 行の状態 | 意味 | pending か |
|---|---|---|
| `status == "pr_open"` かつ `pr` が入っている | その PR が現に開いている | **pending** |
| `status == "pr_open"` かつ `pr` が空 | 辿れる PR が無い**壊れた行** | pending ではない (Step 0 の修復対象) |
| `status == "proposed"` かつ `pr` が入っている | `link-pr` は通ったが `set-status` の前に落ちた過渡状態 (Step 0 の回収の途中)。**PR は実在する** | **pending** (二重起票しない) |
| `status == "proposed"` かつ `pr == null` | まだ PR が無い | pending ではない (Step 0 の修復対象) |
| `merged` / `rejected` / `reverted` | 決着済みの過去の記録 | pending ではない (本当の再発を抑止しない) |

判定は `ledger.py` の `is_pending_row()` が持つ。この条件を満たす行と `target_skill` × finding クラスが一致する finding は
**処理済み (pending)** であり、新規候補にしない。これをしないと、レビュー待ちの
PR がある間じゅう同じ finding で PR を出し続ける。

**`proposed` かつ `pr` が入っている行**は、Step 0 が **PR の状態で決着させる**:
open なら `set-status pr_open`、merged なら `merged`、closed (未 merge) なら
`rejected` にする。放っておくと過渡状態のまま残り続ける。

`proposed` かつ `pr == null` の行は pending ではなく **修復対象**である。workflow
モードでは **PR を起票した実行そのものがこの形を残す**: `improve/**` は作成後に
ruleset で凍結されるため `publish` は `link-pr` の commit を積めず、紐付けは次回
実行の Step 0 に回る (`references/scheduling.md`)。だからこれは異常ではなく通常の
入口であり、新しい候補として拾い直さずに head branch から PR を回収する。

`proposed` のまま `pr` が空の行は `pr_open` の列挙に出てこないので、Step 0 は
`list --status proposed` も併せて回す — そうしないと、起票済みなのに紐付いていない
行がどの列挙からも漏れて回収不能になる。

#### 回収は head branch の PR を **全状態で** 引く

`pr` を持たない `proposed` も、`pr` が辿れない行も、回収の入口は同じ:
`improve/<target_skill>-<id>-<run id>` という head branch の PR を引く。ブランチ名が
finding の id を含むので機械的に引ける — ただし **run ごとに suffix が変わる**ので、
完全一致ではなく**接頭辞** `improve/<target_skill>-<id>-` で引く。
**`--state open` で引いてはいけない**:

- **closed の PR** は列挙から丸ごと落ち、行は `proposed` のまま残る
- **merged の PR** はもっと悪い。default branch に `proposed` / `pr == null` の行が
  残り、`--missing-after` は `merged` の行しか見ないので拾えず、突き合わせでも
  「PR を名指しできない」ため決着させられない

どちらもその finding を Step 2.5 の pending 判定から外しはしないので (`pr` が
無い行は pending ではない) 再提案自体は起きうるが、**同じ finding の PR が既に
merge 済み**なのに台帳がそれを知らないまま重複起票する — 台帳が改善の履歴として
使えなくなる。したがって:

列挙に **`gh pr list` は使わない**。`--limit` は取得件数の上限で、超えた分は黙って
落ちる (既定 30 件) — この節が塞ごうとしている「実在する PR の見落とし」を
列挙自体が作ってしまう。REST の pulls を `--paginate` で全ページ取り切る:

```bash
gh api --paginate "repos/<owner>/<repo>/pulls?state=all&per_page=100" \
  --jq '.[] | {number, state, merged_at, url: .html_url, head_ref: .head.ref}' \
  | jq -s --arg p "improve/<target_skill>-<id>-" \
       '[.[] | select(.head_ref | startswith($p))] | sort_by(.number) | reverse'
```

**作者で絞る必要は無い** — `improve/**` を作れるのは workflow の GitHub App だけ
(ruleset A) なので、この接頭辞の head branch を持つ PR はすべてこのループのものである。

新しい順に最初の 1 件で決着させる — open は `link-pr`、merged は
`link-pr --keep-status` → `set-status --status merged`、closed (未 merge) は
`link-pr --keep-status` → `set-status --status rejected --notes "<URL> は merge
されずに閉じられた"`。1 件も見つからなければ `proposed` のまま残す (finding を
出し直せる状態にして次回に回す)。

#### 不変条件: `pr_open` は必ず `pr` を持つ

`pr_open` は「追える PR がある」ことを意味する status であり、`pr` が空のまま
この status になった行は**どちらの経路からも動かせない**: `is_pending_row()` は開ける
PR URL を持つ行しか pending にしないのでこの行は pending にならず、Step 0 にも決着
させる URL が無い。結果としてその行は永久に `pr_open` のまま残り、**実在する PR を
見落として同じ finding に二重で PR が立つ**。

そこで書き込み側で塞ぐ。`add --status pr_open` は `--pr` を必須にし、
`set-status --status pr_open` は行に `pr` があるか `--pr` が渡されたときだけ通す。
さらに **status を問わず、保存する `pr` は `https://.../pull/<番号>` の形であること**を
`add` / `set-status` / `link-pr` の全経路で検査する — 形の検査を `pr_open` にだけ
掛けると、`proposed` + ブランチ名のような「開けない値を指す pending 行」が同じ袋小路を
作る。`is_pending_row()` が pending と数えるのも `pr` がその形をしている行だけ。

それでも過去の実行や手編集が残した行は入りうるので、`list --inconsistent` で
列挙できるようにしてある (判定は `is_inconsistent_row()`)。拾うのは **`pr_open` なのに
`pr` が空の行**と、**status を問わず `pr` が空でないのに PR URL の形をしていない行**の
2 つ。Step 0 の修復はどちらも同じで、上の**全状態の head branch 検索**を通る:

1. `improve/<target_skill>-<id>-` を接頭辞に持つ head branch の PR を
   `--state all` で引く
2. 見つかれば `link-pr` して紐付けを完成させ、その PR の状態
   (open / merged / closed) で決着させる
3. 1 件も見つからなければ `set-status --status proposed --clear-pr --notes "..."` で
   `pr` の無い `proposed` に戻し、その finding を**出し直せる**状態にする

3 の遷移 (`pr_open → proposed`) は `verify-diff --mode reconcile` でも、
**base 側の行が辿れない場合 (`pr` が空、または PR URL の形をしていない) に限って**
許す。開ける PR URL を持つ `pr_open` を `proposed` に戻すのは、出した PR を
「無かったこと」にする書き換えなので通さない。reconcile モードは併せて、**触った行に
開けない `pr` を残すこと**も、**開ける `pr` を空に戻すこと**も拒否する — `pr` を
消せる唯一の経路がこの 3 の修復 (base 側が辿れない `pr_open → proposed`) である。

書き込み側では `set-status --status pr_open --clear-pr` も拒否する。片方ずつは
正当な操作 (`--clear-pr` は 3 の修復経路、`--status pr_open` は 2 の決着) だが、
組み合わせると `pr` の無い `pr_open` — この節が塞いでいる形そのもの — を作るため。

workflow 側はこの不変条件を「書かないこと」で守る: `publish` は台帳に一切書かない
(凍結されたブランチに commit を積めない) ので、`pr` の無い `pr_open` も、開けない
`pr` を持つ行も作らない。起票した PR は `proposed` / `pr == null` の行と、head
branch の接頭辞という 2 つの手掛かりで次回の Step 0 から辿れる。**起票後に PR の
head が検証済み SHA と違っていた場合**は ruleset が外れている疑いなので、台帳には
何も書かず PR を閉じて run を赤にする (`references/scheduling.md`)。

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
| `status` | enum | `proposed` / `pr_open` / `merged` / `rejected` / `excluded_meta` / `reverted`。`proposed` かつ `pr` が入っている行は**過渡状態** — PR は作られたが `set-status` の前に落ちた残骸で、「PR が実在する finding」として扱う (下記) |
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
| `add --source --target --finding --lever [--class] [--evidence ...] [--status] [--pr] [--notes] [--id] [--created]` | finding を 1 件記録。`recurrence` を自動計算し、`id` を内容から決める。`--class` は再発クラスキーの明示。`--id` は形式 (`IMP-YYYYMMDD-xxxxxxxxxx`) と重複を検査する。対象がメタスキルなら `--status` を無視して `excluded_meta` で記録し、**exit 2** を返す。`--status pr_open` には `--pr` が要る |
| `set-status --id --status [--pr] [--clear-pr] [--notes]` | status を更新。`--status pr_open` は行に `pr` があるか `--pr` を渡したときだけ通る (辿れない `pr_open` を作らない)。`--pr` は status を問わず形を検査する。`--clear-pr` は Step 0 の修復経路が使う — 辿れない `pr` を空に戻す。`--pr` との併用と、**`--status pr_open` との併用**は拒否する (後者は `pr` の無い `pr_open` を作る) |
| `link-pr --id --pr [--keep-status]` | PR URL を紐付け、既定で `status=pr_open` にする。`--pr` は `https://.../pull/<番号>` の形であること。`--keep-status` は Step 0 の回収が使う — `proposed` のまま `pr` だけ入れて「PR は実在する」ことを先に記録する |
| `record-metrics --id --phase before\|after --metric KEY=VALUE [--metric ...]` | 指標を記録。両相が揃うと delta を表示する。非有限値 (`nan` / `inf`) は拒否 |
| `list [--status ...] [--skill] [--missing-after] [--inconsistent] [--json]` | エントリを絞って列挙。Step 0 の突き合わせは `--status pr_open`、`--missing-after` (merged なのに `after` が空)、`--status proposed` (PR に紐付いていない = 修復対象)、`--inconsistent` (`pr_open` なのに `pr` が空、または `pr` が PR URL の形をしていない = 壊れた行) の 4 本を起点にする |
| `report [--skill] [--json] [--fail-on-revert]` | skill 別の件数・**再発クラスキーとその件数**・status 内訳、before→after の delta、**merged without after metrics**、**revert candidate** を出力 |
| `verify-diff --head <file> [--base <file>] --mode candidate\|reconcile [--ledger-id ID] [--pr-index FILE]` | ブランチの台帳差分が許された変更だけかを検査する (workflow の `verify` job 用)。`--pr-index` は信用できる PR index との照合 (下記) |
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
| `reconcile` (突き合わせブランチ) | 削除は不可。既存行で変えてよいのは `status` / `pr` / `after` / `notes` だけで、`status` の遷移は `pr_open → merged`\|`rejected`、`merged → reverted`、`proposed → pr_open`\|`merged`\|`rejected`、`pr_open` (pr が辿れない) `→ proposed` (辿れない行を出し直す修復経路) のみ。**`pr_open` / `merged` / `rejected` を名乗る行は、入る時も留まる時も head 側に開ける PR URL を持つこと** (index があれば実在と状態まで照合する)。base 側の `pr` は空でも入っていてもよい — どちらも起票済みで紐付いていない行で、Step 0 が head branch の PR を接頭辞 + 全状態で引いて回収する経路。**開ける `pr` を空に戻せるのは修復経路 (`pr_open → proposed` かつ base 側の `pr` が空 / 開けない) だけ**で、それ以外の行から `pr` を消すことはできない。追加行があれば `proposed` の形であること |

`verify` job は **base (default branch) 側の `ledger.py`** でこの検査を実行する —
候補ブランチのコピーを使ったら検査にならないため。違反があれば内容を並べて
exit 1 し、そのブランチは PR にならない。

### `pr` は形だけでなく **実在まで**照合する (`--pr-index`)

上の表の `reconcile` は「head 側に開ける PR URL があること」を求めるが、それは
**URL の形**の検査でしかない。`verify` は PR API を引き直さないので、形の検査だけだと
**別リポジトリの / 無関係な PR** を指す整った URL を書くだけで `proposed` →
`merged` / `rejected` に進められる — 台帳の決着は「その PR が本当にそうなった」ことの
記録なので、これは突き合わせではなく捏造になる。

`pr_open` / `merged` / `rejected` に **PR URL を必須にしているのはこの照合を外させない
ため**でもある。`pr` は `reconcile` で変更してよい項目なので、URL を空にできると
`pr_open → merged` / `rejected` のついでに index との突き合わせごと消え、**実際には
開いたままの PR を決着済みとして畳める**。空の `pr` は「照合できない」であって
「照合に通った」ではない。

そこで `verify-diff` は `--pr-index <file>` を受け、**触った行の `pr` を信用できる
index と突き合わせる**。index を作るのは workflow の `stage` job — 候補コードを 1 行も
動かさない runner で、job の `GITHUB_TOKEN` (`pull-requests: read`) を使って
`gh api --paginate` でこのリポジトリの `improve/*` PR を全件取り、manifest artifact に
載せて `verify` に渡す:

```bash
gh api --paginate "repos/<owner>/<repo>/pulls?state=all&per_page=100" \
  --jq '.[] | select(.head.ref | startswith("improve/"))
        | {number, html_url, head_ref: .head.ref, state, merged_at}' \
  | jq -s '.' > pr-index.json
```

index があるとき、**`pr` が入っている触った行**は 3 つを全て満たさなければならない
(1 つでも外れれば差分ごと不合格):

| 条件 | 何を塞ぐか |
|---|---|
| その `html_url` が index にある | **別リポジトリ**の PR を指す URL |
| index 側の head ref が `improve/<target_skill>-<id>-` で始まる | 同じ repo の**別 finding / 無関係**な PR を指す URL |
| head 側の status が PR の状態と一致する (`pr_open` ⇔ open、`merged` ⇔ merged、`rejected` ⇔ merge されずに closed) | まだ open な PR を `merged` として決着させる書き換え |

`proposed` (`link-pr --keep-status` の途中状態) と `reverted` (merge 後の取り消し) は
PR の状態から決まらないので、状態の一致だけ検査対象から外す (URL と head ref は見る)。

**workflow モードでは `--pr-index` が必須**で、`verify` は candidate / reconcile の
どちらにも渡す (candidate の追加行は `pr == null` が条件なので照合対象は無いが、
渡し忘れの経路を作らないために同じ扱いにする)。ファイルが読めなければ `ledger.py` は
そこで **exit 1** する — 「index が無いので形だけ検査した」と黙って落ちる経路を作らない
ため。`--pr-index` を省いた実行は許すが、その場合は「`pr` は形しか見ていない」旨を
stderr に警告として出す (対話モードの手元検査用)。

## revert candidate の扱い

`report` は before / after 双方に値がある指標を比較し、1 つでも悪化していれば
その エントリを revert candidate として並べる。**対象は `status: merged` のものだけ** —
`rejected` / `reverted` は既に取り消し済みで、並べ続けると毎回無効な revert を要求する
ことになる (delta 自体は status を問わず表示する。観測結果は隠さない)。
悪化を見つけたら、追加の改善を重ねる
前に **その差分の revert PR を提案する** (`retro` の roll-back 規律と同じ)。revert したら
`set-status --status reverted` にして、同じ finding が次に来たときに `recurrence` が
「1 度失敗している」ことを伝えるようにする。
