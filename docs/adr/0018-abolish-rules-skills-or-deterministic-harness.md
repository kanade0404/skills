# rule を廃止し、指示は skill か決定論的ハーネスに限定する

Status: accepted (2026-09-19) — [ADR 0015](0015-capability-broker-instead-of-container-credentials.md) と
[ADR 0016](0016-quantum-scoped-fitness-functions.md) を amend する。どちらも supersede せず、Status は
accepted のまま据え置く。両 ADR は **Charter の既定値群の所有者を rules に置いた**が、本 ADR がその所有者を
**版付きデータ**に置き換える。**[ADR 0017](0017-pipeline-code-in-agegis-until-phase-2-gate.md) は amend
しない** — 0017 の前提「Charter は skills repo に留まる」は本 ADR で変わらず、0017 が既に「保護パス manifest
の形式・schema・版の付け方は #122 で定める」と開いているため、Charter の取得手段の変更はその開き口の内側に
収まる。関連: [ADR 0014](0014-add-security-to-ility-priority-order.md) (検査は worker 配備版の設定のみを使い
repo 内は無視する)、[ADR 0017](0017-pipeline-code-in-agegis-until-phase-2-gate.md) (保護パス manifest を
Charter の単一配布物にした先例)、[RELEASING.md](../../RELEASING.md) (consumer は tag 固定で fetch する) と
[ADR 0002](0002-consolidate-on-consumer-update.md) (consumer 側の更新機構)。

Driver: [安全性 (Secure by Design)](0014-add-security-to-ility-priority-order.md) — 「境界の強制は規約では
なく機構で行う」([ADR 0015](0015-capability-broker-instead-of-container-credentials.md))。**本 ADR を支えるのは
構造的な論証である** — (1) rule は助言であって強制ではない、(2) `rules` feature は Claude Code にしか届かず
実装者である Codex には対応機構が無い、(3) path-scoped rule の注入は読み取り tool の種類に依存する。下記
Context の観測は**この論証を支持する 1 事例**であって、論証の根拠そのものではない。用語は
[CONTEXT.md](../../CONTEXT.md) と、仮名については [ADR 0016](0016-quantum-scoped-fitness-functions.md) に従う。

## Context

**rulesync (9.1.1) の `rules` feature は 2 つの出力形を持つ。** `root: true` の rule は生成 `CLAUDE.md` /
`AGENTS.md` として常時文脈に載り、frontmatter `globs` を持つ rule は `.claude/rules/*.md` として
path-scoped に注入される。**現時点で**本 repo が持つ rule は `rules-local/orchestration-policy.md` の 1 本
だけで、それは `root: true` である。配布枠の `rules/` は [README.md](../../README.md) の枠図と consumer の
`--features skills,subagents,commands,hooks,rules` に名前だけが現れ、ディレクトリの実体は無い。**ただし
本 repo は過去に path-scoped rule を配布していた** — `bash-and-api-discipline` と `pr-push-discipline` が
[consumer 伝播の計画メモ](../superpowers/plans/2026-07-19-consumer-pull-propagation.md) に残っている。
現状が 0 本なのは既に撤収済みだからであって、この feature が使われなかったからではない。

**本 ADR は 2 つの前提に立つが、どちらも本 repo では実験で確認していない。** 崩れたときの影響を先に書く。

- **(a) path-scoped rule の注入は built-in file tool の使用に依存する** — glob に一致したファイルを
  built-in Read が読んだときにだけ注入され、Bash 経由 (`cat` / `grep`) では発火しない。出典は
  **2026-09-19 のユーザ観測**と Claude Code の公開ドキュメントの記述で、**本 repo での実験は無い**。
  検証法: glob 付き rule を 1 本置き、対象ファイルを built-in Read と Bash の両方で読んで注入の有無を比べる。
  **(a) が偽なら案 A (現状維持) の却下理由は弱まるが、案 B / C / D の論証は立つ** — 「助言は強制ではない」と
  「Claude Code にしか届かない」は (a) に依存しない。
- **(b) Codex には path-scoped rule 相当の機構が無く、読むのは `AGENTS.md` と `.agents/skills` だけである**
  — 出典は Codex の公開ドキュメントで、**本 repo での実測は無い**。検証法: glob 付き rule を置いた状態で
  Codex を起動し、対象ファイルに触れる作業でその指示が参照されるかを見る。**パイプラインの実装者は Codex**
  ([CONTEXT.md](../../CONTEXT.md)) なので、(b) が真である限り「実装者に読ませたい横断指示」を path-scoped
  rule で表現する経路は存在しない。

