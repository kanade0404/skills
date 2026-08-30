# -ilities をデプロイ可能単位ごとの fitness function として測定する

Status: accepted (2026-08-30) — [ADR 0009](0009-ility-priority-order.md) を amend する。0009 は
supersede せず、Status は accepted のまま据え置く。0009 は
[ADR 0014](0014-add-security-to-ility-priority-order.md) にも amend されており、0014 が -ility の一覧と
順序を、本 ADR が適用単位と測定方法を扱う。

Driver: 本 ADR は [ADR 0009](0009-ility-priority-order.md) と同じく、下流の決定に対して Driver を供給
する側である。用語は [CONTEXT.md](../../CONTEXT.md) に従い、本 ADR が導入する仮名は下記 Decision で
定義する。

## Context

[ADR 0009](0009-ility-priority-order.md) は -ilities の優先順を定め、
[ADR 0014](0014-add-security-to-ility-priority-order.md) が安全性を加えた。どちらも **system 全体に
一枚岩で適用される宣言**として書かれている。設計が具体化するにつれ、この形が 2 つの問題を起こした。

**問題 1 — 単位ごとに要る特性が違う。** 設計の議論では「可用性は買わない (停止を許容し、回復可能性で
代替する)」という言い方をしてきた。これは worker daemon については正しいが、**その worker が死んだこと
を検知する監視 workflow** に同じ言い方を適用したら設計が成立しない — 監視は worker が死んでいるときに
こそ動かねばならない。逆に Codex 実行コンテナには可用性も回復可能性も要らず、要るのは「いつ殺されても
構わないこと」である。system 全体に対して 1 つの順序を宣言しても、**個々の構成要素をどう作るかは決ま
らない**。

**問題 2 — 宣言は退行を検知しない。** 「回復可能である」と書いてあっても、実際に回復するかは誰も測って
いない。測定できない特性は、壊れたことに気づく手段が無い。人間が張り付かない運用 ([ADR 0009](0009-ility-priority-order.md)
の前提) では、これは「壊れていても動いて見える」に直結する。

なお本パイプラインは構成要素の実測源をすでに大量に持っている — lease の時刻も、遷移の履歴も、
heartbeat も、権威面 (state repo) の上にある ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md))。
これは可監査性を第 1 位に置いたことの副産物であり、**測定のための追加インフラをほとんど要求しない**。

## Decision

アーキテクチャ特性を **architecture quantum (独立にデプロイ可能な単位) ごと**に定め、宣言ではなく
**fitness function (測定 + 閾値)** として書く。**全 quantum の全 fitness function が閾値内にあること
を「このシステムが成功している」の定義とする。** 1 つでも破れていれば、機能が動いて見えても設計の
約束は壊れている。

### quantum の同定と仮名

以後、構成要素を次の仮名で呼ぶ。

| 仮名 | 実体 |
|---|---|
| **Foreman** | 常駐 worker daemon ([ADR 0010](0010-resident-worker-with-codex-python-sdk.md))。内部の broker 検査子プロセスを **Customs** と呼ぶ |
| **Crucible** | Codex 実行コンテナ (使い捨て) |
| **Watchtower** | 監視 workflow (Actions schedule) |
| **Herald** | webhook relay |
| **Tribunal** | レビュー層 (Actions の workflow 群 + CodeRabbit) |
| **Charter** | 契約配布物 (スキーマ・遷移表・rules) |
| **Ledger** | state repo ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md))。**データストアであり quantum ではない** — 独立にデプロイされるものではないので、特性は載せず、他の quantum の測定源として現れる |

**Codex thread (SDK client + auth) は Foreman の一部**であって Crucible ではない。信頼・寿命・
credential の 3 つが異なるので、「Codex」と一括りにしない — 一括りにすると
[ADR 0015](0015-capability-broker-instead-of-container-credentials.md) の「Crucible に credential を
配布しない」の適用範囲を誤る。

### 特性は quantum あたり 3 つ以下、fitness function は 1 特性につき複数でよい

**特性の数と fitness function の数を混同しない。** 特性は quantum あたり **3 つ以下**に保ち、1 つの
特性は障害モードごとに複数の fitness function に分解してよい。この階層を明示的に書く — quantum を
分けたこと自体がリストの肥大化への対策であり、単位ごとに 3 つを超えるなら quantum の切り方を疑う。

