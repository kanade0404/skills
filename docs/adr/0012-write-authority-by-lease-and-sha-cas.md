# 書き込み権威を TTL lease + epoch fencing で定義し、Contents API の sha-CAS で調停する

Status: accepted (2026-08-29)

Driver: [-ilities 1 可監査性・回復可能性](0009-ility-priority-order.md) (権威が GitHub 上の耐久事実で
あり、実行体が全滅しても復元できること) と同 2 有界性 (全ての待機に数値の上限があること)。並行性は
上 2 つを害さない範囲でのみ得る。用語は [CONTEXT.md](../../CONTEXT.md) に従う。

## Context

権威状態の置き場は [ADR 0011](0011-authority-state-in-dedicated-state-repo.md) で専用 state repo に
決まった。残るのは「誰がいつ書いてよいか」である。

実行体は複数になりうる。再起動が重なることも、別ホストで起動されることも、split-brain になることも
ある。「実行体は 1 つのはず」を仮定した設計は、2 つになった瞬間に安全性を失う。

この決定には 7 巡の設計レビューと 1 回の実測 PoC の経緯がある。記録として残す。

- **v3 (コメント最古勝ち)** — 権威を issue コメントの「最古のものが勝つ」プロトコルに置いた。レビューで
  **勝者規則が二義的** (何をもって最古とするかが読み手によって変わりうる) であり、fencing token を持て
  ないことが判明して棄却。
- **v4 (git ref)** — 権威を `refs/pl/lease/<issue>` に移した。`POST /git/refs` の create-if-absent
  (既存なら 422) と、ref の non-force 更新の fast-forward 制約を、サーバが強制する CAS として使う案。
- **round 4 の実測で v4 を反証** — `refs/pl/*` の non-force 更新は **FF 制約を持たない** (last-write-wins)。
  `refs/pl/**` は **ruleset で保護できない**。create-if-absent の 422 は動くが非文書化。takeover も
  fencing も成立しないことが分かり、v4 は棄却された。
- **2026-08-29 の PoC (pl-substrate-poc) で S2 に確定** — Contents API の sha-CAS は 10 回中 10 回
  「ちょうど 1 人勝ち」。409 は文書化された契約で、non-default branch でも同一挙動。**古い sha での
  DELETE も 409** を返すため、取得から解放までを 1 つのプリミティブで閉じられる。競合検出は branch tip
  単位なので lane 別 branch が必須で、その形で 150 並列 write の競合ゼロを確認した。**rule 違反も CAS
  敗北も同じ 409 / 422** を返すため、失敗の分類は message 本文でしか行えない。

時刻についても実測がある。**Contents API はサーバ時刻を強制する** (commit の日付に偽装値を入れても
無視される) が、**Git Data 経由で作った commit の日付は偽装可能で、読み手には両者を区別できない**。
したがって commit の日付は判定に使えない。

**2026-08-30 の追補** — 初版の「デッドロックは ≤ TTL で有界である」は不正確だった。TTL が制限するのは
「lease が回収**可能**になる時刻」だけで、「誰かが回収しに**来る**時刻」を制限していない。初版は reap
のトリガを「次にその lane の lease を取得しようとする worker」に置いていたため、その lane に誰も来なけ
れば回収は永久に起きない。穴は新しい機構ではなく**トリガの付け替え**だけで閉じる。同じ検討で、単一
worker 構成 (同時 1 installation・1 VM) では「別の worker が拾う」がそもそも起きないという前提の齟齬
も見つかった。

## Decision

書き込み権威を **lane ごとの TTL lease** として定義する。権威は「プロセス」ではなく「lease を現在
保持していること」であり、lease を持つ実行体だけがその lane の権威レコードを書ける。

lease は lane branch 上の単一オブジェクト `state.json` に状態と同居し、取得・renew・release の全てを
Contents API の **sha-CAS** で行う。

- **取得 / release** — いずれも `state.json` の CAS 更新。**release は DELETE ではなく `status=released`
  の追記**とする。レコードを消さないことで、「未取得」「release 済み」「壊れた」の三義を消す。
- **epoch** — `state.json` 内の永続的な単調カウンタ。**増分トリガは lease の取得と takeover のみ**
  (renew では増えない)。スコープは lane。**用途は順序 (fencing) 専用**で、期限判定には使わない。受理
  検査は「書き込みに刻まれた epoch == その lane の現 epoch」。
- **期限判定** — `acquired_at` / `renewed_at` は、**worker が自分の書き込みレスポンスの Date ヘッダ
  (サーバ時刻) を転記した値**である。失効は「サーバ時刻 now − `renewed_at` > TTL」で判定し、now は
  判定者自身の直近 API レスポンスの Date を使う。**実行体のローカル時計は判定に使わない。**
