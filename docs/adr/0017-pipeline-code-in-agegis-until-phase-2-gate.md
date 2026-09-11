# パイプライン実装コードは Phase 0-1 の間 agegis に同居させ、Phase 2 前に再判定する

Status: accepted (2026-09-11) — [ADR 0016](0016-quantum-scoped-fitness-functions.md) を amend する。
0016 は supersede せず、Status は accepted のまま据え置く。0016 が Tribunal の保護パスに挙げた
**「検査コード」の外延が未定義**だったので、本 ADR が Customs のソースを含むことを確定する。
関連: [ADR 0011](0011-authority-state-in-dedicated-state-repo.md) (置き場を repo 境界で表現した先例と
one-way door の扱い)、[ADR 0013](0013-role-separated-tokens-and-credentials.md) (credential の役割分離と
App 化の Phase 条件)、[ADR 0014](0014-add-security-to-ility-priority-order.md) (検査は worker 配備版の
設定のみを使う)。

Driver: [安全性 (Secure by Design)](0014-add-security-to-ility-priority-order.md) — 「被検査側が検査を
無効化する経路を塞ぐ」を、repo 境界という置き場の話ではなく機構として保つこと。ただし本 ADR は特性から
導かれる決定ではなく置き場の選択であり、**判断を実際に動かしたのは可逆性** — Phase 2 まで移設コストが
moderate に留まるという事実である。用語は [CONTEXT.md](../../CONTEXT.md) と、仮名については
[ADR 0016](0016-quantum-scoped-fitness-functions.md) に従う。

## Context

#137 は「パイプラインの実装コードをどの repo に置くか」を問うた。これまでの ADR 群はこれに答えていない。