種別は 2 つ。**常時** = 定常データからの集計。**drill** = fault injection による定期検証。

**閾値のうち運用パラメータは、ADR が個別に発明した数値ではない。** tick 60s / lease TTL 20min /
heartbeat 間隔 5min / heartbeat 失効判定 30min / thread 時間上限 45min / **同時 thread 3** /
`needs-human` 滞留 72h / 未 dispatch 24h / active lane ≤ 30 は、いずれも**凍結設計の既定値**
— **Charter (契約配布物) の rules で宣言され、consumer が上書きできる既定値群** — に由来する。
本 ADR はそれらを測定対象として引くだけで、値の所有者は rules 側である。**数値を変えるなら rules を
変え、この表の閾値と規範不等式
([ADR 0012](0012-write-authority-by-lease-and-sha-cas.md)) を同時に検算する。**

#### Foreman — 特性 3 つ

- **回復可能性** — 障害モードごとに fitness function 3 本。
  - プロセス死: heartbeat (書き込み周期 5min) の連続欠測 ≤ 1 回 (10min 窓)、再起動から初回 tick 完了
    ≤ 5min [常時 / `heartbeat.json` の commit 履歴]
  - lane: lease 失効から takeover までの遅延 ≤ tick + T_reap、`T_recover ≤ 23min`、超過 0 件
    [常時 / `state.json` の lease 時刻と takeover event の差分。数値の根拠は
    [ADR 0012](0012-write-authority-by-lease-and-sha-cas.md)]
  - 再構築: 空の VM と state repo だけから全 lane を再開できる = pass [drill /「例外領域ゼロ」の実測形]
- **有界性** — 障害の起きる面ごとに以下を全て満たすこと [常時 + drill]
  - cap 超過時の `needs-human` 到達率 100%
  - **core quota の消費が 1,000/hr 以内**で remaining の枯渇 0 回。閾値は**発行したリクエスト数ではなく
    消費した quota** で数える — tick 掃引は全 active lane の状態を毎分読むため発行数は 1,000/hr を超えるが、
    ETag の 304 は core quota を消費しない
    ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md) の実測)。観測は実レスポンスの
    `x-ratelimit-remaining`。**この 1,000 は、凍結設計が「worker 1 台 (同時 1 installation) を主語と
    する設計時の数値目標」として宣言した値**であり、恣意的な仮置きではない。ただし **GitHub 側の実効上限
    に対する比としての導出は無い**ため、Phase 1 に実測トラフィックから再導出する。
  - **同時 thread 数 ≤ 3、1 thread の実行時間 ≤ 45min**。
    [ADR 0010](0010-resident-worker-with-codex-python-sdk.md) は「同時 thread 数と thread 時間に数値の
    上限を**置けること**」を要求し、Python SDK がその能力を持つことを確かめた。**値そのものは既定値群が
    与える** (下記)。
  - lane あたりの受理試行回数 ≤ 20 回、bare repo のディスクサイズ ≤ 1 GiB
    ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md))。**この 2 つは既定値群に
    未収録のため本 ADR で暫定を置いた**。Phase 1 で確定し、**確定時は同じ既定値群 (Charter の rules) に
    収録する** — ADR 側に数値を残したままにしない。
  - **reap の 3 つの終了条件 — deadline (2min) 超過・retry budget 枯渇・reap 中の CAS 敗北 — それぞれの
    `needs-human` 到達率 100%** (復元を中断し、lease は保持したまま倒れること) [drill]。
    `T_reap ≤ 2min` は照会数の上限だけでは成立せず、この 3 つの fail closed が効いて初めて
    `T_recover ≤ 23min` が閉じる ([ADR 0012](0012-write-authority-by-lease-and-sha-cas.md))。
    **不等式を主張する以上、終了条件は測定対象である。**
  - **受信中 quota の fail closed 率 100%** — 受信 pack の入力サイズ上限、および受け口プロセスに対する
    filesystem ないし cgroup の quota を超えた push が、**pre-receive の判定を待たずに**受信段階で落ちる
    こと [drill]。**上の「受理試行 ≤ 20 回 / bare repo ≤ 1 GiB」とは別の防壁である** — あちらは
    「正常系の上限」、こちらは「敵対的入力からの防壁」であり、`git-receive-pack` が pre-receive の実行前に
    quarantine へ書く以上、後者を hook の内側では強制できない
    ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md))。
  - **未 dispatch の lane が 24h を超えて滞留する件数 0**、**`needs-human` の滞留に対する 72h 再通知の
    到達率 100% (通算 3 回まで)** — lease が検出できない停滞を受け持つ別の網が実際に働いていることの
    測定 ([ADR 0012](0012-write-authority-by-lease-and-sha-cas.md))
