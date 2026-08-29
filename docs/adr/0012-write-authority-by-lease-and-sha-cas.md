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
- **reap** — 失効 lease の回収は、**次にその lane の lease を取得しようとする worker** が行う (専任の
  reaper を置かない)。takeover は CAS 更新 + epoch+1。復元は **intent と transition の差分 + code repo
  実体の照会** (PR / branch / thread の存在) で行い、**未適用の intent のみを対象とする — 完了済みの
  transition は巻き戻さない**。reap のカウントは復元を要した takeover のみ加算し、上限は lineage 通算
  3 回。
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
  デッドロックは ≤ TTL で有界である。
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

## Consequences

### Positive

- 権威が「プロセス」ではなく「lease」なので、**実行体の台数と独立に安全**である。台数が増えても
  split-brain にならない。
- 取得から解放までが sha-CAS という 1 つのプリミティブで閉じる。依存する GitHub の機能が 1 つで、
  文書化された 409 の契約の上に乗っている。
- 実行体が死んでも、次に来た worker が takeover して復元できる。**専任の回収役が要らない**ので、回収役
  自身の死活監視も要らない。
- 時刻の規範がサーバ時刻の転記に統一されているため、実行体の時計ドリフトが判定に混入しない。
- 全ての待機に上限がある。fail closed のデッドロックは ≤ TTL、reap 試行は lineage 通算 3 回、needs-human
  の滞留は 72h で再通知 (上限 3 回)。

### Negative

- **副作用面は fence されない**。state 面は CAS で守れるが、Codex コンテナが行う git push や PR の更新
  には epoch を刻めない。takeover の後も、旧 worker の子が abort されるまでの間 push しうる。防壁は
  `codex/**` への隔離・token の失効・人間の merge ゲートの 3 つで、**この残余は設計で消えない**。
- **失敗分類が message 文字列に依存する**。GitHub 側の文言変更で分類が壊れる。fail closed が安全網だが、
  壊れれば全てが needs-human に倒れて自動化が止まる。
- **CAS リトライの実装が複雑**。全ての書き込みが「読み直し → 検査 → 再試行」のループになり、判定純関数
  と I/O の分離を規約で強制しないと保守できなくなる。
- fail closed 時に lease を保持したまま停止するため、**その lane は最長 TTL (20min) 進まない**。安全の
  ために可用性を意図的に捨てている。
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
  本 ADR のプロトコルはそれを前提にしている。