**設計は repo という identity を 3 つしか持っていない。** 権威レコード専用の Ledger
([ADR 0011](0011-authority-state-in-dedicated-state-repo.md))、Codex が実装差分を書く対象である
code repo (agegis — #130 (Phase 2) の実コード投入で code repo になり、本運用は Phase 4)、そして Charter
の配布元である skills repo (rulesync の tag、[RELEASING.md](../../RELEASING.md)) の 3 つである。
**パイプライン実装コード — Foreman 本体、Customs、そして #122 が出す判定純関数 — の置き場はこの
どれでもない。** credential の表
([ADR 0013](0013-role-separated-tokens-and-credentials.md))、権威面の表
([ADR 0011](0011-authority-state-in-dedicated-state-repo.md))、quantum の表
([ADR 0016](0016-quantum-scoped-fitness-functions.md)) のいずれにも現れない。裏返せば、**これまでの設計は
配備物にのみ束縛されており、ソース木の所在に対して indifferent である** — 特性も credential も配備単位で
定義され、ソースがどの repo にあるかに依存する記述が 1 つも無い。

**「同居させると被検査側が検査を書き換えられる」という直観は、2 つの事実で崩れる。**

1. **scanner の設定については、検査の実体は作業ツリーを見ていない。**
   [ADR 0014](0014-add-security-to-ility-priority-order.md) は層 2 (broker pre-flight) と層 3
   (CI required check) の双方について、**scanner の設定・baseline・ignore は worker 配備版のみを使い
   repo 内の設定は無視する**、**config は worker 配備版から供給し契約の「3 点同時 bump」に編入する**と
   明記している。つまり Codex が code repo 内の scanner 設定を編集しても、走っている検査子の設定は
   変わらない。変更が届く経路は release と 3 点同時 bump だけである。**ただし 0014 がこの規則を課したのは
   scanner の設定・baseline・ignore に対してであって、検査コード一般ではない** — Tribunal の ac-verify は
   repo に住み Actions から走るスクリプトであり、これを守っているのは配備経路ではなく、
   `.github/workflows/**`・契約スキーマ・ac-verify スクリプトの変更を required review にする
   [ADR 0013](0013-role-separated-tokens-and-credentials.md) の ruleset — すなわち**保護パス検出と人間の
   review** である。0014 の「被検査側が検査を無効化する経路を塞ぐ」が **repo 境界ではなく配備経路で
   達成されている**のは scanner の設定に限られ、その範囲では同居はこれを破らない。
2. **GitHub App の installation 先は Ledger である。** #129 の AC は App を state repo に入れるものであり、
   [ADR 0013](0013-role-separated-tokens-and-credentials.md) の credential 表でも token を持つのは
   code repo と state repo だけで、実装コードを置く repo は App の対象に現れない。したがって
   **App 化 ([ADR 0013](0013-role-separated-tokens-and-credentials.md) の Phase 1 完了条件) は、この置き場を
   縛らない。**

**扉が閉まるのは #130 (Phase 2) の実コード投入である。** workflow の `uses:` ref と SHA pin
([ADR 0002](0002-consolidate-on-consumer-update.md) の SHA pin + Renovate 追随)、ruleset に焼かれた
required check 名、worker 配備版のビルド / リリース経路と「3 点同時 bump」の台帳
([ADR 0014](0014-add-security-to-ility-priority-order.md)) — この 3 つが同時に固まる地点で、移設コストが
trivial から moderate に変わる。Phase 0-1 の間は「後で移せる」が事実として真であり、Phase 2 以降も
moderate が積み上がるだけで有界である。

なお **#122 (Phase 0) の成果物は、どの案を採っても 2 repo に割れる。** `.coderabbit.yaml` と issue テンプレ
は PR が起きる code repo にしか置けず、契約スキーマ・遷移表・pl-event 語彙は Charter として skills repo に
属する一方、判定純関数 (Python、domain primitive、clock DI) はそのどちらでもないので実装側に属する。
#122 はこの割れを書き分けていない。**本 ADR の選択に依らず有効な指摘**なので、#122 の AC 側で片付ける。

## Decision

**Phase 0-1 の間、パイプライン実装コード (Foreman 本体・Customs・判定純関数) を code repo である agegis に
同居させる。** 専用の実装 repo は今は切らない。以下の 3 条件を伴う。

### 条件 1 — 保護パスを repo 単位ではなく subdirectory 単位で定義する

保護対象は **Customs のソース / ac-verify / `.github/workflows` / 契約スキーマ / scanner 設定 /
worker 配備版の release 経路**とする。**Foreman の通常コードは保護パスの外**に置き、Codex の開発 PR が
全件 `needs-human` に倒れるのを避ける。保護パスは repo 境界と一致する必要が無く、「Tribunal を骨抜きに
するか全件 `needs-human` にするか」の二択は成立しない。

**保護パスの定義そのものも worker 配備版から供給し、repo 内の定義は無視する。** どの ADR も 0015 の
受理手順 3 が使う保護パスの一覧がどこに住むかを決めておらず、同居下でそれが被検査側の作業ツリーに
あると、条件 1 の機構が自分で自分の対象を書き換えられることになる。scanner の設定と同じ扱い
([ADR 0014](0014-add-security-to-ility-priority-order.md)) にして「3 点同時 bump」に編入する。

あわせて、[ADR 0016](0016-quantum-scoped-fitness-functions.md) が Tribunal の改変防止で保護パスに挙げた
**「検査コード」の外延を本 ADR で確定する — Tribunal の ac-verify に加え、Customs (broker pre-flight) の
ソースを含む。** 0016 はこの外延を書いておらず、同居する以上は安全側に倒す必要がある。**これが本 ADR が
0016 を amend する内容である。** amend はもう 1 点あり、**保護パスに worker 配備版の release 経路
(と、上記の保護パス定義の供給経路) を加える**。0016 が定めた「保護パスに触れる PR の `needs-human`
化率 100%」という閾値そのものは変えないが、**測定対象の外延は広がる**。

### 条件 2 — 置き場を再判定する gate を #121 の Phase 2 前提列に置き、#130 にも blocked-by として写す

**gate も時点ではなく状態で定義する** (下記「再考のトリガ」と同じ流儀)。「Phase 2 の実配備開始」のような
時点語は #121 / #130 / [CONTEXT.md](../../CONTEXT.md) のどこにも無く、#121 の Phase 2 は「codex-impl を
タスク 1 件・直列」、本運用は Phase 4 である。**次のいずれかが固まった時点で、「継続するか、専用 repo へ
移設するか」を再判定する。いずれも観測されないままでも、#130 (Phase 2) の実コード投入より前には必ず
判定する。**

1. **実装コードを指す workflow の `uses:` ref / SHA pin が置かれた**
   ([ADR 0002](0002-consolidate-on-consumer-update.md))。
2. **実装コードに由来する required check 名が agegis の ruleset に焼かれた。**
3. **worker 配備版のビルド / リリース経路と「3 点同時 bump」の台帳が存在するようになった**
   ([ADR 0014](0014-add-security-to-ility-priority-order.md))。

**この gate は ADR 本文ではなく issue の受け入れ条件に 1 行として埋め込む。**
[CONTEXT.md](../../CONTEXT.md) の承認ゲートは merge と差し戻し時の再検討の 2 点しか無く、**ADR に
「再判定する」と書いただけでは誰も止まらない**。置き場は **#121 の Phase 2 前提列を primary とする** —
#121 が自身を single source of truth と宣言しているためである。**#130 の依存 / AC には blocked-by の
注記として写す** (secondary)。**ただしこれは規約であって機構ではない** — 下記 Neutral の自己言及のとおり、
本 ADR は repo 境界で何も強制しておらず、AC の 1 行を誰も読まなければ gate は発火しない。

再判定の結論が移設なら、ソースと git 履歴の切り出し (機械的で commit 数に比例するだけ) に加えて、
次の 4 つを移行手順とする。(a) workflow の `uses:` ref と SHA pin の張り替え、(b) ruleset /
branch protection / required check 名の再設定、(c) 実装 issue と cross-repo sub-issue 階層の移送
(件数に線形で、transfer は番号が変わり参照が腐る)、(d) worker 配備版のビルド / リリース経路と
「3 点同時 bump」台帳の移設。`.coderabbit.yaml`・issue テンプレ・Charter consumer の配線・Ledger の
`lane/<issue>` 参照は **移さない** — いずれも code repo 側または skills repo 側の資産であり、実装コードの
置き場が変わっても動かない。

### 条件 3 — 多 repo 化が決まった時点で即再判定する

2 つ目の Codex 対象 code repo を持つと決めた時点で、Phase 2 を待たずに再判定する。
[ADR 0011](0011-authority-state-in-dedicated-state-repo.md) の `lane/<issue>` は単一 code repo を前提と
しており、多 repo 化は lane の名前空間を `lane/<repo>/<issue>` へ広げることを要求する。同居の前提が
そこで崩れる。

### 再考のトリガ

**トリガは時点ではなく状態で定義する** ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md)
と同じ流儀)。「Phase 2 で見直す」だけでは、Phase 2 を過ぎた後に前提がずれても誰も気づかない。以下の
いずれかが観測されたら置き場を再審理する。

