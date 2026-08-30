# トークンと credential を役割で分離する

Status: accepted (2026-08-29), revised (2026-08-30)

**本 ADR は #117 の未 merge バッチ内の ADR であり、同バッチが merge される前に上記の日付で改訂された。** 初版を参照した第三者はまだ存在しないため、accepted 済み ADR の不変性 ([ADR 0014](0014-add-security-to-ility-priority-order.md) の Considered Options) を破らずに本文を直接改訂している。merge 後の変更は amend ADR か Erratum で積む。

Driver: [安全性 (Secure by Design)](0014-add-security-to-ility-priority-order.md) — 権限は既定で最小、
境界の強制は規約ではなく機構で行う。同時に
[-ilities 1 可監査性・回復可能性](0009-ility-priority-order.md) が要求する権威の一意性を、token の層で
支える。用語は [CONTEXT.md](../../CONTEXT.md) に従う。

## Context

このパイプラインには GitHub への書き手が 4 種いる。権威面と派生面を書く worker (**Foreman**)、実装差分を
書く Codex 実行コンテナ (**Crucible**)、タスク issue を起票する **Fable** (claude.ai 上の cloud session)、
そして merge する人間である。仮名の定義は
[ADR 0016](0016-quantum-scoped-fitness-functions.md) にある。**Fable は worker の代行経路に乗らない直接の
書き手**であり ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md) の権威面の表)、credential を
論じる以上ここに書き出さないと境界が閉じない。3 者が同じ credential を共有すると、
[ADR 0011](0011-authority-state-in-dedicated-state-repo.md) と
[ADR 0012](0012-write-authority-by-lease-and-sha-cas.md) が決めた権威の分離が、token の層で崩れる。

実測した制約が 3 つある。

- **PAT では worker と人間の書き込みを分離できない。** ruleset の bypass actor の粒度が「その PAT を
  持つアカウント」になるため、worker に bypass を与えると同じアカウントで行う人間の操作にも bypass が
  効いてしまう。**権威分離を token で表現するには GitHub App (Integration) が必要である。** これは
  [ADR 0011](0011-authority-state-in-dedicated-state-repo.md) の権威分離と本 ADR の credential 分離の
  **二重の根拠**で App 化を要求している。
- **ruleset は private repo でも enforcement が実効**である (パイロットの agegis で確認)。したがって
  private な state repo にも保護規則を張れる。
- **installation token は branch を絞れない。** push 先の制限は token では表現できない (初版はこれを
  ruleset で表現するしかないと結論したが、下記の追補で受理検査へ移した)。

信頼境界の側にも事実がある。実装 issue の本文は Codex が起草する (データ扱い)。issue は code repo に
あるため、**repo 境界だけでは、Codex が契約ブロックを偽造して自分への指示を書き戻す経路を塞げない**。

運用の前提として、worker の VM は使い捨てである。鍵をイメージに焼くと、起動のたびに鍵の再配布経路が
無防備に通ることになる。

**2026-08-30 の追補** — 初版は Crucible に job 別の短命 token を渡す前提で書かれていた。その後の検討で、
GitHub の認可が「操作の種類 × repo」までしか絞れず**操作の引数** (branch・パス・payload) を制約できない
ことが問題の中心だと分かり、token を絞る努力は原理的に完成しないと結論した。詳細と代替案の比較は
[ADR 0015](0015-capability-broker-instead-of-container-credentials.md) にある。本 ADR の credential 表は
その結論を反映して改訂されている。

## Decision

credential を役割で分離する。

