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

権限は job ごとに分けてあり、agent を動かす `improve` は `contents: read` だけ、
書き込み (`contents: write` / `pull-requests: write`) は `publish` と、agent 実行後の
trusted step が使う App トークンにだけ置く (内訳は下の「job の分割」)。default branch への push と merge は**手順として**
行わない — 承認ゲートは PR レビューに置く。

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

push と PR 起票は専用の GitHub App が行う (下の「実行アイデンティティ」節)。
これは任意の強化ではなく**前提条件**で、App と `improve/**` の ruleset が揃うまで
workflow は起動しない。

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
公開 (push) に置く**: `improve` job の trusted push step が、**push の前に**候補
ブランチの差分をシークレットで走査する (`manifest_guard.py scan-diff`)。

走査するもの:

- **この job が見えるシークレットの実値** — `CLAUDE_CODE_OAUTH_TOKEN`、App の
  read / preflight / write トークン、`SKILL_IMPROVER_APP_PRIVATE_KEY`、job の
  `GITHUB_TOKEN`、そして **`ACTIONS_RUNTIME_TOKEN`** (下記)。それぞれ
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

**`ACTIONS_RUNTIME_TOKEN` だけ受け渡しが違う**。これは artifact / cache API 用に
GitHub Actions が **JavaScript action の step にだけ**注入する run スコープの JWT で、
`claude-code-action` は JavaScript action なので agent のプロセスもこれを継承する
(この workflow 自身、manifest の artifact upload / download でこのトークンに依存して
いる)。漏れれば同じ run の artifact を差し替えられるので走査対象に入れたいが、
`run:` の step にはそもそも注入されないため `SCAN_...: ${{ env.ACTIONS_RUNTIME_TOKEN }}`
では空になる。そこで **agent の実行後に trusted な JavaScript action**
(`actions/github-script`、full SHA で pin) を 1 つ走らせて `process.env` から捕まえ、
`$RUNNER_TEMP` の 0700 ディレクトリに **0600 のファイル**として落とし、そのパスだけを
走査 step に渡す (`manifest_guard.py --secret-file NAME=PATH`)。値は step output にも
`GITHUB_ENV` にも載せない。**空なら step を落とす** — 見えない値を「走査した」ことには
できない (fail-closed)。走査側も **symlink や 0600 でない mode のファイルは拒否**する。

運用上の規律:

- シークレットの値は**環境変数名で渡す** (コマンドラインは他プロセスから見える)。
  環境変数に載らない値だけ、0600 のファイルのパスを `--secret-file` で渡す。
  検出しても**値も一致箇所も出力しない** — ログと summary に出るのはパスと
  「どの名前のシークレットか」だけ
- 検査スクリプトは working tree からではなく **run を起動した commit の blob**
  (`git show $GITHUB_SHA:.github/scripts/manifest_guard.py`) を取り出して実行する。
  `.github/` を禁じるパスの allow-list が効くのは `verify` job = push の後なので、
  push 前の working tree にあるコピーは候補側の内容かもしれない
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

### improve/* PR には repo の CI が来ない — だから検証してから起票する