1. **多 repo 化 (2 つ目の Codex 対象 code repo) が決まった** — 条件 3 の即時再判定に当たる。同居は
   ここで不適合に転ずる。
2. **Charter を skills repo から出す決定が下りた** — Charter と worker 配備版を同一 repo に置く案
   (下記 C) が再浮上し、「3 点同時 bump」の原子性の比較がやり直しになる。
3. **Fable を自前でホストする決定が下りた** — quantum が 1 つ増え、その置き場で同じ問題を再演する。
4. **hermes 経由の依頼ルートが実装された** — broker の呼び出し元が agegis 側に増え、関心混在の度合いが
   変わる。
5. **R2 / R3 (#124 / #125) が不成立と実測された** — ruleset 設計と、`human-only merge` と呼んでよいかの
   条件 ([ADR 0013](0013-role-separated-tokens-and-credentials.md)) が変わり、code repo 側の保護設計の
   重みが変わる。
6. **agegis が public になる、または共同作業者が増えた** — 下記 Neutral の前提が崩れ、関心混在と
   CODEOWNERS の二重性が跳ねる。
7. **Customs の変更頻度が高く、保護 subdirectory の `needs-human` が開発の邪魔になった** — 検査コード
   だけを別 repo に出す案 (下記 A') が再浮上する。
8. **[ADR 0011](0011-authority-state-in-dedicated-state-repo.md) の substrate 再考トリガ 3 条件のいずれかが
   観測された** — substrate の再審理に本 ADR の置き場も含める。

## Considered Options

- **専用の実装 repo を今切る** — 関心分離が得られ、Ledger と同列の一貫性があり、多 repo 化に無改造で
  耐える。しかし **「被検査側が検査を書き換える」問題を消さず移設するだけ**である。パイプライン自身を
  Codex に開発させる (dogfooding を続ける) なら実装 repo が 2 つ目の code repo になり、多 repo 化の
  コストをこの案自身が引き込む。加えて Charter が skills repo、workflows が code repo、worker 配備版が
  実装 repo と 3 分裂し、**「3 点同時 bump」の原子性が 0/3 と全案中最悪**になる。secret store と ruleset の
  設定が 1 組増え、[ADR 0011](0011-authority-state-in-dedicated-state-repo.md) が Negative に挙げた
  「人間が 1 つの画面で全体を追えない」も悪化する。Phase 0-1 で払う必要のないコスト。却下。
- **Foreman + Customs + Charter を別 monorepo にまとめる** — Charter と worker 配備版が原子的に bump
  できるのは本案より優れる。しかし #137 で確定した「skills repo = Charter の配布元」と正面衝突し、
  [RELEASING.md](../../RELEASING.md) の `rulesync fetch` 契約と、agegis / dotfiles 両 consumer の配線
  ([ADR 0002](0002-consolidate-on-consumer-update.md)) を壊す。**解こうとしている問題の外側にある
  one-way door を同時に開く。** 却下。
- **PoC 用 repo (`pl-substrate-poc`) を昇格させる** — 「新設コストが実質ゼロ」は偽である。**PoC の
  成果物が現存する repo を引き継ぐ**ということは、ruleset で保護できない `refs/pl/**`
  ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md) /
  [ADR 0012](0012-write-authority-by-lease-and-sha-cas.md) の実測) の残留と、PoC 期の
  PAT・collaborator・bypass の棚卸しを引き継ぐということである。加えて rename は両 ADR の出典表記を
  腐らせる。新規作成の方が安い。却下。