**観測は 2 つの独立した事実であり、一方が他方の代替であったわけではない。**

1. 委譲ポリシー (`orchestration-policy`) は root rule として生成 `CLAUDE.md` / `AGENTS.md` に載り、常時
   文脈にあった。その上で **main agent の逸脱が 1 件発生した**。
2. これとは別に、permission hook が `cat` / `cd` / `sed` を物理的に拒否した。**この hook は委譲を強制する
   ものではない** — 本 repo には委譲ポリシーを強制する機構は無い。

ここから言えるのは **「常時載る prose は遵守を保証しない」** までである。**n=1 であり、遵守された事例の
計数も無い。** 「機構にしていれば同じことを強制できた」とは言えない — 委譲を強制する hook は存在せず、
書けるかどうかも未検討である。

**Charter の所有者記述が、既に実体と食い違っている。**
[ADR 0016](0016-quantum-scoped-fitness-functions.md) は Charter を「スキーマ・遷移表・rules」と定義し、
運用パラメータの既定値群を「Charter の rules で宣言され consumer が上書きできる」と書いた。
[ADR 0015](0015-capability-broker-instead-of-container-credentials.md) も受理試行回数と bare repo サイズの
確定後の収録先を rules とした。しかし **Charter を読むのは Foreman / Customs / Tribunal のコードであって
LLM ではない。** [ADR 0014](0014-add-security-to-ility-priority-order.md) は scanner の設定・baseline・
ignore について「worker 配備版のみを使い repo 内は無視する」と定め、
[ADR 0017](0017-pipeline-code-in-agegis-until-phase-2-gate.md) は保護パス manifest を同じ扱いにした。
**契約の実体は既にデータとコードであり、rules は名前だけが残っている。**

## Decision

**rulesync の `rules` feature を本 repo から廃止する。** `rules-local/` と配布枠 `rules/` を無くし、
`scripts/rulesync-sync.mjs` の `--features` を `skills,permissions` にする。以後、agent に向けた指示は
次の 2 つのいずれかでしか表現しない。

1. **skill** — 発火条件 (description の trigger) が定義された、状況依存の手順知識。文脈に載るのは起動時だけ。
2. **決定論的ハーネス** — hook / permissions / CI テスト / worker 配備版のコード。LLM の読解に依存せず効く。

**廃止するのは「配信が読み手の tool 選択と読まれることへの期待に依存する prose」の層である。** 常時載る
prose それ自体を禁じるのではない — 配信が決定論的なら、それは 2 の決定論的ハーネスに属する (下記
Considered Options の SessionStart hook 案がこれに当たる)。どちらにも落ちないものは、書かない。

**適用範囲は本 repo の配布 feature である。** consumer が自分の repo に手書きする `CLAUDE.md` /
`AGENTS.md` は本 ADR の対象外 — それは各 consumer の判断であり、本 ADR が決めるのは
**skills repo が何を配るか**だけである。

### 条件 1 — Charter は版付きデータとして配り、上書き可能域と封緘域を書き分ける

[ADR 0016](0016-quantum-scoped-fitness-functions.md) の「Charter (契約配布物) の rules で宣言され、
consumer が上書きできる既定値群」を、**Charter の既定値データ**と読み替える。あわせて、**Charter の
データが 2 つの部分集合に分かれ、上書き意味論が異なることを本 ADR の決定として明示する**。

- **上書き可能域 (既定値群)** — tick / lease TTL / heartbeat / thread 時間上限 / 滞留閾値など
  ([ADR 0016](0016-quantum-scoped-fitness-functions.md))。**consumer が上書きできる**。上書きは worker 側の
  データで行い、rule 経由の経路は持たない。
- **封緘域** — 契約スキーマ / 遷移表 / pl-event 語彙 / 保護パス manifest / scanner 設定。
  [ADR 0014](0014-add-security-to-ility-priority-order.md) と
  [ADR 0017](0017-pipeline-code-in-agegis-until-phase-2-gate.md) により **worker 配備版だけが供給し、
  repo 内の内容は無視する**。consumer に上書き経路を与えない。

[ADR 0015](0015-capability-broker-instead-of-container-credentials.md) の受理試行回数・bare repo サイズは
上書き可能域に属する。**両域のデータ形式・schema・版の付け方は #122 (Phase 0) の契約スキーマ側で定める** —
本 ADR が決めるのは所有者と、この 2 分だけである。

### 条件 2 — 廃止を規約ではなく生成物の不変条件として検証する