- **安全性** — 以下の fault injection が全て fail closed になること (率 100%) [drill]
  - 受理検査の fault suite: secret 入り diff・保護パス・`codex/**` 外・TOCTOU・scanner 設定の改変
    ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md))
  - **古い `incarnation` / `epoch` からの push・書き込みが受理検査で拒否される率 100%** —
    [ADR 0012](0012-write-authority-by-lease-and-sha-cas.md) の期限内再取得は incarnation で fence する
    設計であり、**fence の正しさが lease と broker の 2 箇所に分かれている**ため、両者が同じ値を見て
    いることをここで押さえる。測っているのは「古い実行体の副作用が落ちること」であって、broker を
    通らない副作用 (発行済み token の直接呼び出し等) は対象外 — それは 0012 が残余として書いている。
  - **Fable が起票したタスク issue の本文が scan 関数を通過した率 100%** — Fable は worker の代行経路に
    乗らない直接の書き手なので、層 2 の中で最も破れやすい
    ([ADR 0014](0014-add-security-to-ility-priority-order.md))。通過を測って初めて「網の目が書き手ごと
    に変わらない」が主張できる。
  - **複数の write permission を相乗りさせた token の発行試行が拒否される率 100%** —
    [ADR 0013](0013-role-separated-tokens-and-credentials.md) の token 操作別分割は GitHub が強制しない
    worker 内の不変条件なので、テストで守るしかない。**測っているのは最小権限の不変条件であって、
    human-only merge ではない** — merge を止められるかは R2 の実測に依存し、この測定では保証されない。

#### Crucible — 特性 3 つ

- **使い捨て可能性** — 任意時点で kill し、再 dispatch で lane が回復する率 100% (`needs-human` に
  倒れない) [drill]
- **隔離** — credential 検査 (env・mount の allowlist) がデプロイごとに pass、allowlist 外 egress の
  deny 率 100% [常時 / image の CI 検査と proxy ログ]
- **有界性** — 生存が 45min を超える**コンテナ** 0 件 [常時]。これは Crucible の寿命の測定であり、
  [ADR 0012](0012-write-authority-by-lease-and-sha-cas.md) が挙げる **thread の 45min 上限とは別物**で
  ある (thread は Foreman 側の部品)。数値が同じなのは、コンテナが thread に従属して生きるためで、
  片方を変えたらもう片方も検算する。

#### Watchtower — 特性 2 つ

- **可用性 (Foreman と運命を共有しないこと)** — **schedule 周期は 15min**。発火の欠測 (間隔 > 2 × 周期
  = 30min) 0 回/週。かつ **Foreman が停止している間に発火すること** [常時 + drill]
- **検知遅延と権威の不在** — heartbeat 停止から `needs-human` 通知まで ≤ 45min (30min の失効判定 +
  schedule 周期 1 回分の遅延許容 15min)、token の permission は read + 通知のみ
  [drill + workflow permissions の lint]

#### Herald — 特性 1 つ

- **非必須性** — relay を停止しても lane の前進 event が発生し続ける (tick 掃引が拾う)。**SLO を置かない
  ことが意図である** [drill]

#### Tribunal — 特性 2 つ

- **決定性と検出力** — 同一 commit に対する ac-verify の再実行一致率 100%、mutation smoke の変異 kill 率
  100% [常時]
- **改変防止** — 保護パス (workflows・契約・検査コード・scanner 設定) に触れる PR の `needs-human` 化率
  100% [drill]

#### Charter — 特性 1 つ

- **版整合** — 版が不一致なら worker の起動を拒否する = pass、配布物の drift check が green [常時]

### 可用性は「買わない」のではなく「Watchtower にだけ買う」

[ADR 0009](0009-ility-priority-order.md) が捨てたのはレイテンシであって可用性ではない。可用性について
の正確な言い方は、**「Watchtower にだけ買い、そこでは Foreman と運命を共有しないことを要件にする」**
である。Foreman の停止は許容し、回復可能性で代替する。