- **Customs / ac-verify だけを別 repo に出す** — [ADR 0016](0016-quantum-scoped-fitness-functions.md) の「
  Customs は Foreman の内側に置いた」は**運命共有 (デプロイ / 障害) の主張であってソース repo の主張ではな
  い**ので、案としては成立する。しかし Customs の scanner 設定は worker 配備版しか見ず (ADR 0014)、
  ac-verify は別 repo に出しても保護パス検出と人間の review で守られることに変わりはないので、repo を 1 つ
  増やして得られる追加保証が薄い。**却下ではなく保留** — 再考トリガ 7 で再浮上させる。
- **skills repo の subdirectory に置く** — #137 §① で既に却下済み (2026-08-31)。**以下の理由付けは
  本 ADR の補足である** — Charter の配布物と実装が同一 repo にあると、
  [RELEASING.md](../../RELEASING.md) のタグ (consumer の `rulesync fetch` が指す版) が実装の版と絡む。
  記録のみ。
- **「Phase 2 で必ず専用 repo へ移設する」と今決めておく** — Phase 0-1 の摩擦は本案と同じだが、移設が
  Phase 2 着手のクリティカルパスに乗り、repo 設定を 2 回払うことが確定する。**「必ず移設」を「再判定する
  gate」に緩めた** — 多 repo 化の是非、hermes 経由ルート、Fable の自前ホスト化はいずれも未決の隣接判断で、
  Phase 2 時点で得られる情報が結論を変えうる。結論を先に固定するより、**判定の場を固定する方が安い**。
  なお gate を issue の AC に 1 行として埋めるという緩和策は、この案から採って条件 2 にした。