- **renew は時間駆動** — thread の実行中は tick ごとに残 TTL を確認し、**残 TTL < TTL/2 で renew**
  する。renew を含む任意の CAS 敗北は lease の喪失を意味し、そのとき worker は **self-fence** する
  (自分の子 thread / コンテナを即 abort して停止)。**規範不等式は「TTL > 失効検知周期 (tick) + abort
  所要時間」**。TTL は 20min。
- **reap のトリガは scheduler の tick 掃引** — 失効 lease の回収役は「次にその lane の lease を取得
  しようとする worker」ではなく、**毎 tick、全 active lane (≤ 30) の `state.json` を ETag で読み、
  「サーバ時刻 now − `renewed_at` > TTL」の lane を検知した worker** である。検知した worker がその場で
  takeover (CAS 更新 + epoch+1) して reap する。**専任の reaper は居ない — reap は独立したプロセスの名
  ではなく、失効 lease を見つけた worker がその場で行う仕事の名前**である。復元は **intent と transition
  の差分 + code repo 実体の照会** (PR / branch / thread の存在) で行い、**未適用の intent のみを対象と
  する — 完了済みの transition は巻き戻さない**。reap のカウントは復元を要した takeover のみ加算し、
  上限は lineage 通算 3 回。
- **掃引は API 予算の縮退対象から外す** — 予算超過時に周期を延ばすのは dispatch とポーリングであって
  掃引ではなく、掃引は heartbeat / renew と同格に扱う。掃引の読みは ETag + `If-None-Match` で、304 は
  core のレート制限を消費しない ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md) の実測)。
  縮退で掃引まで止めると、最も回復が要る局面で回復の担い手が消える。
- **回復上限の不等式** — `T_recover ≤ TTL (20min) + tick (60s) + T_reap (≤ 2min) ≒ 23min`。TTL が有界に
  するのは**回収可能になるまで**であり、**lane が再稼働するまで**はこの不等式で有界にする。
- **1 lease 区間の未確定 intent は ≤ 3** — reap の照会数は未適用 intent の数に比例するため、この上限が
  ないと T_reap が閉じず、上の不等式が成立しない。上限に達した lane はそれ以上の副作用を起こさず
  needs-human に倒す。
- **同一 `worker_id` による期限内の再取得を認める** — 自分名義の lease は TTL 満了前でも再取得してよい。
  ただし順序を固定する: **runtime 記録から自分名義の残存 thread / コンテナを abort → epoch+1 → 再取得**。
  この設計は同時 1 installation・1 VM が既定で「別の worker が拾う」はほぼ起きないため、これを認めない
  と単一 worker の再起動が毎回 TTL 満了を待つことになる。認めた結果、プロセス死からの実効回復は 23min
  から数分になる。
- **worker が 1 台も居ないときは自動回復しない** — 掃引の担い手が消えるので、上の不等式が成立するのは
  worker が 1 台以上生存している間だけである。全滅は `heartbeat.json` の鮮度でのみ検出され、**鮮度
  30min 超過を失効と判定**して監視 workflow (Watchtower、
  [ADR 0016](0016-quantum-scoped-fitness-functions.md)) が needs-human を通知する。**判定は 30min だが
  通知の到達は schedule の遅延分だけ後ろにずれる** — 遅延は監視側の残余であり、通知までの上限は 0016 の
  fitness function 側で ≤ 45min として測る。監視側に書き込み権威は無く、回復させるのは人間である。
  復旧後の初回 tick 掃引が、失効した全 lane をまとめて回収する。
- **lease が守らないもの** — renew し続けながら前進しない zombie lane は lease では検出できない。これは
  thread の 45min 上限・未 dispatch の 24h starvation・needs-human の 72h 再通知という別の網が受け持つ。
- **副作用を伴う操作は `kind=intent` の write-ahead** を権威面に CAS で先行記録してから実行する。reap
  の復元はこれを前提とする。
- **lane 横断カウンタ (lineage cap / global 予算) は 2 相予約** — ① lineage / global branch に
  `kind=reserve` を CAS (lane_id と当該 lane の epoch を刻む) → ② lane branch で遷移 → ③ `kind=confirm`。
  クラッシュ時は reap が reserve / confirm の差分から復元する。**stale reserve (lane が決着済み /
  epoch が古い / 期限切れ) は scheduler の掃引が `kind=reserve_void` で戻す** — 予約が確定も解放も
  されないまま予算が恒久的に目減りするのを防ぐ。cross-lane の受理検査は「reserve に刻まれた epoch ==
  その lane の現 epoch」で行い、lease を失った worker の予約を棄却する。