運命分離 (⊥) の主張はそのまま drill の対象になる — Watchtower ⊥ Foreman (Foreman が死んでいるときに
こそ動く。だから Actions に置く) / Ledger ⊥ VM (VM が全損しても状態は無傷) / Herald ⊥ 全て (落ちても
劣化はレイテンシだけ) / Tribunal ⊥ Foreman (**Actions 上で走る検査の実行とその生成物は、Foreman が
侵害されても偽造されない** — ただし CI artifact から権威面への**転記は Foreman が行う**ので、転記後の
`ac/<PR>.json` は Foreman 侵害で偽造されうる。運命分離が守るのは判定の生産までで、登記から先は
[ADR 0015](0015-capability-broker-instead-of-container-credentials.md) の worker 侵害の残余に含まれる) /
Crucible ⊥ Foreman (Crucible の死は正常なイベントで、回復は lane 側の仕事)。逆に **Customs は Foreman
の内側に置いた** — 片方だけ生きていても意味がない同士は、運命を共有してよい。

## Considered Options

- **-ilities を system 全体の宣言のまま据え置く** — 0009 の形。単位ごとに要る特性が違うため、
  「可用性は買わない」のような宣言が Watchtower に適用されて設計が矛盾する。個々の構成要素をどう作るか
  が決まらず、判断のたびに例外を作ることになる。却下。
- **測定せず、宣言のまま運用する** — 退行を検知する手段が無い。人間が張り付かない運用では「壊れていても
  動いて見える」に直結し、0009 が最も恐れた失敗モード (静かに止まる / 間違ったまま進む) そのものである。
  却下。
- **特性を quantum ごとに列挙するが、数に上限を置かない** — 上限が無いリストは網羅の努力で膨らみ、
  「全部が閾値内 = 成功」という定義が実質的に達成不能になる。3 つを超えるなら quantum の切り方が粗い
  というシグナルとして使う方が有用。却下。
- **可用性 SLO のような数値予算で -ilities を表す** — 0009 が「測定基盤を先に作ることになる」として
  却下した案。本 ADR は測定を導入するが、**測定源の大半が権威面に既に存在する**という前提が変わって
  いるため成立する。新しい観測基盤を建てるのではなく、既にある耐久事実を集計する。順序 (0009) は捨てず、
  測定はその下位に置く。
- **0009 / 0014 を supersede して 1 本に書き直す** — 0014 が同じ選択で却下した理由と同じ。「何が、いつ、
  なぜ変わったか」が履歴から読めなくなる。amend を積む形式を維持する。却下。
- **fitness function を Tribunal (レビュー層) の検査項目としてのみ持つ** — CI で測れるものだけが特性に
  なってしまい、drill (fault injection) で初めて分かる回復可能性・運命分離が落ちる。CI に載る分は載せる
  が、それを定義にはしない。却下。

## Consequences

### Positive

- **「成功している」が定義になった**。閾値の一覧が、そのまま「この設計の約束が守られているか」の
  チェックリストである。動いて見えることと約束が守られていることを、別々に判定できる。
- **単位ごとに何を作り込むかが決まる**。Watchtower には可用性を、Crucible には使い捨て可能性を、
  Foreman には回復可能性を作り込む — 一枚岩の宣言では出てこない配分が導出できる。
- **測定のための追加インフラがほぼ要らない**。測定源の大半が権威面と GitHub 上に既にあり、可監査性を
  第 1 位に置いた投資がそのまま回収されている。
- **運命分離の主張が検証対象になった**。「Watchtower は Foreman と独立です」を、Foreman を止めた状態で
  発火することの確認として drill にできる。主張が主張のままにならない。
- 特性を 3 つ以下に保つ規律が、**quantum の切り方が粗いことを検知するシグナル**として働く。

### Negative

- **drill の運用コストが恒久的に発生する**。fault injection を定期的に回すこと自体が仕事であり、回さな
  ければ drill 種別の fitness function は「書いてあるだけ」に退化する。**測定しない fitness function は
  宣言より悪い** — 測っているつもりになる分だけ悪い。