## Consequences

### Positive

- **立ち上げの摩擦が最小になる。** Phase 0 の時点で既に中身を持つ判定純関数を、repo の新設・ruleset の
  設定・secret store の配線を待たずに書き始められる。
- **dogfooding の距離がゼロになる。** パイプラインの実装差分そのものが Codex の作業対象になり、
  パイプラインが自分自身を通る。検査・レビュー・merge ゲートの体験が最初の実装から得られる。
- **「3 点同時 bump」の原子性が 2/3 になる。** workflows の ref と worker 配備版が同一 repo で揃い、
  Charter だけが skills repo の tag として残る。専用 repo を切る案 (0/3) より良い。
- **`lane/<issue>` の単一 code repo 前提と整合する** ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md))。
  lane の名前空間を今は拡張しなくてよい。
- **管理対象の repo を増やさない。** [ADR 0011](0011-authority-state-in-dedicated-state-repo.md) が
  Negative に挙げた「repo が 1 つ増える」(作成・ruleset 設定・バックアップ・GC) を、今は払わない。

### Negative

- **agegis とパイプラインが運命を共有する。** パイプライン側の障害 — 壊れた workflow、暴走した job、
  失効した secret — が agegis の CI と権限設定に直接波及する。
  [ADR 0016](0016-quantum-scoped-fitness-functions.md) が quantum 間で切り分けた運命を、ソース木の同居で
  部分的に結び直している。
- **agegis の required check に Python 系の検査が混ざる。** agegis は pnpm、Foreman は Python
  ([ADR 0010](0010-resident-worker-with-codex-python-sdk.md)) なので、agegis の PR が自分と無関係な check の
  完了を待つ。**PR の緑化が遅くなるという運用上の代償**であり、Foreman の thread 実行時間の閾値
  ([ADR 0016](0016-quantum-scoped-fitness-functions.md)) に効く話ではない — そこを混同しない。
- **同居下では、走っている検査子の完全性は最終的に保護パス検出と人間の merge に帰着する。** 配備経路が
  効くのは scanner の設定・baseline・ignore までで ([ADR 0014](0014-add-security-to-ility-priority-order.md))、
  repo に住んで Actions から走る ac-verify を守るのは保護パスの検出とそれが強制する人間の review である
  ([ADR 0013](0013-role-separated-tokens-and-credentials.md) の required review)。**これは機構ではなく
  規約に近い** — 別 repo に出せば得られた「被検査側から触れない」を、同居では買っていない。
- **worker の push 用 job token が、自分のソース木を含む repo への `contents:write` を持つ**
  ([ADR 0013](0013-role-separated-tokens-and-credentials.md))。installation token は branch を絞れないので、
  この権限の形は変えられない。broker の受理検査 (機構。ただし worker 侵害では消える) と code repo の
  ruleset (機構) が push 先を `codex/**` に限り、merge は [CONTEXT.md](../../CONTEXT.md) の人間による
  承認ゲートに委ねる。**R2 / R3 が未実測である以上、worker が merge API を呼べない保証は無い**
  ([ADR 0013](0013-role-separated-tokens-and-credentials.md))。いずれにせよ **「そもそも届かない」という
  構造的排除は失っている**。
- **関心が混在する。** README・CODEOWNERS・ruleset が「agegis というプロダクトの規則」と「パイプラインの
  規則」の二重の意味を持つ。読み手は毎回どちらの文脈かを判断することになり、この負担は共同作業者が
  増えるほど大きくなる。