| credential | 保持者 | スコープ | 用途 |
|---|---|---|---|
| GitHub App の秘密鍵 | **worker のみ** (ディスクに置かず起動時に secret store から取得) | — | installation token の発行 |
| Crucible (Codex 実行コンテナ) の GitHub credential | **なし** | — | GitHub への到達手段を持たない。push は broker 経由の条件付き capability として受け取る ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md)) |
| push 用 job token | **worker のみ** (job 別・短命) | code repo の `contents:write` **のみ** | 検査に合格した SHA の `codex/**` への push |
| PR 操作用 job token | **worker のみ** (job 別・短命) | code repo の `pull_requests:write` **のみ** | PR の作成・更新・コメント |
| issue 起票用 job token | **worker のみ** (job 別・短命) | code repo の `issues:write` **のみ** | 実装 issue の起票、ラベル・コメントの付与 |
| Fable の credential | **Fable のみ** (claude.ai 側が保持。worker からは触れない) | code repo の `issues:write` **のみ**。state repo・push・merge の権限を持たない | タスク issue の起票 ([ADR 0003](0003-two-layer-task-and-implementation-issues.md) の上位層) |
| 権威面 (state repo) への書き込み | **worker のみ** | state repo | lease・遷移・予算・heartbeat |
| Codex の auth (ChatGPT auth または API key) | **worker のみ** | — | Codex SDK の認証 ([ADR 0010](0010-resident-worker-with-codex-python-sdk.md))。Codex thread は VM 上の worker 側の部品であり、Crucible とは別物 |
| Watchtower (監視 workflow) の token | Actions の workflow | **read + 通知のみ。書き込み権威を持たない** | heartbeat の鮮度読みと `needs-human` 通知 ([ADR 0016](0016-quantum-scoped-fitness-functions.md)) |
| CI runner の `GITHUB_TOKEN` | Actions の workflow | **`contents: read` のみ。secrets を注入しない** | codex 由来 PR のテスト実行 ([ADR 0014](0014-add-security-to-ility-priority-order.md) の信頼境界) |

- **Crucible の行が「なし」であることの根拠と、それでも成果が GitHub に届く仕組みは
  [ADR 0015](0015-capability-broker-instead-of-container-credentials.md) が決める。** 本 ADR はその決定を
  credential 表に反映するだけで、受理検査の内容には立ち入らない。表の帰結として、Codex が起草した実装
  issue 本文はデータとして扱われ、worker が契約スキーマで検証・正規化して起票する — これは credential を
  持たせないことの帰結であって、規約ではない。
- **worker の job token は操作の種類ごとに分割して発行する** — push 用は `contents:write` のみ、PR 用は
  `pull_requests:write` のみ、issue 起票用は `issues:write` のみ。**「複数の write を 1 本に相乗りさせ
  ない」を発行関数の不変条件とする**。これが本 ADR 固有の決定である。実装 issue の起票は worker の仕事
  なので、`issues:write` を worker が持つこと自体は必要である — 与えないのは Crucible に対してであって、
  worker に対してではない。
- **Fable には `issues:write` だけを与え、直接経路を許すが無検査にはしない** — タスク issue の起票を
  broker 経由に変えることは可能だが、Fable は claude.ai 上のマネージド実行で worker とは別系統に生きて
  おり、worker 停止中も仕様策定が進めることに価値がある (運命分離)。代わりに **Fable が書く本文も同じ
  scan 関数を通すことを要件とし**、通過率を測定する ([ADR 0014](0014-add-security-to-ility-priority-order.md)
  / [ADR 0016](0016-quantum-scoped-fitness-functions.md))。**適用点が worker の外にあるため、これは層 2 の
  中で最も弱い箇所である**と明記しておく。
- **push 先の制限を token に期待しない** — installation token は branch を絞れないため、制限は
  [ADR 0015](0015-capability-broker-instead-of-container-credentials.md) の受理検査が表現する。ruleset
  (`codex/**` 以外への push 禁止、force-push 禁止、`.github/workflows/**` および契約スキーマ・ac-verify
  スクリプトの変更は required review) は**その検査を代替するものではなく、独立した第 2 の網**として
  重ねる。
- token の撤回経路は App の installation suspend とする。鍵の rotation 手順と侵害時の suspend 手順を
  運用文書に持つ。
- **App 化は Phase 1 の完了条件**とする。PAT の暫定運用は Phase 0-1 の開発中に限り、**権威分離の無い
  dev モードであることを明示する**。パイロットの本運用 (Phase 2 の開始条件) に App 化を含める。

### merge を機械から遠ざける 3 層と、覆えていない経路

**この 3 層は「人間だけが merge できる」を機構的に保証しない。** 保証できるのは「Crucible は merge
できない」までで、**worker 自身が merge API を呼べてしまう経路は 3 層のどれも塞いでいない** — worker は
push のために `contents:write` を持ち、merge API がその permission だけで通るなら worker は merge を実行
できる。したがって本節が達成するのは **機械 merge の経路を減らすこと**であって、human-only merge の証明
ではない。単一の機構に賭けず 3 層を重ね、**どれが機構でどれが不変条件か、そして何を覆えていないか**を
書き分ける。

- **第 1 層 (構造・load-bearing。ただし覆う範囲は Crucible の経路のみ)**: Crucible は credential 自体を
  持たないので merge API に到達できない。第 2・第 3 層が実測で不成立でもこの層は残るため、**「Codex が
  merge しない」の根拠はこれで足りる**。しかし**この層は worker の経路には何も効かない** — worker は
  credential を持つ側だからである
  ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md) の worker 侵害の残余)。