- **閾値の数値が根拠を持ち続けるとは限らない**。23min も 45min も quota 1,000/hr も、現在の設計と規模から
  導いた値である。**値の所有者が rules 側にあるため、rules を上書きした consumer では本 ADR の閾値が
  そのままでは成立しない** — 上書きを許した以上、閾値も consumer ごとに再計算される必要がある。設計が
  変われば全部を検算し直す必要があり、
  [ADR 0012](0012-write-authority-by-lease-and-sha-cas.md) の規範不等式と同じ結合が、閾値の一覧全体に
  広がった。
- **「全閾値内 = 成功」は厳しすぎる定義になりうる**。1 つの drill が落ちただけで「成功していない」と
  言うことになり、運用上は閾値を緩める圧力が常にかかる。緩めた瞬間に定義としての価値が消えるため、
  緩めるなら理由を ADR に残す規律が要る。
- **quantum の一覧が設計の変更に追随する**。構成要素が増えれば表も増え、本 ADR は他の ADR より書き換え
  頻度が高くなる。amend で積むと系列が長くなり、supersede すると履歴が消える — どちらにも痛みがある。
- 特性を 3 つ以下に絞ったことで、**4 つ目に挙がるはずだった性質は明示的に捨てている**。捨てた記録は
  残らない。

### Neutral

- 本 ADR は 0009 を supersede しない。**0009 (順序) + 0014 (一覧に安全性を追加) + 本 ADR (適用単位と
  測定) を合わせたものが現在の -ilities である。**
- 優先順そのものは変えていない。競合したときに上位を採る規則は 0009 のまま有効で、本 ADR は「どの単位に
  どの特性を、どの閾値で置くか」だけを決める。
- **Fable は quantum に含めない。意図的な除外である。** Fable は claude.ai 上の cloud session であり、
  こちらがデプロイも構成も測定もしない **Anthropic 管理のマネージド実行**で、「独立にデプロイ可能な
  単位」という quantum の定義に当てはまらない。閾値を置いても、破れたときに直せる手がこちら側に無い。
  **代わりに、Fable が触れる面をこちら側の quantum の閾値で覆う** — Fable がタスク issue に書く本文が
  scan 関数を通過した率を Foreman の安全性に入れたのはこのためである
  ([ADR 0014](0014-add-security-to-ility-priority-order.md) が層 2 の最も破れやすい箇所と認めた経路)。
  **「全 quantum 閾値内 = 成功」は、Fable 自体の振る舞いを保証しない**ことを明記しておく。
- 仮名は本 ADR を定義の所在とする。[CONTEXT.md](../../CONTEXT.md) の用語集はパイプラインの業務語彙
  (タスク・実装 issue・差し戻し等) を扱っており、実行基盤の構成要素名は本 ADR 側に置く。
- 個々の閾値の根拠は各 ADR にある — 回復時間は
  [ADR 0012](0012-write-authority-by-lease-and-sha-cas.md)、fault suite の内容と worker 侵害の残余は
  [ADR 0015](0015-capability-broker-instead-of-container-credentials.md)、保護パスと scanner の要件は
  [ADR 0014](0014-add-security-to-ility-priority-order.md)。本 ADR はそれらを測定可能な形に束ねる。
- **Tribunal を Foreman と別の quantum にする理由は本 ADR で述べたものが全てである** (検査と駆動の分離
  = Foreman が侵害されても検査結果が偽造されない)。[ADR 0005](0005-dual-track-security-review.md) は
  レビュー系統を 2 本に冗長化する決定であって、レビュー層と実行体を分ける決定ではない — 別の論点なので
  根拠として引かない。
- Tribunal は**自分でデプロイしない第三者 SaaS を 2 系統含む** —
  [ADR 0005](0005-dual-track-security-review.md) が定めた CodeRabbit (line-level のコード品質・バグ・
  セキュリティ) と Claude Code Actions (仕様適合・アーキテクチャ・適合度関数・セキュリティ) である。
  上の閾値が測っているのは**自前で書いた部分だけ** — ac-verify の決定性、mutation smoke の検出力、
  保護パスの拒否がそれに当たる。**2 系統の判定品質そのものは測定対象にしていない**。0005 が「セキュリ
  ティは両系統が担い、重複を許す」ことで得た冗長は、どちらも LLM である以上、相関する見落としが残る
  — その残余は本 ADR の測定では縮まない。測っているのは「レビューが動いたか」であって「レビューが
  正しかったか」ではない。