- **Charter の consumer の中に、Charter の版整合を強制するコードが住む。** agegis は rulesync の consumer
  ([ADR 0002](0002-consolidate-on-consumer-update.md)) であり、Foreman のソースがその作業ツリーにあると
  **未リリースの Charter をローカル参照する経路**が自然にできる。
  [ADR 0016](0016-quantum-scoped-fitness-functions.md) の「版が不一致なら worker の起動を拒否する」が
  「機構」から「気をつける」に退化しやすい (drift check の実装形は未規定である)。
- **移設を選んだ場合、moderate なコストを後払いする。** Phase 2 以降は workflow の `uses:` ref と SHA pin、
  ruleset の required check 名、cross-repo sub-issue 階層、worker の release 経路と bump 台帳が同時に
  固まっており、移設はそれらをまとめて触ることになる。有界ではあるが無料ではない。

### Neutral

- 以下は **人間が確認していない推定であり、本 ADR の前提として明記する**。崩れたら再審理に当たる。
  - **agegis は private で、作業者は 1 人**である。
    [ADR 0011](0011-authority-state-in-dedicated-state-repo.md) /
    [ADR 0013](0013-role-separated-tokens-and-credentials.md) が「パイロットの agegis」を private repo の
    実測例として引くことからの推定で、明文は無い。関心混在と CODEOWNERS の二重性を受け入れられるのは、
    この前提の下でだけである。
  - **Phase 4 の本運用対象は当面 agegis のみ**である。dotfiles は rulesync の consumer
    ([ADR 0002](0002-consolidate-on-consumer-update.md)) であって Codex の code repo ではない。
    **多 repo 化はまだ仮定であって既定ではない。**
  - **Charter は skills repo に留まる。** [RELEASING.md](../../RELEASING.md) の `rulesync fetch` 契約が
    consumer 側の配線の前提になっている。
- 本 ADR が決めるのは **ソース木の所在だけ**である。quantum の切り方 (独立にデプロイ可能な単位) は変えて
  いない — Customs が Foreman の内側であることも、Charter が独立に版を切れることも、同居では変わらない
  ([ADR 0016](0016-quantum-scoped-fitness-functions.md))。
- **「3 点同時 bump」の原子性は本案でも 2/3 であって 3/3 ではない。** Charter だけは別 repo の tag として
  bump される。3/3 にできるのは Charter を巻き込む monorepo 案だけで、それは却下した。
- **#122 の成果物が 2 repo に割れる件は、本 ADR の選択に依らず有効**である。code repo 側・Charter 側の
  資産と実装側の判定純関数の書き分けは #122 の AC で行い、本 ADR では扱わない。
- **fitness function の測定コード (Foreman の常時測定・Watchtower)
  ([ADR 0016](0016-quantum-scoped-fitness-functions.md)) がどの repo に住むかは、どの ADR も規定して
  いない。** 本 ADR は**測定コードも「パイプライン実装コード」に含める** — すなわち agegis 同居の対象と
  する。Watchtower が Actions の workflow である以上、その多くは元より code repo 側にしか置けない。
- **#129 側に注記が要る (本 ADR では解決しない)。** worker の push / PR / issue 起票用 job token は
  code repo を対象とするので ([ADR 0013](0013-role-separated-tokens-and-credentials.md))、App は agegis
  にも installation されているはずだが、**#129 の AC は state repo への installation しか書いていない**。
  同居下では App の `contents:write` が自分のソース木を覆うことになるため、この含意を #129 側に書き足す
  必要がある。follow-up であって、本 ADR の決定ではない。
- 本 ADR は [ADR 0011](0011-authority-state-in-dedicated-state-repo.md) と違い、**repo 境界で何かを強制
  していない**。ここで決めた同居は機構ではなく配置であり、機構として効いているのは
  [ADR 0014](0014-add-security-to-ility-priority-order.md) の「scanner の設定は worker 配備版のみを使う」
  の方である — **ただし覆うのは設定までで、検査コードそのものは上の Negative のとおり保護パス検出と
  人間の review に帰着する**。