- **第 2 層 (worker 内の不変条件。成立するかどうかが未確定)**: 上記の token 操作別分割。**この層が merge
  を止められるのは「merge API が `contents` と `pull_requests` の両方の permission を要する」場合に限る**。
  **その前提は確認できていない** — 公開文書には merge が `Contents: write` のみで通ると読める記述があり、
  それが正しければ **push 用 token (`contents:write`) だけで merge API を呼べてしまい、第 2 層は merge に
  対して何の制限にもならない**。merge を gate する permission の特定は Phase 1 の実測項目 (R2 残) であり、
  **実測で「contents だけで通る」と出たら、この層は merge 制御としては数えない** (操作別分割そのものは
  最小権限として残す価値があるので、分割はやめない)。
  加えて、仮に両方必要だったとしても**これは GitHub が強制する機構ではなく、worker の token 発行関数が
  守る不変条件である** — 発行関数にバグがあれば成立しない。[ADR 0004](0004-two-human-approval-gates.md)
  の Erratum を参照。
- **第 3 層 (未実測・load-bearing にしない。ただし worker の経路を覆える唯一の候補)**: code repo の
  ruleset で master の update bypass を人間アクターのみに限定する。**ruleset の restrict-update が merge
  API の呼出 actor に効くかは公式の明文がなく、Phase 1 の実測項目 (R3)** である。auto-merge は ruleset が
  再評価されないという報告があるため使用禁止。**3 層のうち worker の merge を機構で止めうるのはこの層
  だけである** — R3 が不成立なら、worker が merge しないことは「worker が merge API を呼ばない」という
  プログラム上の不変条件だけに支えられ、第 2 層と同じ強さしか持たない。実測で不成立なら、この層は無い
  ものとして扱う。

**worker の Integration bypass は state repo の ruleset に対してのみ張り**、code repo 側ではどの
credential も bypass actor に登録しない ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md) の
Considered Options が「code repo の ruleset へ権威用の bypass を張ると人間の merge ゲートが緩む」ことを
S1 却下理由に挙げている、その裏返し)。review dismissal も人間アクターに限定する。

**呼称の条件**: `human-only merge` と呼んでよいのは、**worker の経路を機構で覆えた場合に限る — すなわち
R3 が成立した場合だけ**である。R3 が不成立なら、第 1 層と第 2 層が健在でも呼称は **`human approval gate`**
(人間が通す運用上のゲートであって、機械が通れない機構ではない) とし、**worker による merge を残余として
明記する**。第 1 層の成否はこの条件を左右しない — 覆うのが Crucible の経路だけだからである。

## Considered Options

- **単一の PAT で全てを賄う** — 実測で bypass actor の粒度が人間と一致してしまい、worker と人間の書き
  込みを分離できない。[ADR 0012](0012-write-authority-by-lease-and-sha-cas.md) の lease が token の層
  では支えられず、「書けるが書かない約束」に退化する。開発中の暫定としてのみ許す。却下。
- **Crucible に job 別の短命 token を渡し、ruleset で補正する (本 ADR の初版)** — token は「push 可否」
  の二値しか表現できず、危険な push (`codex/**` 外・secret 入り履歴) と安全な push を区別できない。
  ruleset は branch 単位の補正しかできず、pre-receive hook は github.com には置けない。**引数レベルの
  認可が GitHub 側に存在しない以上、この線は原理的に完成しない**。
  [ADR 0015](0015-capability-broker-instead-of-container-credentials.md) の B3 に置き換えた。ただし
  PoC ② が不成立の場合の第 2 fallback としては保持する。却下。
- **Crucible に `issues:write` を与え、自分で実装 issue を起票させる** — 起票の手間は減るが、Codex
  が起草した本文がそのまま指示として読まれる経路ができる。信頼のロンダリングであり、repo 境界では塞げ
  ない。credential ゼロにした現在は選択肢としても存在しない。却下。
- **token で push 先の branch を絞る** — installation token は branch を絞れない。表現できない制約を
  token に期待しない。却下。
- **worker の job token を 1 本にまとめる (`contents` + `pull_requests` の両方入り)** — 実装は単純に
  なるが、merge を人間に限定する第 2 層がそこで消える。1 本にまとめる利益は発行呼び出しが 1 回減ること
  だけで、失うものと釣り合わない。却下。
