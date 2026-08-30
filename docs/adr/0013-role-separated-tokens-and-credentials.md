# トークンと credential を役割で分離する

Status: accepted (2026-08-29)

Driver: [安全性 (Secure by Design)](0014-add-security-to-ility-priority-order.md) — 権限は既定で最小、
境界の強制は規約ではなく機構で行う。同時に
[-ilities 1 可監査性・回復可能性](0009-ility-priority-order.md) が要求する権威の一意性を、token の層で
支える。用語は [CONTEXT.md](../../CONTEXT.md) に従う。

## Context

このパイプラインには書き手が 3 種いる。権威面と派生面を書く worker (Foreman)、実装差分を書く Codex
実行コンテナ (Crucible)、merge する人間である。3 者が同じ credential を共有すると、
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
- **installation token は branch を絞れない。** push 先の制限は token では表現できず、ruleset で表現
  するしかない。

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
| 権威面 (state repo) への書き込み | **worker のみ** | state repo | lease・遷移・予算・heartbeat |
| Codex の auth (ChatGPT auth または API key) | **worker のみ** | — | Codex SDK の認証 ([ADR 0010](0010-resident-worker-with-codex-python-sdk.md))。Codex thread は VM 上の worker 側の部品であり、Crucible とは別物 |

- **Crucible に GitHub credential を配布しないのは構造的な決定**である。`issues:write` を外すだけでは
  契約ブロック偽造しか塞げず、push/PR の引数 (branch・パス・payload) は token では制約できない。Codex が
  起草した実装 issue 本文はデータとして扱い、worker が契約スキーマで検証・正規化して起票する — これは
  credential を持たせないことの帰結であって、規約ではない。
- **worker の job token は操作の種類ごとに分割して発行する** — push 用は `contents:write` のみ、PR 用は
  `pull_requests:write` のみ。**「両方入りの token を作らない」を発行関数の不変条件とする**。
- **push 先の制限は broker の受理検査で表現する** — `codex/**` 一致・保護パス・全 commit patch の scan
  に合格した SHA だけを worker が push する ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md))。
  ruleset (`codex/**` 以外への push 禁止、force-push 禁止、`.github/workflows/**` および契約スキーマ・
  ac-verify スクリプトの変更は required review) は**この検査を代替するものではなく、独立した第 2 の網**
  として重ねる。
- token の撤回経路は App の installation suspend とする。鍵の rotation 手順と侵害時の suspend 手順を
  運用文書に持つ。
- **App 化は Phase 1 の完了条件**とする。PAT の暫定運用は Phase 0-1 の開発中に限り、**権威分離の無い
  dev モードであることを明示する**。パイロットの本運用 (Phase 2 の開始条件) に App 化を含める。

### merge を人間に限定する 3 層

human-only merge を単一の機構に賭けない。3 層を重ね、**どれが機構でどれが不変条件かを書き分ける**。

- **第 1 層 (構造・load-bearing)**: Crucible は credential 自体を持たないので merge API に到達できない。
  第 2・第 3 層が実測で不成立でもこの層は残るため、**これが人間 merge の実質的な根拠である**。ただし
  この層が守るのは Crucible からの経路だけで、**worker 自身の侵害は覆わない**
  ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md) の残余)。
- **第 2 層 (worker 内の不変条件)**: 上記の token 操作別分割。merge は `contents` と `pull_requests` の
  両方の permission を要する見込みで、片方しか持たない token では呼べない。**これは GitHub が強制する
  機構ではなく、worker の token 発行関数が守る不変条件である** — 発行関数にバグがあれば成立しない。
  加えて、merge を gate する permission の特定自体が Phase 1 の実測項目 (R2 残) であり、前提が確定して
  いない。[ADR 0004](0004-two-human-approval-gates.md) の Erratum を参照。
- **第 3 層 (未実測・load-bearing にしない)**: code repo の ruleset で master の update bypass を人間
  アクターのみに限定する。**ruleset の restrict-update が merge API の呼出 actor に効くかは公式の明文が
  なく、Phase 1 の実測項目 (R3)** である。auto-merge は ruleset が再評価されないという報告があるため
  使用禁止。実測で不成立なら、この層は無いものとして扱う。

**worker の Integration bypass は state repo の ruleset に対してのみ張り**、code repo 側ではどの
credential も bypass actor に登録しない ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md) の
Considered Options が「code repo の ruleset へ権威用の bypass を張ると人間の merge ゲートが緩む」ことを
S1 却下理由に挙げている、その裏返し)。review dismissal も人間アクターに限定する。

PoC が不成立で第 1 層を諦め (B1 への fallback)、かつ第 2・第 3 層も実測で成立しなかった場合にのみ、
human-only merge の主張を「human approval gate」に改名し、機械 merge の可能性を残余として明記する。

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
- **Codex コンテナに `issues:write` を与え、自分で実装 issue を起票させる** — 起票の手間は減るが、Codex
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
- **merge の禁止が単一の機構に賭けられていない**。3 層のうち load-bearing なのは第 1 層だけで、未実測の
  第 2・第 3 層が倒れても human-only merge は残る。何が実測待ちかが読んで分かる。

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