「rule を書かない」を規約に留めない。`node scripts/rulesync-sync.mjs --check` と `tests/` が、**`rules/` /
`rules-local/` が存在しないこと、生成物に root rule 由来の出力 (`.claude/rules` および生成 `CLAUDE.md` /
`AGENTS.md`) が現れないこと**を検証し、現れたら CI を落とす。

**現行の rule 用 sensor は置き換えが必須である** — 「repo-local rule が 1 本以上ある」「`root: true` の
rule がちょうど 1 つある」という 2 つの assertion を持つため、`rules-local/` を消すだけでは CI が 2 件
落ちる。**削除ではなく不在検査への置き換えでしか廃止は完了しない。**

**この検査が測るのは禁止の遵守であって、指示が実際に skill か hook に着地したかではない。** 「rule が無い」
と「指示が届いている」は別の命題で、後者は条件 3 の記録と下記 Neutral の配信確認が受け持つ。

### 条件 3 — `retro` / `session-retro` に `neither` 終端分岐を残す

本 ADR は rule という lever を消すので、両 skill の lever 表は改訂の対象になる。**その改訂では、
`neither` (「skill でも決定論的ハーネスでも表現できなかった」) を終端分岐として必ず残し、記録フィールドを
持たせる。** 消した lever をただ削ると、**表現できなかった事例が観測不能になり、下記トリガ 2 が原理的に
発火しなくなる**。`none` (構造的に再発しない) と `neither` (表現手段が無い) は別の結論として書き分ける。

### 条件 4 — `.codex/rules/rulesync.rules` は残す

名前に rules を含むが、これは `permissions` feature が `permissions.json` から生成する Codex の exec-policy
であって `rules` feature の生成物ではない。**本 ADR の対象外**であり、そのまま残す。混同を避ける注記を
[README.md](../../README.md) に置く。

### 再考のトリガ

**トリガは時点ではなく状態で定義する** ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md)
と同じ流儀)。**観測者も併せて定める** — 誰も見ない条件はトリガではない。

1. **Claude Code の rule 注入が tool 非依存になり、かつ Codex に同等機構が入った** — 案 B (root rule だけ
   残す) を再評価する。**観測者は `retro` の横断 sweep**で、vendor の release notes を確認する回に判定する。
   ただし「助言は強制ではない」は変わらないので、**Charter の所有者は戻さない**。
2. **条件 3 の `neither` 記録が 2 件以上積まれた** — 常時必要な横断指示が skill の trigger でも hook でも
   表現できなかった事例が複数あるということなので、下記 Considered Options の **SessionStart hook 案を
   発動する**。**rule の復活ではない。**
3. **Charter の版不一致による worker 起動拒否 ([ADR 0016](0016-quantum-scoped-fitness-functions.md) の
   Charter 適合度関数) が、直近 30 日で 2 件以上発生し、原因が rulesync tag と Charter 版の二重管理と
   判定された** — 配布経路を再設計する。[ADR 0015](0015-capability-broker-instead-of-container-credentials.md)
   と同じく、**標本が 2 件未満なら判定を保留してトリガを引かない**。**rule に戻すことは選択肢に含めない** —
   負荷の原因は配布経路であって所有者ではない。

## Considered Options

- **現状維持 (path-scoped rule + root rule を持ち続ける)** — path-scoped は前提 (a) の下で Bash 読みの環境で
  発火せず、前提 (b) の下で Codex には機構自体が無い。**守らせたいものを守れない機構を持ち続ける**ことに
  なり、しかも「書いてあるから効いているはず」という誤った安心を供給する。却下。
- **root rule だけ残し、path-scoped を廃止する** — root rule は確かに常時載るので配信は確実である。却下理由は
  **構造的なもの**である: (1) 常時載ることは遵守を強制しない、(2) 全 consumer の全ターンが文脈コストを払う、
  (3) `rules` feature は Claude Code にしか届かず、実装者である Codex には届かない。上記の観測は
  **不遵守 1 件の事例**であって、却下を単独で支えるものではない (遵守の計数も無い)。却下。
- **root rule を skill への索引 (pointer) に縮退させる** — 「どの skill がいつ起動するか」だけを常時載せる
  案。しかしこれも `rules` feature であり、Claude Code にしか届かず、助言のままである。**skill の
  description が既に trigger の canonical source**なので、索引は drift する第 2 の情報源になる。却下。
- **SessionStart hook で `additionalContext` を注入し、常時載る経路とする** — rule と違い、**決定論的で
  読み取り tool に依存しない配信機構**である。本 repo は `hooks-local/` で既に SessionStart を配線しており、
  追加の仕組みは要らない。**今は採らない** — 唯一の候補内容 (`orchestration-policy`) が不要と判断された以上
  載せるものが無く、Codex に同等機構が無いので「Claude Code 限定」という案 B の欠点をそのまま引き継ぐ。
  **ただしトリガ 2 が発火したときの正規の受け皿として指定する** — そのときも rule には戻さない。
  **これは本 ADR の見出しと矛盾しない** — 廃止したのは配信が読み手の tool 選択に依存する prose であり、
  この案の prose は配信が決定論的だからである (上記 Decision)。
