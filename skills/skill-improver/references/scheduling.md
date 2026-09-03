# 週次実行 (スケジューリング)

改善ループは**常時起動にしない**。finding が出るたびに PR が飛ぶと 1 finding = 1 PR の
規律がレビュー負荷で先に壊れ、before / after の比較期間も取れなくなる。週 1 回まとめて
収集し、その週で最も根拠の強い候補だけを PR にする。

## 経路 1 — GitHub Actions (このリポジトリの既定)

`.github/workflows/skill-improver.yml` が毎週月曜 00:00 UTC (月曜 09:00 JST) に
`anthropics/claude-code-action@v1` でこのスキルを起動する
(`claude.yml` / `claude-code-review.yml` と同じ action・同じ secret
`CLAUDE_CODE_OAUTH_TOKEN` を使う)。`workflow_dispatch` から手動起動もでき、
`focus` 入力で skill 名 / finding id / issue URL に絞れる。

権限は job ごとに分けてあり、agent を動かす `improve` は `contents: read` だけで
**書き込み資格情報を一度も持たない**。書き込み (`contents: write` /
`pull-requests: write`) は `stage` と `publish` の 2 job にだけ置き、どちらも agent が
走った runner とは**別の runner** で個別に発行する (内訳は下の「job の分割」)。
default branch への push と merge は**手順として**行わない — 承認ゲートは PR レビューに
置く。