**`GITHUB_TOKEN` が作成・更新した PR の `pull_request` run (`opened` / `synchronize` /
`reopened`) は approval-required 状態で作成され、write 権限者が「Approve workflows to
run」を押すまで走らない** ([GITHUB_TOKEN のドキュメント](https://docs.github.com/en/actions/concepts/security/github_token))。
そのため trigger-evals / rulesync drift / unittest の結果は、人間が承認するまで
レビュアーに見えない。workflow 内の事前検証は、その承認前に結果を見せるための代替である。

そこで **workflow モードでは agent は push も PR 起票もしない**。責務は 3 段に分かれる:

1. **agent (`improve` job)** — 改善ブランチを切って**ローカルに commit**し、環境変数
   `MANIFEST` のファイルに 1 行 1 JSON
   (`{"branch":..., "title":..., "body_file":..., "ledger_id":...}`) を追記する。
   持っているのは読み取り専用トークンだけで、`gh pr create` も allow-list の外
2. **trusted push step (`improve` job、agent の実行後)** — 書き込みトークンを発行し、
   manifest の `branch` を検証してローカル ref を push する。**`head_sha` はここで
   実際に push した ref から計算して manifest に書き足す** (agent の申告値は使わない)。
   続けて **artifact に上げる前に** manifest を `.github/scripts/manifest_guard.py`
   で組み直す — 許可したキー (`branch` / `head_sha` / `body_file` / `ledger_id` /
   `title`) だけを残し、**全ての文字列フィールド**について長さ・制御文字・トークン様
   文字列を検査する (agent は自分の `GH_TOKEN` を読めるので、`title` に入れられると
   `gh pr create --title` で公開される)。`body_file` も 1 件ずつ検証し (bodies/ 直下の
   通常ファイル、symlink でない、hard link でない、64 KiB 以下、トークン様文字列を
   含まない)、
   **通ったファイルだけを新しく作った空のディレクトリにコピーして、そちらだけを上げる** —
   agent が書けるディレクトリをそのまま artifact にすると、`body_file` を任意のファイルへの
   symlink / hard link にすり替えるだけで `upload-artifact` がその中身を運び、PR 本文に載る
3. **`verify` → `publish`** — `verify` が manifest の値・台帳差分・パスの allow-list を
   検査し、その SHA でテストを回す。通ったものだけ `publish` が `gh pr create` し、
   `link-pr` を commit / push する

### job の分割 (権限と信頼の分離)

候補ブランチの中身は agent が書いたコードであり、そのまま書き込み資格情報のある場所で
実行すると、テストや検査スクリプトを書き換えたブランチが資格情報に手を伸ばせる。
そのため job を 4 つに割り、**ブランチを実行する job から資格情報を外し、その job の
出力も信用しない**:

| job | 権限 | 役割 |
|---|---|---|
| `improve` | **`contents: read` のみ** | ruleset の preflight、agent 実行 (読み取り専用トークン)、trusted step でのブランチ push、manifest の検査と artifact 化 |
| `verify` (**候補 1 件につき 1 job**) | `contents: read` のみ (`persist-credentials: false`、`GH_TOKEN` もシークレットも渡さない) | manifest の値の検証、パスの allow-list ゲート、**台帳差分のゲート (`ledger.py verify-diff`)**、メタスキル対象の拒否、ブランチ上で `unittest` / `check_trigger_evals.py` / `rulesync-sync.mjs --check`。**artifact は上げない** |
| `collect` | `actions: read` のみ | 各 `verify (<idx>)` の conclusion と improve の manifest から合格記録を組み立てて artifact に上げる。候補コードは動かさない |
| `publish` | `contents: write` / `pull-requests: write` | 合格記録にある候補の `gh pr create`、`link-pr` の commit / push、失敗時の補償 |

`improve` の job 権限が `contents: read` だけなのは、`claude-code-action` が
`process.env` をそのまま SDK に渡す = **job の `GITHUB_TOKEN` は agent の環境に届く**
ため。書き込み能力はこの job の `GITHUB_TOKEN` には持たせず、**agent の実行後に
発行する App の write トークン** (trusted な push step) と **`publish` job** の 2 か所に
だけ置く。

**なぜ verify を 1 件 1 job にするか**: 「候補コードを動かした runner が、そのまま
合格記録も書く」構造は成立しない。候補コードは runner 上で任意に動けるので、落ちた
別の `improve/*` ブランチを指す「合格した行」を書き足せてしまい、`publish` からは
区別が付かない。artifact も同じ理由で信用できない (`ACTIONS_RUNTIME_TOKEN` があれば
runner 上の任意のコードが artifact を上げられる)。そこで **verify の信頼できる出力を
job の conclusion 1 ビットだけに絞り**、合格記録の組み立ては候補コードを動かさない
`collect` が行う。`collect` が読むのは (1) 各 `verify (<idx>)` の conclusion
(`actions: read` で jobs API から取得)、(2) **候補コードが 1 行も動く前に** improve が
上げた manifest — の 2 つだけで、PR 本文も後者から取る。

push できなかった候補があっても `improve` はそこで落とさない (落とすと後続 job が
まるごと skip され、push できた候補まで検証・起票されなくなる)。件数を job output
`push_failed` に出し、**`publish` の最終 step が起票を終えてからその run を赤にする**。

`verify` は候補が 1 つでも落ちれば赤くなる (その可視性は保つ) が、**通った候補は
起票する** — `collect` と `publish` は `always()` を含む条件で回し、cancelled では
走らせない (状態関数を含まない `if` には暗黙の `success()` が掛かるため `always()` が
要る)。候補が 0 件なら `verify` の matrix は空で job ごと skip され、`collect` が空の
合格記録を出して `publish` が「起票なし」で正常終了する。

`verify` は `fetch-depth: 0` の checkout で全 remote head をローカルに取り込むため、
job 中に追加のネットワークアクセス (= 資格情報) が要らない。`publish` は書き込み権限を
持つが**ブランチのコードを実行しない** — `ledger.py` は checkout 前に default branch 側の
コピーを `$RUNNER_TEMP` へ退避して、そちらを使う。

**台帳差分のゲート**: パスの allow-list は `improvements/ledger.jsonl` を**ファイル
単位**で許すため、行の粒度でも検査する。`verify` は base 側の `ledger.py` (候補
ブランチのコピーではない) で `verify-diff` を実行し、改善ブランチには「自分の 1 行の
追加だけ」、突き合わせブランチには「決着した行の `status` / `pr` / `after` / `notes`
を許された遷移で進めるだけ」を要求する (詳細は `references/ledger.md`)。

**パスの allow-list ゲート**: `verify` はブランチの中身を実行する前に
`git diff --name-only origin/<default>...origin/<branch>` を取り、
`skills/<target_skill>/**` / `.claude/skills/<target_skill>/**` /
`.agents/skills/<target_skill>/**` / `improvements/ledger.jsonl` の範囲に収まっているかを
確かめる (reconcile ブランチは `improvements/ledger.jsonl` のみ)。外れるパスが 1 つでも
あればそのブランチは実行せず、PR にもしない。

**manifest の値は agent が書いたデータ**として扱い、特権的に使う前に検証する:
`branch` は `^improve/[A-Za-z0-9._-]+$` で origin に実在すること、`head_sha` は
`^[0-9a-f]{40}$` であること、`body_file` は `bodies/` 直下の通常ファイル
(symlink・`..`・別ディレクトリは不可) であること、`ledger_id` は
`^IMP-[0-9]{8}-[0-9a-f]{10}$` かつそのブランチの台帳に `proposed` / `pr == null` の
行として実在すること。`publish` は artifact 経由で受け取った値を自分でもう一度検証する。
落ちた行は PR にならず job summary に出て job が赤になる (ブランチは調査用に残る)。

**検証も起票もブランチ名ではなく `head_sha` を対象にする**。`improve` の push step が
push した ref の SHA を manifest に書き、`verify` は `origin/<branch>` が
その SHA と一致することを確かめてから **SHA を checkout** して allow-list ゲートと
検査を回す。`publish` は `gh pr create` の直前と `link-pr` の push 直前に
`git ls-remote origin refs/heads/<branch>` で remote の先端を取り直し、`head_sha` と
一致しなければそのブランチを起票しない (起票後に判明した場合は PR を閉じて補償する)。
ブランチ名で追い続けると、検証と起票の間に押された commit が「検証済み」として PR に
載る (TOCTOU)。

> **最後の窓**: 最後の照合と `gh pr create` の間はごく短いが 0 ではない。`link-pr` の
> push は非 force なので remote が動いていればそこでも弾かれるが、窓そのものを消すのは
> **`improve/**` への push をこの App だけに絞る ruleset** の役目である (下の
> 「実行アイデンティティ」節。preflight がその存在を必須として検査する)。

**PR 起票後に `link-pr` が落ちた場合の補償**: 台帳から辿れない PR をレビュー待ちに
残さないため `publish` がその PR を閉じるが、**閉じる前に「閉じた」という事実を台帳に
載せる** — 先に close だけすると「PR は closed、台帳は `proposed` / `pr == null`」と
いう、どちらからも辿れない状態が残る。順序は (1) そのブランチで `link-pr` と
`set-status --status rejected --notes "link-pr failed: ..."` を commit / push、
(2) 成功したときだけ `gh pr close`。(1) が失敗したら **PR は open のまま残し**、
job summary に修復対象として記録する (次回の Step 0 が「`proposed` かつ `pr == null` の
行に対応する open PR」として拾う)。

CI runner は `trigger-evals.yml` と同じく `python3` を直接呼ぶ (`uv` が無い runner
前提) — ローカル / agent 実行 (Step 5 やこの下の Route 2) では `uv run python3` を使う。

`rulesync-sync.mjs` は引数なしだと生成物を**書き込む**。検証に使うのは `--check` の方で、
生成は Step 5 の前段として別に実行する。

### 実行アイデンティティ — 専用の GitHub App (必須)

この workflow は **専用の GitHub App としてしか動かない**。`GITHUB_TOKEN` で走らせる
経路は用意していない:

- `GITHUB_TOKEN` (`github-actions[bot]`) は **ruleset の bypass actor になれない**。
  つまり `improve/**` への push を絞る ruleset を作ると workflow 自身の push が止まり、
  作らなければ verify と publish の間に第三者が push できる窓が残る。どちらも成立しない
- App なら `improve/**` の push をその App 1 つに絞れて窓が消える。ついでに
  `improve/*` PR の `pull_request` run が承認待ちにならず、default branch への write を
  workflow の `GITHUB_TOKEN` から切り離せる

**前提条件 (すべて揃うまで workflow は起動しない)**:

1. GitHub App を作る。権限は **Contents: read/write**、**Pull requests: read/write**、
   **Issues: read** (`agent-feedback` ラベルの issue とそのコメントを読むため)、
   **Administration: write** (ruleset の `bypass_actors` を読むため — 後述)
2. その App をこのリポジトリにインストールする
3. App ID と private key を repo secrets に置く
   (`SKILL_IMPROVER_APP_ID` / `SKILL_IMPROVER_APP_PRIVATE_KEY`)
4. default branch の ruleset を作る (前節。`~DEFAULT_BRANCH` / enforcement=active /
   `pull_request` または `update` ルール / bypass に Integration を入れない)
5. `improve/**` の ruleset を作る (`refs/heads/improve/**` / enforcement=active /
   `update` ルール / **bypass はその App 1 件だけ**、bypass mode は
   **「Always allow」** — `pull_request` モードでは直接 push を通せない)

preflight は 3 と 5 を機械的に検査し (`actor_id` が `SKILL_IMPROVER_APP_ID` と一致し、
`bypass_mode` が `always` で、`bypass_actors` がちょうど 1 件であることまで)、欠けて
いれば agent を起動せずに `exit 1` する。4 と 5 は repo 設定の変更なのでコード側では
作れない。

### トークンは 2 本に割る (agent は書き込みトークンを持たない)

installation token は既定でインストール時の**全権限**を持つ。そのまま agent に渡すと、
prompt injection が通ったときに書き込み権限ごと持っていかれる。そこで
`permission-*` で明示的に絞ったうえで、**用途ごとに 2 本発行する**:

| トークン | 権限 | 使う場所 |
|---|---|---|
| read | `contents: read` / `issues: read` / `pull-requests: read` | `improve` の checkout、**agent の `GH_TOKEN`** |
| preflight | `administration: write` | ruleset の preflight step **だけ** |
| write | `contents: write` / `pull-requests: write` | agent の実行**後**の push step、`publish` job |

job の `GITHUB_TOKEN` は `improve` では **`contents: read` だけ**に絞ってある。
`claude-code-action` は `process.env` をそのまま SDK に渡すので、我々が
`github_token` / `GH_TOKEN` に何を入れても **job の `GITHUB_TOKEN` は agent の環境に
届く**。pin した action の中身は変えられないので、届いても害が無いように
**その `GITHUB_TOKEN` から書き込み能力を取り上げる**方で解いた: checkout は App の
read トークン、preflight は App の preflight トークン、ブランチ push は App の write
トークン、artifact は `ACTIONS_RUNTIME_TOKEN` を使うので、job の `GITHUB_TOKEN` に
write が要る場面がそもそも無い (`ACTIONS_RUNTIME_TOKEN` 自体も agent に届くため、
走査対象に入れてある — 上の「走査するもの」を参照)。

**preflight トークンが `administration: write` を要る理由**: GitHub は ruleset の
`bypass_actors` を **その ruleset への write 権限を持つ呼び出しにしか返さない**
([REST API endpoints for rules](https://docs.github.com/en/rest/repos/rules))。
読み取り権限だけで引くとこの項目がそもそも応答に無く、`(.bypass_actors // [])` の
ような書き方では「bypass が無い」と読めてしまう (**fail-open**)。そこで preflight は
専用トークンで引き、**キーの有無そのものを先に確かめて**、無ければ権限不足として
`exit 1` する。

このトークンの扱い: 発行するのは agent より前の trusted step で、**渡し先は
preflight step の `GH_TOKEN` だけ**。checkout にも agent にも渡らない
(`persist-credentials: false` なので `.git/config` にも入らない) し、用途も
ruleset の GET に限られる。`administration` を要求するのはこの 1 本だけで、
read / write の 2 本は従来どおり。

さらに `improve` の checkout は `persist-credentials: false` にしてある。
`.git/config` に資格情報を残さないので、agent が git 設定を読んでトークンを
PR 本文に書き出す経路が無い。**書き込みトークンは agent の実行中に runner のどこにも
存在しない** (実行後の step で初めて発行する)。

その結果、**agent は push しない**: 改善ブランチにローカル commit するところまでで、
push は後段の trusted step が行う。その step は manifest の `branch` を検証し、
ローカル ref の存在を確かめ、**`head_sha` を push する ref から自分で計算して**
manifest に書き戻す (agent の申告値は使わない)。

`concurrency: skill-improver` で直列化しているのは、同時実行が同じ
`improve/<skill>-<finding-id>` ブランチを取り合うのを防ぐため。

### ブランチと PR の種別

台帳の書き込みは**すべて PR 経由**で default branch に入る (Iron Law: default branch に
直接 push しない)。1 回の実行が作りうるのは次の 2 種類:

| ブランチ | PR タイトル | 中身 |
|---|---|---|
| `improve/<skill>-<finding-id>` | 改善差分に応じた title | 対象 skill の差分 + その finding の台帳行 (1 commit 目) + `link-pr` (2 commit 目) |
| `improve/ledger-reconcile-<YYYYMMDD>` | `chore(ledger): reconcile <date>` | Step 0 の突き合わせ結果 (`set-status` / `record-metrics --phase after`) だけ |

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
解釈されるため、月曜 09:00 JST は `0 0 * * 1`。登録するプロンプト:

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
   improve/ledger-reconcile-<YYYYMMDD> ブランチに commit し、
   "chore(ledger): reconcile <date>" として PR を出す (default branch には push
   しない)。そのうえで ledger.py report を読み、revert candidate があれば
   新規候補より先に報告する
2.5. open な improve/* PR (gh pr list --state open --search 'head:improve/') の
   head ブランチにある improvements/ledger.jsonl も
   ledger.py list --ledger <tmp> で読む。pending と数えるのは status が pr_open の
   行と、proposed かつ pr が null の行だけ (head ブランチの台帳は default branch の
   履歴を含むので、merged / rejected / reverted の行まで数えると本物の再発を
   握り潰す)。pending の行と target_skill + finding クラスが一致する候補は新規
   起票せず、実行レポートの「既存 PR あり (skip)」に PR URL を並べる。
   proposed かつ pr が null のまま open PR がある行は pending ではなく修復対象で、
   そのブランチで link-pr を実行して commit / push する
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
   最新化してから improve/<skill>-<finding-id> を切る (前の改善ブランチから枝分かれ
   させない)。default branch には push しない。merge もしない
6. skill のソースを触ったら node scripts/rulesync-sync.mjs で生成物を再生成し、
   そのうえで PR を開く前に score_triggers.py / .github/scripts/check_trigger_evals.py /
   uv run python3 -m unittest discover -s tests / node scripts/rulesync-sync.mjs --check
   を通す (検証に使うのは --check。引数なしは生成物を書き込むので検証にならない)。
   Routine は workflow の外なので、検証も PR 起票も自分で行う
7. PR を作ったら ledger.py link-pr で紐付け、その差分を同じブランチに commit して
   push する (紐付けを commit しないと次回の Step 0 が突き合わせる相手を失う)
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