- **Charter を skill として配る** — Charter は Foreman / Customs / Tribunal が**強制する**ものであって、
  LLM が読むものではない。skill 化すると「LLM が読んだ」が「守られた」と混同され、
  [ADR 0014](0014-add-security-to-ility-priority-order.md) の「被検査側が検査を無効化する経路を塞ぐ」が
  壊れる。実装者である Codex の skill 発火も保証されない。却下。
- **Charter のデータを rule に鏡写しで置く (人間可読の便宜)** — 2 か所を持てば必ず drift し、drift した
  ときにどちらが正かを決める規則が要る。[ADR 0014](0014-add-security-to-ility-priority-order.md) /
  [ADR 0017](0017-pipeline-code-in-agegis-until-phase-2-gate.md) の「repo 内は無視、配備版のみ」と衝突する。
  人間可読性は schema の description と docs で足りる。却下。
- **rulesync を捨てて独自の配布機構を作る** — 本件は feature を 1 つ外すだけで、skills / permissions の配布は
  問題なく動いている。解こうとしている問題の外側にある one-way door を同時に開く。対象外。

## Consequences

### Positive

- **「読まれることを祈る」層が消える。** 指示は「発火条件が定義された skill」か「機械が強制するハーネス」の
  二分になり、**どちらでもないものは書けない**。書く側が毎回どちらかを選ぶことを強制され、選べなかった事実は
  条件 3 に残る。
- **Charter の所有者記述が実体と一致する。** [ADR 0014](0014-add-security-to-ility-priority-order.md) /
  [ADR 0017](0017-pipeline-code-in-agegis-until-phase-2-gate.md) が既に確立した「配備版のデータだけを読む」に
  例外が無くなり、0016 の既定値群だけが rule 側に残っている状態が解消される。上書き可能域と封緘域の
  書き分け (条件 1) も、rule のままでは表現できなかった。