- **App の秘密鍵を VM のディスクに置く** — 使い捨て VM の前提と噛み合わず、イメージやスナップショットに
  鍵が残る経路ができる。起動時に secret store から取得する形にする。却下。
- **App に merge 権限を与えて「機械 merge + 事後レビュー」にする** — [ADR 0004](0004-two-human-approval-gates.md)
  の承認ゲート 2 点と正面から衝突し、人間ゲートを自ら壊す。却下。
- **Crucible に state repo への読み取り権限だけ与える** — 読めれば lease の内容が Codex の context に
  入り、権威面の語彙が「データとして引用される」経路に載る。必要が無いので与えない。却下。

## Consequences

### Positive

- 権威面への書き込みが credential の層で 1 者に閉じる。lease は約束ではなく「**書けないから書かない**」
  で支えられる。
- **Crucible が侵害されても盗める GitHub credential が無い**。injection が防げないことは前提であり
  ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md) の Context)、盗る物を置かない
  ことがその前提と噛み合う唯一の設計である。コンテナの残骸から token が漏れる経路も消えた。
- Codex が契約ブロックを偽造する経路が構造で塞がる。信頼境界 (指示 / データ) の線が、機構の線と一致する。
- 侵害時の撤回が installation suspend という 1 操作になる。撤回対象を探し回らなくてよい。
- **機械 merge の抑止が単一の機構に賭けられていない**。3 層のうち load-bearing なのは第 1 層だけで、未実測の
  第 2・第 3 層が倒れても「Codex は merge しない」は残る。何が実測待ちで、どの経路が覆えていないかが
  読んで分かる。

### Negative

- **consumer のセットアップが増える**。GitHub App の作成・インストール・権限設定・secret store の用意
  が、パイプラインを使い始める前の前提になる。PAT を貼るだけでは動かない。
- **token 発行の実装を自前で持つ**。JWT 署名・installation token の取得・失効・job への受け渡しが worker
  のコードになり、**そこ自体が新しい攻撃面**である。
- **secret store という外部依存が増える** ([ADR 0010](0010-resident-worker-with-codex-python-sdk.md) の
  常駐 VM と合わせて 2 つ目)。secret store が落ちれば worker は起動すらできない。
- App 化が Phase 1 の完了条件なので、**Phase 0-1 は権威分離が無い状態で走る**。この期間の実測結果は、
  権威分離の検証としては使えない。
- **書き込み範囲が token の権限表・broker の受理検査・ruleset の 3 箇所に分かれた**。token の権限だけを
  読んでも実際にどこへ何を書けるかは分からず、監査は 3 つを突き合わせる必要がある。初版の 2 箇所より
  読みにくい。
- 秘密鍵と全 credential が worker に集中するため、**worker の VM 自体が最も価値の高い攻撃対象になる**。
  Crucible から credential を外した分だけ、worker 侵害時の被害は相対的に大きくなった。緩和は uid 分離
  (broker 検査は鍵を継承しない別 uid の子プロセス) だけで、消えはしない。
- **第 2 層が「実装が正しければ成り立つ」層である**。GitHub は両方入りの token の発行を止めてくれない
  ので、発行関数のバグや将来の「便利だから 1 本にまとめる」変更で静かに崩れる。テストで守るしかない。
- **第 2・第 3 層の前提が未実測のまま ADR に載っている**。merge を gate する permission (R2 残) と
  ruleset × merge API (R3) の実測結果によっては、この節を書き直すことになる。

### Neutral

- 権限表の細部 (どの job にどのスコープを配るか) は two-way door。**GitHub App というアーキテクチャの
  選択は one-way door に近い**。Crucible への credential 配布を再開することも技術的には容易だが、
  [ADR 0015](0015-capability-broker-instead-of-container-credentials.md) の判定原理を捨てることを意味
  する。
- 本 ADR は [ADR 0009](0009-ility-priority-order.md) が「未起票の下流決定」として挙げていた token の分離
  に相当する。
- 本 ADR は **credential を誰が持つか**を決める。持たない実行体が GitHub に成果を届ける**手段**は
  [ADR 0015](0015-capability-broker-instead-of-container-credentials.md) が決める。
- [ADR 0004](0004-two-human-approval-gates.md) の「App に merge 系権限を与えない = GitHub の機構」という
  記述は不正確であり、同 ADR の Erratum で訂正されている。本 ADR の 3 層がその訂正後の正しい姿である。