ただし `contents: write` は improve/* と default branch を区別できない。**手順は
約束であって強制ではない**ので、実効的な保証は default branch 側の設定に置く:

> **必須セットアップ**: default branch に branch ruleset を作り、`GITHUB_TOKEN` /
> GitHub Actions からの push を拒否する (Rulesets → Restrict updates、bypass list に
> Actions を入れない)。これが無い限り、default branch へ push しない保証は
> 「workflow のプロンプトと本スキルの手順がそう書いてある」ことだけになる。

これは**推奨ではなく前提条件**として workflow が検査する (fail-closed)。agent を起動する
前のステップが `gh api repos/{owner}/{repo}/rulesets` を読み、default branch
(`~DEFAULT_BRANCH` または `refs/heads/<default>`) を対象とする ruleset のうち

- `enforcement` が `active`
- `pull_request` または `update` のルールを持つ
- `bypass_actors` に `Integration` (= Actions / GitHub App) を含まない

ものが 1 つも無ければ、そこで `exit 1` して agent を走らせません。**現在この
リポジトリには該当する ruleset が無いため、この workflow は ruleset を作るまで
起動しません** (PR の Follow-ups に挙げてあるとおり、repo 設定の変更はコード PR の
スコープ外)。

ブランチの作成と PR 起票は専用の GitHub App が行う (下の「実行アイデンティティ」節)。
これは任意の強化ではなく**前提条件**で、App と `improve/**` の ruleset **2 本**
(作成を App に絞る A、作成後の更新・削除を誰にも許さない B) が揃うまで workflow は
起動しない。

この job は書き込み資格情報を持ったまま、第三者が書ける可能性のあるテキスト
(`agent-feedback` ラベルの issue / PR コメント) を読む。そのため:

- **読む対象を絞る**: ラベルが付いた item の、author association が
  `OWNER` / `MEMBER` / `COLLABORATOR` のコメントだけ (`references/feedback-intake.md`)
- **道具を絞る**: workflow の `claude_args` で `--allowed-tools` を allow-list にし、
  `--disallowed-tools` で default branch への直 push と merge を落とす。これは
  injection が通ったときの被害範囲を狭める浅いガードで、保証は上の ruleset 側
- **action は full commit SHA で固定する**: 可変タグのままだと、差し替えられた
  action が `CLAUDE_CODE_OAUTH_TOKEN` と `contents: write` ごと持っていける

### 資格情報は agent のプロセスに残る — 関門は「公開の直前」に置く

**`CLAUDE_CODE_OAUTH_TOKEN` は agent のプロセスから取り除けない**。`claude-code-action`
はその値で API を呼ぶので、agent の環境変数として存在することは前提条件であって、
設定で消せる類のものではない。`--allowed-tools` には `Bash(python3 *)` が要る
(`ledger.py` と Step 5 の検査を回すため) 以上、`os.environ` からでも
`/proc/self/environ` からでも読める。`--disallowed-tools` の
`Read(//proc/**)` / `Bash(printenv*)` / `Bash(env*)` は被害を面倒にするだけの
**浅いガードで、境界ではない**。

そして**パスの allow-list は値を見ない**。`skills/<skill>/**` や
`improvements/ledger.jsonl` の中に資格情報を書けば allow-list は通り、push →
`publish` の `gh pr create` で公開 PR に載る。**したがって信用の境界はブランチの
公開 (push) に置く**: **agent が走った runner とは別の runner** で動く `stage` job が、
**push の前に**候補ブランチの差分をシークレットで走査する
(`manifest_guard.py scan-diff`)。

走査するもの:

- **`stage` job が見えるシークレットの実値** — `CLAUDE_CODE_OAUTH_TOKEN`、
  `SKILL_IMPROVER_APP_PRIVATE_KEY`、`stage` 自身が発行した write トークン、`stage` の
  `GITHUB_TOKEN` の 4 つ (`improve` 側のトークンは下記)。それぞれ
  **literal / base64 / base64url / hex** の姿で探し、
  さらに**空白と `\n` `\r` `\t` エスケープを落とした姿**でも突き合わせる
  (折り返して埋め込むだけで一致を外せないため)。秘密鍵は**行単位**でも探す。
  `SKILL_IMPROVER_APP_ID` だけは短い数字列で誤検知にしかならないので対象外
- **値を知らなくても拾う接頭辞** — `sk-ant-`、`ghs_` / `ghp_` / `ghu_` / `gho_`、
  `github_pat_`、PEM 秘密鍵の `BEGIN ... PRIVATE KEY` 行、**run スコープの JWT**
  (base64url 3 節で先頭 2 節が `eyJ` — 捕捉に失敗しても形だけで止まる)
- **対象は差分テキストだけでなく、変更後ファイルの中身丸ごと** (`git show
  <sha>:<path>` の生 blob)。バイナリ扱いのファイルは差分に中身が出ないし、
  生 blob なら textconv / smudge フィルタで隠せない
- **PR 本文ファイル**も同じ規則で走査する (`scan-files`)。OAuth トークンは
  `ghs_` 系の接頭辞に当たらないので、既存の接頭辞 grep だけでは足りない

**`improve` job のトークンは `stage` からは実値で走査できない**。`stage` は別 runner
なので、`improve` の read トークン・preflight トークン・その job の `GITHUB_TOKEN`・
`ACTIONS_RUNTIME_TOKEN` の値をそもそも知らない (同じ値をもう一度発行することもできない)。
**代わりに接頭辞の網でだけ拾う**:

- installation token (read / preflight / `GITHUB_TOKEN`) は `ghs_` で始まるので
  `PREFIX_PATTERNS` の GitHub token パターンが拾う
- **`ACTIONS_RUNTIME_TOKEN`** は artifact / cache API 用に GitHub Actions が
  **JavaScript action の step にだけ**注入する run スコープの JWT で、
  `claude-code-action` は JavaScript action なので agent のプロセスもこれを継承する
  (この workflow 自身、hand-off の upload / download でこのトークンに依存している)。
  値は知りようがないので、**base64url 3 節で先頭 2 節が `eyJ` という「形」**で拾う
  (`PREFIX_PATTERNS` の run-scoped JWT パターン)

以前は agent の実行後に `actions/github-script` を 1 つ走らせて `process.env` から
`ACTIONS_RUNTIME_TOKEN` を捕まえ、0600 のファイル経由で走査 step に渡していた。
その受け渡し自体が「**agent と同じ job の後段 step は trusted**」という成立しない前提の
上に載っていたので (次節)、実値の走査ではなく形の網に落とした。**この 1 点は実値走査より
弱い**ことを明記しておく — 分割・符号化して埋め込まれた JWT はこの網では止まらない。

運用上の規律:

- シークレットの値は**環境変数名で渡す** (コマンドラインは他プロセスから見える)。
  検出しても**値も一致箇所も出力しない** — ログと summary に出るのはパスと
  「どの名前のシークレットか」だけ。`manifest_guard.py` には 0600 ファイル経由の
  `--secret-file` も残っているが、**workflow からは使っていない**
- 検査スクリプトは `stage` / `publish` の working tree のものをそのまま使う。どちらの
  job も**候補ブランチを checkout しない**ので、working tree は run を起動した commit
  (`$GITHUB_SHA`) の内容そのもの — `git show $GITHUB_SHA:...` で取り出し直す必要が無い
- 走査できる値が 1 つも組み立てられなければ**走査しなかったものとして落とす**
  (fail-closed)。差分が読めない場合も同じ
- 当たった候補は push しない。manifest からも落ちるので `verify` / `publish` には
  渡らず、**他の候補はそのまま進む**。件数は job output `secret_hit` に出し、
  `publish` の最終 step が起票を終えてからその run を赤にする

**残余リスク (捕まえられないもの)**: 見ているのは実値とその base64 / hex、そして
接頭辞だけである。値を分割して別の符号に変える、圧縮・暗号化する、`Bash(python3 *)`
から外部へ送る、といった経路は**この走査では捕まらない**。したがって:

> この workflow に渡す資格情報は、**この用途専用で、失効させられて、上限を掛けられる
> もの**にすること。個人の長期トークンを流用しない。`secret_hit` で run が赤くなったら、
> ブランチの調査より先に該当の資格情報をローテーションする。

### agent と同じ job に「後段の trusted step」は置けない

以前この workflow は、agent の step の**後ろに**「trusted step」を並べていた
(`ACTIONS_RUNTIME_TOKEN` の捕捉、write トークンの発行、差分の走査、push、manifest の
sanitize、artifact の staging)。**同じ job である以上、この前提は成立しない**:

- 同じ job の step は**同じ runner・同じ UID・同じファイルシステム**で走る
- agent は終了する前にバックグラウンドプロセスを残せる。そのプロセスは後続 step が
  走っている間ずっと生きている
- そこからは **0600 のトークンファイルが読める** (同一 UID に mode は効かない)、
  **兄弟プロセスの `/proc/<pid>/environ` が読める** (= agent の**後**に発行した write
  トークンが読める)、**`$GITHUB_OUTPUT` / `$GITHUB_ENV` に追記できる**、**走査と push
  の間にローカル ref を書き換えられる**

runner 上でプロセスを殺しに行く対処は取らない (取りこぼしを前提にできない)。代わりに
**agent が走った runner の出力は、artifact も `$GITHUB_OUTPUT` も含めて全て
untrusted** と決め、資格情報の扱い・走査・push を**別 job (`stage`) = 別 runner** に
移した。`improve` job に残るのは 3 つだけ:

1. agent より**前**の preflight (ruleset 検査。admin トークンは agent が走り出す前の
   step でしか使わない)
2. agent の実行
3. agent の成果を小包にまとめて上げるだけの、**untrusted な hand-off step** 1 つ

hand-off は `$RUNNER_TEMP/handoff` に作り、`skill-improver-handoff` という名前の
artifact として上げる。中身は (a) `git bundle create` した候補ブランチ
(`refs/heads/improve/*` を基底なしで丸ごと)、(b) agent が書いた manifest、
(c) PR 本文ファイル、の 3 つ。**この artifact の中身は全て untrusted** として
`stage` が検証する。`improve` job は **job output も持たない** — `$GITHUB_OUTPUT` に
書ける主体が agent 側に居る以上、matrix の添字も push の結果もそこからは取らない。

### improve/* PR には repo の CI が来ない — だから検証してから起票する

**`GITHUB_TOKEN` が作成・更新した PR の `pull_request` run (`opened` / `synchronize` /
`reopened`) は approval-required 状態で作成され、write 権限者が「Approve workflows to
run」を押すまで走らない** ([GITHUB_TOKEN のドキュメント](https://docs.github.com/en/actions/concepts/security/github_token))。
そのため trigger-evals / rulesync drift / unittest の結果は、人間が承認するまで
レビュアーに見えない。workflow 内の事前検証は、その承認前に結果を見せるための代替である。

そこで **workflow モードでは agent は push も PR 起票もしない**。責務は 4 段に分かれる:

1. **agent (`improve` job)** — 改善ブランチを切って**ローカルに commit**し、環境変数
   `MANIFEST` のファイルに 1 行 1 JSON
   (`{"branch":..., "title":..., "body_file":..., "ledger_id":...}`) を追記する。
   持っているのは読み取り専用トークンだけで、`gh pr create` も allow-list の外
2. **hand-off step (`improve` job、agent の実行後 — trusted ではない)** — ローカルの
   `refs/heads/improve/*` を `git bundle` にまとめ、manifest と PR 本文ファイルを添えて
   `skill-improver-handoff` artifact に上げるだけ。**資格情報は扱わず、検証もしない**
   (この step 自体が agent の残したプロセスと同居しているため)
3. **`stage` job (別 runner。ここが信用の境界)** — hand-off を落として
   `git bundle verify` → ref 名を検証して `refs/candidates/<name>` に取り込む →
   `manifest_guard.py sanitize` で manifest を組み直す (許可したキー
   `branch` / `head_sha` / `body_file` / `ledger_id` / `title` だけを残し、**全ての
   文字列フィールド**について長さ・制御文字・トークン様文字列を検査する。agent は
   自分の `GH_TOKEN` を読めるので `title` に入れられると `gh pr create --title` で
   公開される) → **manifest の `branch` と bundle の ref が 1 対 1 で対応すること**を
   確かめる (片側にしか無いものが 1 件でもあれば manifest 全体を落とす: fail closed) →
   PR 本文ファイルを 1 件ずつ検証 (bodies/ 直下の通常ファイル、symlink でない、
   hard link でない、64 KiB 以下) して `scan-files` に掛ける → **パスの allow-list
   ゲート** → **差分のシークレット走査** → 通った候補だけを push する。
   **`head_sha` は push した ref の SHA** (agent の申告値は使わない)。書き込み
   トークンはこの job で発行するので、**agent が走った runner には最初から最後まで
   存在しない**。artifact (`skill-improver-manifest`) に載るのも、検証を通った本文を
   新しく作った空のディレクトリ (`mktemp -d`) にコピーしたものだけである
4. **`verify` → `publish`** — `verify` が manifest の値・台帳差分・パスの allow-list を
   検査し、その SHA でテストを回す。通ったものだけ `publish` が `gh pr create` する。
   **`publish` は台帳に書かない** (ブランチは凍結済み) — `link-pr` は次回実行の
   Step 0 が行う

### job の分割 (権限と信頼の分離)

候補ブランチの中身は agent が書いたコードであり、そのまま書き込み資格情報のある場所で
実行すると、テストや検査スクリプトを書き換えたブランチが資格情報に手を伸ばせる。さらに
**agent 自身も、同じ job に並べた後段 step には手が届く** (前節)。そのため job を
5 つに割り、**agent を動かす job とブランチを実行する job の双方から資格情報を外し、
どちらの出力も信用しない**:

| job | 権限 | 持つ資格情報 | 役割 |
|---|---|---|---|
| `improve` | **`contents: read` のみ** | App の read トークン、App の preflight トークン (agent より**前**の step だけ)、`CLAUDE_CODE_OAUTH_TOKEN`、この job の `ACTIONS_RUNTIME_TOKEN`。**書き込みトークンは一切持たない** | ruleset の preflight、agent 実行。実行後は untrusted な hand-off artifact を上げるだけで、**job output も持たない** |
| `stage` (**信用の境界**) | `contents: read` (job の `GITHUB_TOKEN`) + この job で発行する App の **write** トークン | App の秘密鍵、`CLAUDE_CODE_OAUTH_TOKEN` (走査用)、自分で発行した write トークン。**`improve` 側のトークンは知らない** | hand-off の取り込み (`git bundle verify` → `refs/candidates/*`)、manifest の組み直しと bundle との突き合わせ、本文の検証と走査、**パスの allow-list ゲート**、**差分のシークレット走査**、通った候補の push、検証済み manifest の artifact 化。**候補コードは実行しない** |
| `verify` (**候補 1 件につき 1 job**) | `contents: read` のみ (`persist-credentials: false`、`GH_TOKEN` もシークレットも渡さない) | **無し** | manifest の値の検証、パスの allow-list ゲート、**台帳差分のゲート (`ledger.py verify-diff`)**、メタスキル対象の拒否、ブランチ上で `unittest` / `check_trigger_evals.py` / `rulesync-sync.mjs --check`。**artifact は上げない** |
| `collect` | `actions: read` のみ | この job の `GITHUB_TOKEN` (jobs API の読み取りだけ) | 各 `verify (<idx>)` の conclusion と stage の manifest から合格記録を組み立てて artifact に上げる。候補コードは動かさない |
| `publish` | `contents: read` / `pull-requests: write` | App の PR トークン (この job で発行。**contents は read**) | 合格記録にある候補の `gh pr create` と起票後の head 照合。**push も台帳の書き込みもしない** — `improve/**` は作成後に凍結されていて commit を積めないので、この job から改善ブランチを動かす能力そのものを外してある |

`improve` の job 権限が `contents: read` だけなのは、`claude-code-action` が
`process.env` をそのまま SDK に渡す = **job の `GITHUB_TOKEN` は agent の環境に届く**
ため。書き込み能力はこの job には (`GITHUB_TOKEN` にも App トークンにも) 一切置かず、
**別 runner で走る `stage`** にだけ置く (`publish` は `contents: read` で足りる)。

**なぜ verify を 1 件 1 job にするか**: 「候補コードを動かした runner が、そのまま
合格記録も書く」構造は成立しない。候補コードは runner 上で任意に動けるので、落ちた
別の `improve/*` ブランチを指す「合格した行」を書き足せてしまい、`publish` からは
区別が付かない。artifact も同じ理由で信用できない (`ACTIONS_RUNTIME_TOKEN` があれば
runner 上の任意のコードが artifact を上げられる)。そこで **verify の信頼できる出力を
job の conclusion 1 ビットだけに絞り**、合格記録の組み立ては候補コードを動かさない
`collect` が行う。`collect` が読むのは (1) 各 `verify (<idx>)` の conclusion
(`actions: read` で jobs API から取得)、(2) **候補コードが 1 行も動く前に** `stage` が
上げた manifest — の 2 つだけで、PR 本文も後者から取る。

push できなかった候補があっても `stage` はそこで落とさない (落とすと後続 job が
まるごと skip され、push できた候補まで検証・起票されなくなる)。件数を job output
`push_failed` に出し、**`publish` の最終 step が起票を終えてからその run を赤にする**。

`verify` は候補が 1 つでも落ちれば赤くなる (その可視性は保つ) が、**通った候補は
起票する** — `collect` と `publish` は `always()` を含む条件で回し、cancelled では
走らせない (状態関数を含まない `if` には暗黙の `success()` が掛かるため `always()` が
要る)。候補が 0 件なら `verify` の matrix は空で job ごと skip され、`collect` が空の
合格記録を出して `publish` が「起票なし」で正常終了する。

`verify` は `fetch-depth: 0` の checkout で全 remote head をローカルに取り込むため、
job 中に追加のネットワークアクセス (= 資格情報) が要らない。`stage` と `publish` は
書き込み権限を持つが**ブランチのコードを実行しない** — `stage` は候補を
`refs/candidates/*` に置くだけで一度も checkout せず (working tree は
`$GITHUB_SHA` のまま)、`publish` の `ledger.py` は候補 checkout 前に default branch 側の
コピーを `$RUNNER_TEMP` へ退避して、そちらを使う。

**台帳差分のゲート**: パスの allow-list は `improvements/ledger.jsonl` を**ファイル
単位**で許すため、行の粒度でも検査する。`verify` は base 側の `ledger.py` (候補
ブランチのコピーではない) で `verify-diff` を実行し、改善ブランチには「自分の 1 行の
追加だけ」、突き合わせブランチには「決着した行の `status` / `pr` / `after` / `notes`
を許された遷移で進めるだけ」を要求する (詳細は `references/ledger.md`)。

**パスの allow-list ゲート**: 同じ検査を `stage` (push の前) と `verify` (実行の前)
の**両方**が持つ。allow-list を外れるブランチは `stage` がそもそも push しないので
公開もされない。それぞれ
`git diff --name-only origin/<default>...<候補の SHA>` を取り、
`skills/<target_skill>/**` / `.claude/skills/<target_skill>/**` /
`.agents/skills/<target_skill>/**` / `improvements/ledger.jsonl` の範囲に収まっているかを
確かめる (reconcile ブランチは `improvements/ledger.jsonl` のみ)。外れるパスが 1 つでも
あればそのブランチは実行せず、PR にもしない。

**manifest の値は agent が書いたデータ**として扱い、特権的に使う前に検証する:
`branch` は `^improve/[A-Za-z0-9._-]+$` かつ **末尾がこの run の id** で origin に実在すること (形は `manifest_guard.py`、run id との一致は `stage` が push の直前に見る)、`head_sha` は
`^[0-9a-f]{40}$` であること、`body_file` は `bodies/` 直下の通常ファイル
(symlink・`..`・別ディレクトリは不可) であること、`ledger_id` は
`^IMP-[0-9]{8}-[0-9a-f]{10}$` かつそのブランチの台帳に `proposed` / `pr == null` の
行として実在すること。`publish` は artifact 経由で受け取った値を自分でもう一度検証する。
落ちた行は PR にならず job summary に出て job が赤になる (ブランチは調査用に残る)。

**検証も起票もブランチ名ではなく `head_sha` を対象にする**。`stage` の push step が
push した ref の SHA を manifest に書き、`verify` は `origin/<branch>` が
その SHA と一致することを確かめてから **SHA を checkout** して allow-list ゲートと
検査を回す。`publish` も `gh pr create` の直前に
`git ls-remote origin refs/heads/<branch>` を 1 度読むが、これは**安い早期終了**で
あって保証ではない (保証は下の ruleset)。ブランチ名で追い続けると、検証と起票の間に
押された commit が「検証済み」として PR に載る (TOCTOU)。

#### 検証済み SHA の不変条件は「ブランチを不変にする」ことで守る

**GitHub の PR head は必ず可変な branch である**。`gh pr create --head` に tag や
commit SHA のような不変 ref を渡すことはできず、PR は常に「そのブランチの現在の
先端」を指し続ける。しかも `git ls-remote` / `gh pr create` / `gh pr view` は 3 つの
別々の API 呼び出しで、その隙間を GitHub 側で原子化する手段は無い。つまり
**workflow の中には、検証した SHA を起票まで不変に保つ仕組みを置けない**。
「起票の直前に照合する」も「起票の後に照合して補償する」も、窓を狭めるだけで
閉じはしない (実際、以前はここに復旧 → 台帳 → close の補償一式を置いていた)。

そこで**照合をやめてブランチ自体を不変にする**。保証は repo 側の ruleset 2 本に
置き、preflight が両方を fail-closed で検査する:

| ruleset | 対象 | ルール | bypass actors | 効果 |
|---|---|---|---|---|
| **A (create-only)** | `refs/heads/improve/**` | `creation` | この App 1 件だけ (`Integration` / `actor_id` = App ID / `bypass_mode: always`) | `improve/**` を**作れる**のは workflow の App だけ |
| **B (freeze)** | `refs/heads/improve/**` | `update` と `deletion` | **空 (1 件も入れない)** | 作成後の `improve/**` は**誰も動かせない** — App も、App の鍵を持つ運用者も、repo admin も |

**ruleset は bypass actor に載っていない限り admin にも効く**。だから B に
bypass を 1 つも置かないことが要点で、ここに App を入れた瞬間に「App の鍵を持つ
運用者が `ls-remote` と `gh pr create` の間でブランチを差し替える」経路が戻る。
**PR 起票専用の別アイデンティティを立てるだけでは足りない**のも同じ理由で、鍵の
持ち主は「起票できない別の身元」ではなく「ブランチを更新できる身元」を使えばよく、
更新の能力が誰かに残っている限り窓は閉じない。閉じられるのは、更新の能力を
**全員から**取り上げたときだけである。

その結果、`stage` が push した瞬間から `head_sha` は不変になり、**publish 側の補償は
不要になった**。`publish` に残るのは起票直後の `gh pr view --json headRefOid` 1 回
だけで、これは補償の入口ではなく **ruleset が外れていないかの検出器**である
(一致しなければ PR を閉じ、job summary に「手動対応が必要 (unverified PR)」を書き、
`head_moved` output で run を赤にする)。`publish` は force push もしなければ台帳も
書かないので、`contents` は `read` で足りる。

**残余リスクは ruleset を編集できる者だけ**である。ruleset そのものの編集権限が
この設計のトラストルートで、そこは workflow の側から縛れない (スコープ外)。
B を外した / bypass を足した痕跡は repo の監査ログに残り、外れた状態で走らせれば
preflight が `exit 1` して agent すら起動しない。

##### 凍結の代償 (1) ブランチ名を run ごとに一意にする

更新も削除もできないということは、**同じ名前を作り直せない**ということでもある。
そこでブランチ名に run id を入れて 1 run 1 ブランチにする:

| ブランチ | 名前 |
|---|---|
| 改善候補 | `improve/<skill>-<finding-id>-<github.run_id>` |
| 突き合わせ | `improve/ledger-reconcile-<github.run_id>` |

`stage` は push の直前に **branch 名の末尾が `-$GITHUB_RUN_ID` であること**を要求し、
違えばその候補を push せず落とす (`push_failed` として run が赤くなる)。黙って
rename しないのは、manifest / PR 本文 / 台帳が指す名前と実際の ref が食い違う方が
後から追えなくなるためである。push 自体も **create-only** で行う
(`git push --force-with-lease="refs/heads/<branch>:" ...` — 空の期待値は「その ref が
存在しないこと」を意味する) ので、同名の ref が既にあれば手元で落ちる。push の直後に
`git ls-remote` で `head_sha` と一致することも確かめる (ここで一致していれば、以後
ruleset B が動かさせない)。

##### 凍結の代償 (2) PR と台帳の紐付けは次回実行に移る

凍結されたブランチには `link-pr` の commit を積めない。したがって
**`publish` は台帳を一切書かない**: 起票された PR の台帳行は `proposed` /
`pr == null` のまま default branch に残り、紐付けは**次回実行の Step 0** が行う。
Step 0 は head branch の**接頭辞**で PR を引く (ブランチ名が run ごとに変わるため
完全一致では引けない):

```bash
gh pr list --state all --limit 200 \
  --json number,state,mergedAt,url,headRefName \
  | jq --arg p "improve/<target_skill>-<id>-" \
       '[.[] | select(.headRefName | startswith($p))] | sort_by(.number) | reverse'
```

**作者で絞る必要は無い** — `improve/**` を作れるのは App だけ (ruleset A) なので、
この接頭辞を持つ head branch の PR はすべてこのループが起票したものである。
`--state all` で引くのも従来どおり必須で、closed / merged の PR を落とすと台帳が
その finding を決着させられなくなる (`references/ledger.md`)。

この「1 週遅れ」は、承認ゲートを PR レビューに置いたことによる台帳の遅延
(下の「ブランチと PR の種別」) と同じ性質のもので、同じ理由で受け入れる。

CI runner は `trigger-evals.yml` と同じく `python3` を直接呼ぶ (`uv` が無い runner
前提) — ローカル / agent 実行 (Step 5 やこの下の Route 2) では `uv run python3` を使う。

`rulesync-sync.mjs` は引数なしだと生成物を**書き込む**。検証に使うのは `--check` の方で、
生成は Step 5 の前段として別に実行する。

### 実行アイデンティティ — 専用の GitHub App (必須)

この workflow は **専用の GitHub App としてしか動かない**。`GITHUB_TOKEN` で走らせる
経路は用意していない:

- `GITHUB_TOKEN` (`github-actions[bot]`) は **ruleset の bypass actor になれない**。
  つまり `improve/**` の作成を絞る ruleset (A) を作ると workflow 自身がブランチを
  作れなくなり、作らなければ誰でも `improve/**` を植えられる。どちらも成立しない
- App なら A の bypass にその App だけを載せられる。ついでに `improve/*` PR の
  `pull_request` run が承認待ちにならず、default branch への write を workflow の
  `GITHUB_TOKEN` から切り離せる

**前提条件 (すべて揃うまで workflow は起動しない)**:

1. GitHub App を作る。権限は **Contents: read/write**、**Pull requests: read/write**、
   **Issues: read** (`agent-feedback` ラベルの issue とそのコメントを読むため)、
   **Administration: write** (ruleset の `bypass_actors` を読むため — 後述)
2. その App をこのリポジトリにインストールする
3. App ID と private key を repo secrets に置く
   (`SKILL_IMPROVER_APP_ID` / `SKILL_IMPROVER_APP_PRIVATE_KEY`)
4. **default branch の ruleset** を作る (前節。`~DEFAULT_BRANCH` /
   enforcement=active / `pull_request` または `update` ルール / bypass に
   Integration を入れない)
5. **ruleset A (create-only)** を作る — `refs/heads/improve/**` /
   enforcement=active / **`creation` ルール** / **bypass はその App 1 件だけ**、
   bypass mode は **「Always allow」** (`pull_request` モードでは直接 push を
   通せない)。これで `improve/**` を作れるのは workflow の App だけになる
6. **ruleset B (freeze)** を作る — `refs/heads/improve/**` / enforcement=active /
   **`update` と `deletion` の両ルール** / **bypass actors は 1 件も入れない**
   (この App も入れない)。これで作成後の `improve/**` は誰も動かせなくなり、
   検証済みの `head_sha` が起票まで不変になる。**A と B は別々の ruleset**である
   (bypass の有無が真逆なので 1 本にはまとめられない)
7. **3 本とも `conditions.ref_name.exclude` を空にする** — GitHub は `exclude` に
   一致した ref に ruleset を適用しないので、`include` だけ合っていても `exclude` が
   対象を覆っていれば**何も強制されない ruleset が preflight を通ってしまう**

preflight は 3・4・5・6・7 を機械的に検査し (A は `actor_id` が
`SKILL_IMPROVER_APP_ID` と一致・`bypass_mode` が `always`・`bypass_actors` が
ちょうど 1 件、B は `bypass_actors` が空、どちらも `ref_name.exclude` が空で
`creation` / `update`+`deletion` のルールを持つことまで)、欠けていれば agent を
起動せずに `exit 1` する。4〜6 は repo 設定の変更なのでコード側では作れない。
**ruleset を編集できる者がこの設計のトラストルート**であり、そこだけは workflow から
縛れない (残余リスク)。

### トークンは 3 本に割る (agent は書き込みトークンを持たない)

installation token は既定でインストール時の**全権限**を持つ。そのまま agent に渡すと、
prompt injection が通ったときに書き込み権限ごと持っていかれる。そこで
`permission-*` で明示的に絞ったうえで、**用途ごとに分けて発行する** (write は job ごとに別発行なので実際には計 4 本):

| トークン | 権限 | 使う場所 |
|---|---|---|
| read | `contents: read` / `issues: read` / `pull-requests: read` | `improve` の checkout、**agent の `GH_TOKEN`** |
| preflight | `administration: write` | ruleset の preflight step **だけ** (agent より前) |
| write | `contents: write` / `pull-requests: write` | `stage` の push (`improve/**` の作成)。**agent が走った runner には存在せず、`stage` の runner で発行する** |
| PR | `contents: read` / `pull-requests: write` | `publish` の checkout と `gh pr create`。**contents は read** — 凍結されたブランチに commit を積む経路が無くなったので、起票 job から `improve/**` を動かす能力を外してある |

job の `GITHUB_TOKEN` は `improve` では **`contents: read` だけ**に絞ってある。
`claude-code-action` は `process.env` をそのまま SDK に渡すので、我々が
`github_token` / `GH_TOKEN` に何を入れても **job の `GITHUB_TOKEN` は agent の環境に
届く**。pin した action の中身は変えられないので、届いても害が無いように
**その `GITHUB_TOKEN` から書き込み能力を取り上げる**方で解いた: checkout は App の
read トークン、preflight は App の preflight トークン、artifact は
`ACTIONS_RUNTIME_TOKEN` を使うので、`improve` の `GITHUB_TOKEN` に write が要る場面が
そもそも無い。ブランチの作成は**この job では行わない** (別 runner の `stage`)。
`ACTIONS_RUNTIME_TOKEN` 自体も agent に届くが、`stage` からは実値を知れないので
run スコープ JWT の形で拾う — 上の「走査するもの」を参照。

**preflight トークンが `administration: write` を要る理由**: GitHub は ruleset の
`bypass_actors` を **その ruleset への write 権限を持つ呼び出しにしか返さない**
([REST API endpoints for rules](https://docs.github.com/en/rest/repos/rules))。
読み取り権限だけで引くとこの項目がそもそも応答に無く、`(.bypass_actors // [])` の
ような書き方では「bypass が無い」と読めてしまう (**fail-open**)。そこで preflight は
専用トークンで引き、**キーの有無そのものを先に確かめて**、無ければ権限不足として
`exit 1` する。

このトークンの扱い: 発行するのは **agent が起動する前**の step で、**渡し先は
preflight step の `GH_TOKEN` だけ**。checkout にも agent にも渡らない
(`persist-credentials: false` なので `.git/config` にも入らない) し、用途も
ruleset の GET に限られる。`administration` を要求するのはこの 1 本だけで、
read / write の 2 本は従来どおり。

さらに `improve` の checkout は `persist-credentials: false` にしてある。
`.git/config` に資格情報を残さないので、agent が git 設定を読んでトークンを
PR 本文に書き出す経路が無い。**書き込みトークンは `improve` job の runner に、
agent の実行中も実行後も存在しない** (発行するのは別 runner の `stage` / `publish`)。

その結果、**agent は push しない**: 改善ブランチにローカル commit するところまでで、
その ref は bundle として `stage` に渡り、`stage` が検証・走査を通してから push する。
`head_sha` は `stage` が **実際に push する ref から**取る (agent の申告値は使わない)。

`concurrency: skill-improver` (`cancel-in-progress: false`) で直列化しているのは、
週次の run 同士が同じ台帳行や同じ finding を二重に扱うのを防ぐため。**ブランチ名の
衝突**はここではなく run id の suffix が防ぎ、**検証済み SHA が動かないこと**は
ruleset B が保証する (上の「検証済み SHA の不変条件」) — 直列化はもうその保証の
一部ではない。

### ブランチと PR の種別

台帳の書き込みは**すべて PR 経由**で default branch に入る (Iron Law: default branch に
直接 push しない)。1 回の実行が作りうるのは次の 2 種類:

| ブランチ | PR タイトル | 中身 |
|---|---|---|
| `improve/<skill>-<finding-id>-<run id>` | 改善差分に応じた title | 対象 skill の差分 + その finding の台帳行 (**1 commit だけ**。ブランチは凍結されるので `link-pr` の 2 commit 目は積めない) |
| `improve/ledger-reconcile-<run id>` | `chore(ledger): reconcile <date>` | Step 0 の突き合わせ結果 (`set-status` / `record-metrics --phase after` と、前回起票した PR の `link-pr`) |

次の実行は **default branch の台帳**を読む。したがって突き合わせが効くのは reconcile PR が
merge された後で、それまで同じエントリが再び `pr_open` として並ぶ。これは承認ゲートを
PR レビューに置いたことの代償として受け入れる遅延で、既に PR が出ている突き合わせは
重複起票せずレポートに書く。

手動起動:

```bash
gh workflow run skill-improver.yml                       # 全系統
gh workflow run skill-improver.yml -f focus=ci-self-heal # 対象を絞る
```

## 経路 2 — Anthropic Routine

Actions を使わない (使えない) 環境では、Routine に週次で登録する。cron は UTC で
解釈されるため、月曜 09:00 JST は `0 0 * * 1`。

> **ruleset A / B はこの経路にも効く**。`improve/**` を作れるのは App だけ
> (A) で、作った後は誰も更新できない (B) — 人間や Routine の資格情報では
> `improve/**` を作れないし、作れたとしても 2 度目の push は通らない。
> **この repo のように ruleset を入れている環境では、Routine / 対話セッションは
> `gh workflow run skill-improver.yml` で workflow を起動する**のが正しい経路で、
> 自分で push するのは ruleset が無い (consumer) 環境に限られる。どちらの環境でも
> 手順は同じ: 改善はローカルに commit し切ってから **1 度だけ** push し、
> 台帳の `link-pr` は次回実行の Step 0 に任せる。

登録するプロンプト:

```text
kanade0404/skills で skill-improver を 1 周回してください。

1. skills/skill-improver/SKILL.md を読み、その手順に従う。モデル配分は model-policy に
   従い、収集・台帳操作のような機械的作業は subagent に委譲して自分は判断に徹する
2. まず Step 0 (reconcile): ledger.py list --status pr_open で未決着のエントリを
   列挙し、各 PR の現在の状態 (merged / closed / open) を確認して台帳を更新する。
   merged なら set-status --status merged と、取れる after メトリクスの
   record-metrics --phase after。closed (未 merge) なら rejected。あわせて
   ledger.py list --missing-after も列挙し、after を取り損ねた merged エントリを
   拾い直す (放置すると効果が測られないまま静かに消える)。更新は
   improve/ledger-reconcile-<run ごとに一意な id> ブランチに commit し、
   "chore(ledger): reconcile <date>" として PR を出す (default branch には push
   しない)。そのうえで ledger.py report を読み、revert candidate があれば
   新規候補より先に報告する
2.5. open な improve/* PR (gh pr list --state open --search 'head:improve/') の
   head ブランチにある improvements/ledger.jsonl も
   ledger.py list --ledger <tmp> で読む。pending と数えるのは (a) status が
   pr_open の行と (b) status が proposed でも `pr` に**開ける PR URL** が入って
   いる行の 2 つだけで、これは `ledger.py` の `is_pending_row()` と同じ条件
   (`references/ledger.md`)。(b) は PR 起票の後 `set-status` の前に落ちた残骸で、
   PR は実在するので二重起票しない。head ブランチの台帳は default branch の履歴を
   含むので、merged / rejected / reverted の行まで数えると本物の再発を握り潰す。
   pending の行と target_skill + finding クラスが一致する候補は新規起票せず、実行
   レポートの「既存 PR あり (skip)」に PR URL を並べたうえで、その PR の現在の
   状態で決着させる。**`pr` が null の行と `pr` が PR URL の形をしていない行は、
   status が proposed でも pr_open でも pending ではなく修復対象** — 辿れる PR が
   無いまま pending として数えると、決着させる URL が無いのにその finding が永久に
   抑止される。列挙は `ledger.py list --status proposed` と
   `ledger.py list --inconsistent` の 2 本で拾う (pending だけを直接引くサブ
   コマンドは無い)。reconcile ブランチで link-pr を実行して commit し、**修復した
   行はそのまま通常の pending 判定に戻る** (link-pr できたなら (b) として pending、
   引ける PR が 1 件も無ければ proposed / pr=null のままで、その finding は新規
   候補として拾い直してよい)
3. 入力は 3 系統: (a) 直近 1 週の retro / session-retro finding のうち lever が
   skill edit / ept-handoff のもの (台帳には ept として保存される)
   (b) agent-feedback ラベルの issue / PR で、author association が
   OWNER / MEMBER / COLLABORATOR のコメントだけ。取得は
   `gh issue list --label agent-feedback --state all --limit 200` /
   `gh pr list --label agent-feedback --state all --limit 200`
   (既定の open / 30 件では closed の feedback と 31 件目以降が落ちる)
   (c) skills/*/evals/*-trigger-results-*.jsonl のうち F1 < 0.8 の skill
4. 候補ごとに ledger.py check-target を通す。exit 2 (メタスキル) なら編集せず
   improvements/ledger.jsonl に excluded_meta で記録して人間に上げる
5. 採用する候補は 1 finding = 1 PR = 1 テーマ。改善ブランチは毎回 default branch を
   最新化してから improve/<skill>-<finding-id>-<run ごとに一意な id> を切る (前の
   改善ブランチから枝分かれさせない。**同じ名前は作り直せない**ので id は毎回変える)。
   default branch には push しない。merge もしない
6. skill のソースを触ったら node scripts/rulesync-sync.mjs で生成物を再生成し、
   そのうえで PR を開く前に score_triggers.py / .github/scripts/check_trigger_evals.py /
   uv run python3 -m unittest discover -s tests / node scripts/rulesync-sync.mjs --check
   を通す (検証に使うのは --check。引数なしは生成物を書き込むので検証にならない)。
   Routine は workflow の外なので、検証も PR 起票も自分で行う
7. push は **1 ブランチ 1 回だけ** (improve/** は作成後に更新できない)。PR を
   作った後の ledger.py link-pr は**このブランチには積まない** — 台帳行は
   proposed / pr=null のまま残し、次回実行の Step 0 が head branch の接頭辞検索で
   回収して reconcile ブランチに紐付ける
8. 起票した PR・除外・見送り・revert candidate を SKILL.md の実行レポート形式で報告する
9. 候補が 0 件なら PR を作らず「今週は改善なし」と報告して終了する
```

## 手動起動 (対話セッション)

週次を待たずに回したいときは、そのまま依頼すればよい:

「skill 改善 PR 出して」「retro の finding を skill に反映して」
「agent-feedback を取り込んで」「改善ループ回して」

特定の finding だけ回すときは finding id (`IMP-20260910-4ce5643613`) か対象 skill 名を添える。

## 実行間隔を変えるときの判断材料

- **PR がレビューされずに溜まっている** → 間隔を延ばす (隔週)。未レビューの改善 PR が
  積み上がると、after メトリクスが取れず revert 判断もできない
- **同じ finding の `recurrence` が 3 以上に伸びる** → 間隔ではなく差分の質の問題。
  `ledger.py report` で再発クラスを確認し、原則への一般化ができているかを見直す。
  再発かどうかは `report --skill <skill>` の出力を読んで **agent が判断する** —
  台帳側の正規化が吸収するのは表記揺れだけで、言い換えは別クラスに落ちる。同じ問題だと
  判断したら `add --class <key>` で同じクラスキーを付けて束ねる