- **不変条件が機械検証できる形になる。** rule のままでは「宣言された既定値が守られているか」を測る手段が
  無かった。データになれば fixture + clock DI のテスト (#122 の AC) で検証できる。
  [ADR 0016](0016-quantum-scoped-fitness-functions.md) の「測定しない fitness function は宣言より悪い」と
  同じ論理である。

### Negative

- **最初に失われるのは `orchestration-policy` の「常時適用される」という性質である。** この rule の適用域は
  **「main が skill の外で自由裁量に実行する場合」**であり、**定義上どの trigger でも捕まえられない** —
  skill の外側を対象にする指示を、起動条件を持つ skill では表現できない。`model-policy` skill は
  **上位集合でも下位集合でもない**: (a)「実行系 subagent に fable は使わない」、(b) 独立 subtask の並列
  dispatch と main の俯瞰・介入義務、(c) 個々の skill (`commit` / `tdd` / `tidy-first` / `shipping` /
  `rulesync-sync`) が自身の git 操作を自分で決めてよいという除外規定 — これらに `model-policy` 側の対応物は
  無い。model 段の決め方も違う (rule は dispatch ごとに main が選ぶ / `model-policy` は subagent 定義に
  固定し呼び出し時の上書きを禁じる)。**これは重複の解消ではなく内容の喪失である。**
  **ユーザが 2026-09-19 に「root rule も不要、skill で足りる」と判断したことを、この喪失を受け入れる決定
  入力として記録する** — 本 ADR が論証から導いた結論ではない。
- **常時載る横断指示を書く場所が無くなる。** 「全 agent に 1 行だけ読ませたい」が出るたびに、skill の trigger
  にするか hook にするかの設計コストを払う。どちらにも落ちない指示は書かれずに失われ、その事実は条件 3 の
  `neither` に記録される。
- **consumer の移行が必要になる。** consumer は `--features` から `rules` を落とさねばならない。
  **`agegis` が該当する** — [consumer 伝播の計画メモ](../superpowers/plans/2026-07-19-consumer-pull-propagation.md)
  の時点で `rulesync fetch kanade0404/skills@v0.8.0 --features skills,rules` を `package.json` に持つ。
  **リリース前に実挙動を確認する必要がある 2 点**: (1) `rules/` を持たない tag に対して
  `--features ...,rules` を指定した `fetch` がエラーになるか no-op になるか、(2) `generate` が既存の生成
  `CLAUDE.md` / `AGENTS.md` を削除するか、古いまま残すか。**後者が「残る」なら、廃止したはずの指示が
  consumer 側で生き続ける。**
- **semver 上は MAJOR として扱う。** [RELEASING.md](../../RELEASING.md) は MAJOR を「互換が壊れる変更」と
  定め、skill の削除を例に挙げる。**feature 枠の削除は consumer の fetch コマンドを壊すので同格である。**
  ただし RELEASING.md の表には feature 削除の行が無く、追記が要る — **これは follow-up であって本 ADR の
  決定ではない。**
- **改訂の範囲が広い。** 本 ADR は対象を列挙するだけで、修正は行わない。
  - skill: `harness-distribution` (配布枠)、`rulesync-sync` (feature dir 説明 + `evals/` の golden prompt
    「rules はどこに置けばいい?」が陳腐化)、`retro` (lever 表、`rules*/` glob、**および上記トリガ 1 の
    観測義務 — 横断 sweep で vendor の release notes を確認する手順の追加**)、`session-retro` (4 分岐)、
    `skill-builder` / `skill-improver` (整合確認先のパス)
  - 配布・生成: [README.md](../../README.md) の枠図と consumer 向け fetch 例、`scripts/rulesync-sync.mjs` の
    `--features`、`.github/workflows/trigger-evals.yml` の path filter (`rules/**` / `rules-local/**`)
  - 説明文: `hooks/README.md` と `hooks-local/README.md` が「`rules/` と `rules-local/` の分離と同じ命名」を
    根拠に自分を説明しているため、根拠の側が消える
  - 外部: #122 / #128 の AC
- **Charter の配布形式を新たに決める必要が生じる。** rule という既成の器を捨てた以上、データの形式と版の
  付け方を #122 で決めねばならない。rulesync の tag と Charter の tag を同一にするか分けるかは未決で、
  分けた場合は [ADR 0017](0017-pipeline-code-in-agegis-until-phase-2-gate.md) の「3 点同時 bump」の数え方に
  影響する。
- **戻すコストは対称である。** feature を戻すこと自体は安いが、上記の改訂を逆向きに全部やり直すことになる。
  two-way door ではあるが無料ではない (moderate)。

### Neutral

- **生成 `CLAUDE.md` / `AGENTS.md` は root rule 由来なので消える。** skill の到達性は
  **Codex (`.agents/skills`)、Fable の cloud session、claude-code-action の 3 経路で確認する** — root
  instructions 無しで skill が発見されるかは経路ごとに別の問題で、Codex だけを見ても足りない。
  **確認の失敗は本 ADR を revert しない** — 届かなかった指示について SessionStart hook 案 (上記
  Considered Options) を発動し、条件 3 の `neither` に記録してトリガ 2 の計数に入れる。
- **`.codex/rules/rulesync.rules` は残る** (条件 4)。名前が紛らわしい点は README の注記で吸収する。
- **本 ADR は他 repo の `rules/` については何も決めていない。** agegis が持つ rule 群 (ai-agent-practices /
  architecture-style / design-guide / robust-python) の移送先 — lint / 型検査 / ast-grep に落とせるものと
  skill 化すべきものの仕分け — は agegis 側の issue で扱う。本 ADR の帰結ではあるが、決定の場が別である。
- **「consumer が既定値を上書きできる」という性質は失われない** ([ADR 0016](0016-quantum-scoped-fitness-functions.md))。
  上書きの機構は変わらず、置き場が rule から worker 側のデータに移るだけである。
- **未決: `rules/` を持たない tag に対する `rulesync fetch --features ...,rules` の挙動** (エラーか no-op か)、
  および **`generate` が既存の生成 `CLAUDE.md` / `AGENTS.md` を削除するか残すか。** リリース前に実挙動を
  確認する (上記 Negative)。
- **未決: root instructions 無しでの skill 発見が、Fable の cloud session と claude-code-action で成立するか。**
  Codex の `.agents/skills` と同様に成立する想定だが未確認 (上記の 1 点目)。
- **未決: consumer が自分で書く `CLAUDE.md` / `AGENTS.md` をどうするか** — **本 ADR の範囲外**である。本 ADR は
  skills repo が何を配るかだけを決める。
- **未決: Charter の配布物を rulesync の tag に載せ続けるか、別経路にするか** — #122 に委ねる (上記 Negative)。
- **未決: [RELEASING.md](../../RELEASING.md) の semver 表への「feature 枠の削除 = MAJOR」行の追記** — follow-up
  とする。