- **fail closed** — 失敗は message 本文で分類する。`does not match` / `is at X but expected Y` は CAS
  敗北 (再読込してリトライ)、`Repository rule violations found` はポリシー違反 (escalate)。**未知の
  message は遷移せず needs-human**。**fail closed 時に lease は release しない** — 保持したまま停止し、
  TTL 失効で自然に回収させる。release すると別の worker が同じ未知 message を踏んで無限ループになる。
  デッドロックは上の不等式 (≒ 23min) で有界である。
- 全レコードに `schema_version` を持たせ、**不一致検査は write 受理時のみ**行う (read は自 version 以下
  を受理する)。kind の追加は minor 版として受理を継続し、意味の変更は major 版 + lane drain 後に切替。

## Considered Options

- **単一プロセスを権威にする** — SPOF であり、かつプロセスが 1 つであることを外から保証できない (再起動
  の重なり、別ホストでの起動)。split-brain したときに、どちらが正かを決める機構が無い。却下。
- **issue コメントの最古勝ちプロトコル (v3 案)** — 勝者規則が二義的で、fencing token を刻む先が無い。
  なお楽観的並行制御そのものを棄却したわけではない — 採用案の sha-CAS がまさにそれである。却下。
- **git ref (`refs/pl/*`) を lease の権威にする (v4 案)** — **実測で反証済み**。non-force 更新に FF 制約
  が無く last-write-wins になり、`refs/pl/**` は ruleset で保護できない。takeover の CAS も fencing も
  成立しない。却下。
- **`refs/heads` の FF-CAS** — FF 制約は効くが、ref の DELETE が無条件なので release を保護できず、
  状態の本体を載せる手段も無い。「state を伴わずにちょうど 1 人だけ通す」補助用途に限定する。権威と
  しては却下。
- **外部の分散ロック (Redis / etcd / DB の advisory lock)** — 状態が GitHub の外に出て
  [ADR 0007](0007-github-as-sole-durable-state.md) に違反し、権威が人間に見えなくなる。却下。
- **TTL を Codex thread の時間上限より長くして、正常系で renew を不要にする (v4 の不等式)** — 一見単純
  だが、**renew が起きないと CAS 敗北による self-fence のトリガが消える**。lease を失ったことに気づく
  経路が無くなり、fencing が名前だけになる。検算で見つかった欠陥。却下。
- **lease の DELETE で release する** — レコードの不在が「未取得」「release 済み」「壊れた」の 3 つに
  読めてしまう。追記による release に変えて二義を消した。却下。
- **reap のトリガを「次にその lane の lease を取得しようとする worker」に置く (初版の線)** — 取得試行が
  来なければ回収も来ない。TTL は回収**可能**時刻しか有界にせず、回収**実行**時刻を有界にする主体が誰も
  いない。単一 worker 構成では取得試行がそもそも稀なので、実質「回収されない」に等しかった。却下。
- **専任の reaper プロセスを置く** — 回収を確実にする直接の手段だが、reaper 自身の死活監視が要り、
  「worker が 1 台も居ないときは reaper も居ない」という同型の問題が 1 段ずれて再発する。この問題は
  heartbeat の鮮度 → 人間通知という別経路で既に解いているので、プロセスを増やす価値が無い。却下。
- **同一 `worker_id` でも TTL 満了まで再取得を禁じる (規則の単純さを採る)** — 「lease は誰が持って
  いようと満了まで不可侵」は規則としては最も単純だが、単一 worker 構成では**自分の落とし物を自分で
  拾えない**ことを意味し、プロセス死のたびに 20min 以上止まる。安全性の増分はほぼゼロ (自分名義の
  子は自分が abort できる) で、回復可能性の損失だけが残る。却下。

## Consequences

### Positive

- 権威が「プロセス」ではなく「lease」なので、**実行体の台数と独立に安全**である。台数が増えても
  split-brain にならない。
- 取得から解放までが sha-CAS という 1 つのプリミティブで閉じる。依存する GitHub の機能が 1 つで、
  文書化された 409 の契約の上に乗っている。
- 実行体が死んでも、次の tick で掃引した worker が takeover して復元できる。**専任の回収役が要らない**
  ので、回収役自身の死活監視も要らない。掃引は既に回している tick に相乗りするので、機構は増えない。
- 時刻の規範がサーバ時刻の転記に統一されているため、実行体の時計ドリフトが判定に混入しない。
- 全ての待機に上限がある。fail closed のデッドロックと lane 再稼働は ≒ 23min (TTL + tick + T_reap)、
  reap 試行は lineage 通算 3 回、needs-human の滞留は 72h で再通知 (上限 3 回)。
- **回復と通知の時刻が 3 段の不等式に並ぶ** — 数分 (同一 worker_id の再取得) < 23min (掃引 takeover)
  < 30min (heartbeat 失効の判定。通知の到達はさらに schedule 遅延の分だけ後)。「自動回復を人間通知より
  先に走らせる」が、運用上の願いではなく数値から導出できる関係になった。**不等式が要求するのは判定時刻
  の順序だけ**で、通知の遅延はこの順序を強めこそすれ壊さない。

### Negative

- **副作用面は fence されない**。state 面は CAS で守れるが、GitHub への git push や PR の更新には epoch
  を刻めない。takeover の後も、旧 worker の子が abort されるまでの間 push しうる。**残る防壁は
  `codex/**` への隔離と人間の merge ゲートの 2 つだけで、この残余は設計で消えない**。
- **token の失効は防壁から外れた**。[ADR 0015](0015-capability-broker-instead-of-container-credentials.md)
  で Crucible (Codex 実行コンテナ) は credential 自体を持たなくなり、upstream への push 主体は worker
  本体になった。worker の token は自分の副作用を止める道具ではないので、**token の TTL 60min を
  split-brain 時の安全性の防壁として数えてはならない**。初版はこれを 3 つ目の防壁として数えていた。
- **失敗分類が message 文字列に依存する**。GitHub 側の文言変更で分類が壊れる。fail closed が安全網だが、
  壊れれば全てが needs-human に倒れて自動化が止まる。
- **CAS リトライの実装が複雑**。全ての書き込みが「読み直し → 検査 → 再試行」のループになり、判定純関数
  と I/O の分離を規約で強制しないと保守できなくなる。
- fail closed 時に lease を保持したまま停止するため、**その lane は最長 ≒ 23min 進まない**。安全の
  ために可用性を意図的に捨てている。
- **掃引が固定コストになる**。active lane 数 × tick 回数のリクエストが、進行の有無にかかわらず毎分発生
  する。304 は quota を消費しないが、リクエスト自体は消えない。lane 上限 30 はこの固定コストの上限でも
  ある。
- **同一 `worker_id` の期限内再取得を認めたことで、「権威は台数と独立」の純度が下がった**。再取得の可否
  が `worker_id` の同一性という新しい前提に載る。VM を作り直した実行体が同じ id を名乗ると、fencing が
  意図せず緩む。id の一意性は運用規律であり、機構ではない。
- **未確定 intent ≤ 3 という制約が実装に漏れる**。副作用を起こすコードのすべてが「これは何本目の未確定
  intent か」を意識することになり、reap の都合が副作用側の設計に染み出す。
- 2 相予約は、予約・確定・無効化の 3 種のレコードと、それらを掃く scheduler 掃引を追加する。lane 横断
  カウンタ 1 つのために機構が 1 つ増えている。
- **TTL・tick・abort 所要時間が規範不等式で結ばれている**ため、どれか 1 つを変えると他を検算し直さな
  ければならない。数値は独立に調整できない。
- lease を「一時的な権利」として扱う以上、**すべての書き込み前に権利の確認が要る**。実装上のあらゆる
  副作用に self-fence の分岐がぶら下がることになる。

### Neutral

- one-way door に近い。運用開始後にプロトコルを変えるなら、稼働中の lane を drain してからの切替になる。
- 権威の**置き場**は [ADR 0011](0011-authority-state-in-dedicated-state-repo.md) が決めており、本 ADR は
  その上に載るプロトコルだけを決める。
- **並行での lane 初回作成 (sha なし PUT の 422) は PoC 未実測**であり、Phase 1 の実装前の検証項目。
  再読込で既存 `state.json` を確認できた場合のみ CAS 敗北として扱い、確認できなければ fail closed と
  する。
- 権威を token の層で守るのは [ADR 0013](0013-role-separated-tokens-and-credentials.md) の責務であり、
  本 ADR のプロトコルはそれを前提にしている。副作用面 (push) を守るのは
  [ADR 0015](0015-capability-broker-instead-of-container-credentials.md) の broker であり、本 ADR は
  そこに epoch を刻めないことを残余として引き受けている。
- 本 ADR の回復時間は fitness function として測定される — 「lease 失効 → takeover の遅延 ≤ tick +
  T_reap、全体 T_recover ≤ 23min、超過 0 件」が Foreman (worker daemon) の常時測定項目である
  ([ADR 0016](0016-quantum-scoped-fitness-functions.md))。数値を変えるなら閾値も同時に変える。
