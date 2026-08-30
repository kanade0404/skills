# Codex 実行コンテナから GitHub credential を排除し、worker を capability broker にする

Status: accepted (2026-08-30)

Driver: [安全性 (Secure by Design)](0014-add-security-to-ility-priority-order.md) — 権限は既定で最小、
境界の強制は規約ではなく機構で行い、残余は消さずに書き出す。同
[-ilities 1 可監査性・回復可能性](0009-ility-priority-order.md) — 副作用が権威面に記録できる単一点を
通ること。同 2 有界性 — 検査そのものが有界であること。用語は [CONTEXT.md](../../CONTEXT.md) に従う。
本 ADR で使う仮名 (Foreman = worker daemon / Customs = broker の検査子プロセス / Crucible = Codex 実行
コンテナ) の定義は [ADR 0016](0016-quantum-scoped-fitness-functions.md) にある。

## Context

**GitHub の認可モデルは、PAT でも GitHub App でも「操作の種類 × repo」までしか絞れない。** ところが
このパイプラインが実際に課したい制約は、**すべて操作の引数に対する条件**である。

- `codex/<issue>/**` 以外の branch には push させない
- `.github/workflows/**`・契約スキーマ・検査装置そのものには触れさせない
- PR は作れるが merge はできない (merge だけを除外する permission の粒度は存在しない)
- secret を含む payload は書き込ませない

branch・パス・payload はいずれも操作の**引数**であり、token のスコープでは表現できない。ruleset は
branch 単位の補正はできるが全部は塞げず、pre-receive hook は github.com 側には置けない。したがって
**引数レベルの認可は自前で実装するしかない** — その実装点をどこに置くか、が本 ADR の問いである。

前提として、**コンテナ内部の完全侵害を防げるとは仮定しない**。Web 要約を依頼するだけで任意コード実行
まで到達する prompt injection が 60–80% の成功率で実証されており [S2]、egress の allowlist も実装バグ
で無力化されうる (Claude Code の network sandbox に null-byte injection によるバイパスが 5.5 ヶ月存在
した報告 [S3])。単層を信じる設計は、その層のバグ 1 つで全部が倒れる。

同型の構成が既に稼働している先行事例がある [いずれも S0]。

- **Anthropic の Claude Code sandboxing / on the web** — 「git credential や署名鍵は sandbox 内に決して
  置かない」。git 操作は sandbox 外の proxy が受け、push 先 branch 等を検証してから token を付けて
  GitHub へ送る。**本設計と同じ発想が製品として動いている。**
- **OpenAI の Codex CLI** — `--dangerously-bypass-approvals-and-sandbox` は "Only use inside an
  externally hardened environment" と原文で明記されている。同時に「コンテナ内の credential ごと
  exfiltrate されうる」と警告しており、**フラグ自体は credential 分離を提供しない**。外部の硬化は
  利用者の仕事だと公式に言われている。
- **GitHub の Copilot coding agent** — ephemeral コンテナ + egress firewall、`copilot/` branch にしか
  push 不可、自分の PR を merge しない。
- **Gerrit の `refs/for/` + pre-receive hook** — push を仲介 ref に受けて検証後に反映する
  store-and-forward は 15 年物の確立パターンである。
- **SLSA v1.2** — 「secret はユーザ定義ビルドステップの実行環境からアクセス不能でなければならない」
  + ephemeral 環境必須。build 一般への公式要求として同じ形が書かれている。

## Decision

**Crucible (Codex 実行コンテナ) に GitHub credential を一切配布しない。** **Codex 由来のコンテンツを
GitHub へ書き込むのは worker (Foreman) だけ**とし、worker は **capability broker** — 「検証済みの操作
だけを提供する interface」— として振る舞う。**「すべての GitHub 書き込みが worker を通る」ではない** —
Fable のタスク issue 起票と人間の操作は別経路であり、本 ADR が閉じるのは Codex 由来の経路である
([ADR 0013](0013-role-separated-tokens-and-credentials.md) の credential 表がその 3 経路を列挙する)。**この採用案を以後 B3 と呼ぶ**。検討した案には B1〜B7 の番号を振り、
B1・B2・B4 が設計セッションの主要な比較対象、B5〜B7 が並行して検討した追加案である (下記
Considered Options)。

### 判定原理

**検証は、コンテナ内の任意コードが迂回できない場所に置く。** これが実装点を選ぶ唯一の基準である。

- **受け側** (worker 側の受け口・broker) は満たす。コンテナから見て「向こう側」なので、コンテナ内で
  何が動いていても迂回できない。
- **要求側** (SDK のコマンド承認、wrap した git CLI) は満たさない。テストスクリプトの中の `git push`
  は SDK の承認経路を通らず、wrapper は素の git を持ち込めば迂回できる。**どちらも攻撃者が支配する
  ゾーンの内側にある。** 早期フィードバックの UX 層として重ねるのは自由だが、**荷重はかけない**。

### 構造 — store-and-forward

Crucible は成果を **VM 上の bare repo へ `git push` する**。worker はそれを受けて検査し、**合格した
commit SHA だけを upstream (GitHub) へ push する**。

受理は**安い検査から順に**並べる — branch 名 → サイズと数 → 保護パス → **全 commit の patch に対する
secret scan** → 合格 SHA の固定 push。この順序が有界性を買う: 敵対的入力に高コストな走査を払う前に
落とせる。2 点だけ順序に理由がある。

- **net diff ではなく全 commit の patch を走査する** — net diff では中間 commit に入った secret を
  見逃す。履歴に残る以上、消えていても漏洩である。
- **最後は合格した SHA を固定して push する** — 検査の後に ref を進める TOCTOU を遮断する。branch 名で
  push すると、検査した中身と push する中身が別物になりうる。

受理検査は**内容だけでなく、送り手が今の権威かどうかも見る** — 現在の `epoch` と `incarnation`
([ADR 0012](0012-write-authority-by-lease-and-sha-cas.md)) を要求し、古い実行体からの push を落とす。
**副作用面に fence を刻めるのは、push が credential を要する操作として broker を通るようになったからで
ある** — token を直接持たせる設計では、この検査を差し込む場所自体が無かった。

どの段の失敗も **fail closed** で `needs-human` に倒す。scanner の timeout・未知 exit・OOM も「検出」と
同じ扱いにする。

**受理検査の実装点は bare repo の pre-receive hook** — 我々の側に置く hook であって、repo 内に仕込まれ
る hooks とは別物である。ここに置くと合格しない限り ref の更新自体が起きないので、**検査と受理が git
側で原子化する**。拒否理由は `git push` のエラーとして Codex に自然に返るため、別の通知経路を作らなくて
よい。

**git を経由しない payload の走査点は、worker の GitHub 書き込みラッパの内側 (broker pre-flight) に置
く。** PR の title と body・コメント・issue 本文・state repo への CAS 書き込みは push 経路を通らないの
で、pre-receive hook では捕まらない。これらは worker が API を呼ぶ直前に同じ検査を通す。**pre-receive
と pre-flight は同一の走査関数を共有する** — 経路ごとに別実装を持つと、片方だけルールが古くなる形で網に
穴が空く。[ADR 0014](0014-add-security-to-ility-priority-order.md) が層 2 に要求する「対象の網羅」は、
この 2 つの実装点の和で満たす。走査を通していない値から書き込み用の payload を構築できないよう、
**scanner だけが生成できる型を経由させる**ことで、規約ではなく型で強制する。

受け口を **bare repo** にしたのは消去法である。push の宛先は repository しかありえず、working tree は
受け口の仕事 (objects + refs + hook) には不要で、footgun (checkout 済み branch への push) と攻撃面を
増やすだけである。transport を自作すれば、増分転送・整合性・検査点・リトライを再発明することになる。

肥大化は 3 方向から有界である。① bare repo は lane と同寿命で、決着時に破棄する — 耐久状態ではなく
キャッシュであり、消えても upstream と Ledger (state repo) から再構成できる ② **lane あたりの受理試行
回数 ≤ 20 回、bare repo のディスクサイズ ≤ 1 GiB** とし、どちらの超過も `needs-human` ③ 拒否した push の
objects は git の quarantine に留まり、本体には移らない (この実挙動は PoC ② の実測項目)。

**②のディスク上限は、pre-receive が走る前から効いていなければならない。** `git-receive-pack` は受信した
objects を **pre-receive hook の実行前に quarantine へ書く**ので、**拒否される push でも判定前にディスクを
消費できる**。hook の中でサイズを見るだけでは、敵対的な巨大 push に対して遅すぎる。したがってディスク上限は
**受信中の使用量に対して強制する** — 受信 pack の入力サイズ上限と、受け口プロセスに対する filesystem
ないし cgroup の quota を、hook の判定とは独立に置く。**受理手順 2 のサイズ検査は「正常系の上限」であって
「敵対的入力からの防壁」ではない**という書き分けをする。拒否時に quarantine が破棄される挙動は git の
version に依存するため、**対象 version での実挙動を PoC ② で確認する**。

**②の 2 つの数値は暫定値である。** 他の運用パラメータ (tick・TTL・thread 時間・同時 thread 数など) は
Charter (契約配布物) の rules で宣言される既定値群が値を持っているが、**受理試行回数と bare repo の
ディスクサイズはその既定値群に未収録**で、本 ADR が暫定を置いた。Phase 1 の実測 (通常の lane が何回の
push で決着するか、成果物の実サイズ) で確定し、**確定時は同じ既定値群 (rules) に収録して ADR 側から
数値を落とす**。**値そのものより「上限があり超過が fail closed になる」ことが Driver の要求**であり、
暫定値のまま実装してよい。

### 受け口の硬化

**broker を突破することと、鍵を直接握ることを別にする。**

- Crucible が触れるのは git protocol 経由の push 先のみとし、共有ファイルシステムへの直書きは禁止する。
  **worktree を VM 側とコンテナ内のどちらに置くか、マウントするかどうかは意図的に未確定**で、PoC ② で
  確定する — どの配置でも「受け口へは git protocol でしか到達できない」が保たれることが要件である。
- worker 側の git 実行は、**repo 内に仕込まれた hooks・config・gitattributes filter を一切実行しない**
  設定で行う。検査される側が検査する側のコードを実行できてはならない。
- 受け口と scanner は、**App 秘密鍵を継承しない別 uid の子プロセス (Customs)** で動かす。

### 禁止したのは「push」ではなく「無条件の push」

push そのものは capability として残る。危険なのはその部分集合 — `codex/**` 外への push と、secret を
含む履歴 — であって、悪意あるコードそのものはレビュー層と人間 merge の受け持ちである。しかし token は
「push 可否」の二値しか表現できず、危険な push と安全な push を区別できない。**だから push 能力を
credential ごと剥がし、「検査済み・`codex/**` 限定・SHA 固定」という条件付きの push を broker の
interface として再提供する。**

一般形はこうである — **credential が必要な操作は実行体に持たせず、別の interface に call させ、そこで
検証する。** push はその操作の 1 つにすぎず、call の形が RPC ではなく `git push` をしているだけである。
PR の作成・コメント・issue の起票も同じ形で worker が代行する。worker が転記する自由文は必ずフェンスと
解釈禁止の定型で包み、契約ブロック以外を指示面に出さない (injection のロンダリングの遮断)。

### 物理配置は独立のダイヤル

論理境界 (capability interface) は本 ADR で確定する。**物理配置は別の判断**として、「同一 VM + uid
分離」から始める (デプロイ物が増えない)。interface の定義は別システム化を許す形にしておき、worker 侵害
を実際に懸念する段階で物理分離へ格上げできる two-way door として残す。

## Considered Options

- **B1: Crucible に job 別の短命 token を直接渡し、ruleset で補正する** — 初版の
  [ADR 0013](0013-role-separated-tokens-and-credentials.md) の線。**引数レベルの認可が GitHub 側に存在
  しない以上、原理的に完成しない** — token をどれだけ絞っても「`codex/**` 外への push」と「`codex/**`
  への push」を区別できず、ruleset は branch 単位の補正しかできない。pre-flight の scan を差し込む場所
  も無い (コンテナ内に置けば迂回される)。却下 — ただし PoC ② が不成立の場合の**第 2 fallback** として
  保持する。この fallback を採ったときに human-only merge の主張を「human approval gate」へ改名するか
  否かの条件は、[ADR 0013](0013-role-separated-tokens-and-credentials.md) が定める (第 2・第 3 層も
  実測で不成立だった場合にのみ改名する) — 本 ADR では条件を重ねて定義しない。
- **B2: 汎用の GitHub API proxy を立て、Crucible に API を話させる** — Crucible に「GitHub API を話す
  能力」を残すこと自体が不要である。Codex の仕事は code を書くことであって、GitHub を操作することでは
  ない。能力を残せば、その能力に対する認可を全 API について設計することになる。却下。
- **B5: 同期・透過型の forward proxy (Anthropic 型)** — 検証して即 GitHub へ中継する型。**浅い検証 (push 先
  branch 程度) なら成立する実例がある** [S0]。採らない理由は要件差の 3 点。① 全履歴の scan と tree 検査
  は pack の展開 = 内部 repo を要し、深い検証を積むほど store-and-forward の再発明になる (GitHub の
  pre-receive も quarantine repo で動く同型である) ② upstream への push は `intent` の write-ahead 記録
  の後に worker が実行する必要があり ([ADR 0012](0012-write-authority-by-lease-and-sha-cas.md))、Codex の
  git コマンド内で同期に発生させられない ③ 深い検証は攻撃面が大きく、credential を持たない子プロセスに
  隔離して合格 SHA だけを渡す方が blast radius が小さい。同期性の利点は対話 UX であり、レイテンシを
  明示的に捨てた本設計 ([ADR 0009](0009-ility-priority-order.md)) には価値がない。却下。**検証が浅くて
  済むと判明したら、この選択は逆転する** — 分岐条件として記録しておく。
- **B6: SDK のコマンド承認機構を境界にする / wrap した git CLI を境界にする** — どちらも**攻撃者が支配する
  ゾーンの内側**にある。テストスクリプト内の `git push` は SDK の承認経路を通らず、wrapper は素の git
  の持ち込みで迂回できる。bypass 実行を前提にすると承認機構自体が働かず、codex hooks は worktree で
  壊れる (openai/codex#27133、[ADR 0010](0010-resident-worker-with-codex-python-sdk.md))。判定原理を
  満たさないので境界にはしない。却下 (UX 層としては可)。
- **B4: Crucible には push すらさせず、worker が worktree を直接読んで commit・push する** — git の
  receive 面が丸ごと消えるので攻撃面は最小になる。代わりに「どこまでが 1 push か」という**完了宣言を
  自前で発明することになる** — Codex がいつ書き終えたのかを、git の外側の規約で決めねばならない。
  **PoC ② が不成立の場合の第 1 fallback として保持する**。今は却下。
- **B7: egress を締めるだけで credential は配布したままにする** — allowlist は実装バグで無力化される実例が
  あり [S3]、CI runner という別の egress 経路も残る
  ([ADR 0014](0014-add-security-to-ility-priority-order.md))。単層を信じる設計で、**破られたときに盗る
  物がある**状態を維持してしまう。却下。

## Consequences

### Positive

- **Crucible が完全に侵害されても、盗める GitHub credential が存在しない**。injection を防げないという
  前提と、この設計だけが噛み合う。allowlist のバイパスが見つかっても、持ち出す鍵が無い。
- **token では表現できない引数レベルの制約が表現できるようになった**。「`codex/**` 限定」「保護パスに
  触れない」「secret を含まない」が、約束ではなく検査として存在する。
- **Codex 由来の書き込みの検査点が 1 箇所に集まる**。push も PR も issue も worker を通るので、
  [ADR 0014](0014-add-security-to-ility-priority-order.md) の層 2 が経路ごとに漏れない。
- **検査と受理が git 側で原子化する**。合格しなければ ref の更新自体が起きず、拒否理由は `git push` の
  エラーとして Codex に自然に返る — 別の通知経路を作らなくてよい。
- **先行事例が同型で稼働している** [S0]。新規発明ではないので、想定外の失敗モードを引く確率が下がる。
- Crucible の egress から `api.github.com` が消えた。認証と SDK は VM 側にあるので、コンテナに許すのは
  registry・DNS・**VM 上の受け口 (git protocol)** の 3 つだけでよくなった。

### Negative

- **worker が単一侵害点になった**。GitHub App の秘密鍵・Codex の auth・broker の判定・PR 本文の生成が
  すべて 1 つの VM に集まる。worker が侵害されれば、人間 merge ゲートの判断材料 (ac-report・PR 本文・
  派生表示) の**偽造まで可能**である。緩和は uid 分離・判定の権威面記録・レビュー層との運命分離
  ([ADR 0016](0016-quantum-scoped-fitness-functions.md)) だけで、**この残余は消えない**。
- **container escape は同一 VM の worker を直撃する**。物理配置を「同一 VM + uid 分離」から始めた代償
  であり、物理分離のダイヤルを回すまで残る。
- **registry 経由の exfiltration は残る**。公式 registry ホストへの任意 HTTP メソッド (publish を含む)
  と DNS クエリは通る。lockfile の固定で縮小するだけで、塞げてはいない。
- **実装量が増える**。bare repo のライフサイクル管理、受け口の hook、受理検査、fault injection の一式が
  すべて自前のコードになり、**そのコード自身が新しい攻撃面**である。判定を純関数に寄せ、I/O を薄い層に
  留める規律で保つしかない。
- **成果の反映が同期でなくなった**。Codex は push が upstream に届いたかを直接は確認できず、受理は
  worker の記録を通してしか観測できない。レイテンシと、状態の見え方の間接化を引き受けている。
- **PoC ② の未実測項目に乗っている**。**PoC ② =「worktree × 外部コンテナ」の実測**であり、
  [ADR 0010](0010-resident-worker-with-codex-python-sdk.md) が Phase 2 前の必須ゲートとして挙げる PoC
  3 件 (①多 thread 並行の安定性 / ②worktree × 外部コンテナ / ③lease プロトコルの競合試験) の 2 番目を、
  broker の受け口まで拡張したものを指す。観測項目は 4 つ — ① bare repo 仲介と Codex SDK の cwd 前提が
  両立するか ② 受け口の硬化の下で通常のワークフローが阻害されないか ③ thread からコンテナへコマンドを
  渡す縫い目 (docker exec への転送が第一候補。agent をコンテナ内で丸ごと動かす案は auth がこの ADR と
  衝突する) ④ 拒否された push の objects が quarantine で消えること。**不成立なら B4 → B1 の順に落ちる。**

### Neutral

- **B3 自体は two-way door である**。直接 token を渡す形に戻すのは容易で、scanner も interface の背後に
  あり差し替え可能。one-way door 寄りなのは org への移行と GitHub Secret Protection の購入で、これは
  移行条件を明文化して先送りしている ([ADR 0014](0014-add-security-to-ility-priority-order.md))。
- 本 ADR は **credential を持たない実行体が成果を届ける手段**を決める。誰がどの credential を持つかは
  [ADR 0013](0013-role-separated-tokens-and-credentials.md) が決める。
- broker の判定 (allow / deny・適用したポリシー版・対象 SHA) は権威面に記録する。「例外領域ゼロ」
  ([ADR 0009](0009-ility-priority-order.md)) を broker にも適用した結果であり、記録に secret の値は
  含めない。副作用に先行する `intent` の write-ahead
  ([ADR 0012](0012-write-authority-by-lease-and-sha-cas.md)) は従来どおり別に必要である — 判定の記録は
  write-ahead ではない。
- 検査が働いていることの確認は fitness function に載る — 「secret 入り diff・保護パス・`codex/**` 外・
  TOCTOU・scanner 設定の改変」を注入する fault suite の fail closed 率 100% が Foreman の drill 項目で
  ある ([ADR 0016](0016-quantum-scoped-fitness-functions.md))。

## References

出典の確度は S ランクで示す — **S0** = 一次資料 (公式文書・公式リポジトリの原文)、**S1** = 自分たちの
実測、**S2** = 査読ないし再現手順のある第三者の実証報告、**S3** = 個別の事例報告・未確認の二次情報。
いずれも 2026-08-30 に取得。

- [S0] Anthropic, *Claude Code sandboxing* — <https://www.anthropic.com/engineering/claude-code-sandboxing>
  / *Claude Code on the web* — <https://claude.com/blog/claude-code-on-the-web>
  (git credential と署名鍵を sandbox 内に置かず、透過 proxy が検証してから token を付ける)
- [S0] OpenAI, *Codex CLI developer commands* —
  <https://learn.chatgpt.com/docs/developer-commands?surface=cli> / *sandboxing* —
  <https://learn.chatgpt.com/docs/sandboxing>
  (`--dangerously-bypass-approvals-and-sandbox` は "Only use inside an externally hardened environment")
- [S0] GitHub, *Customize the agent firewall* —
  <https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/customize-the-agent-firewall>
  / *Responsible use of Copilot agents* — <https://docs.github.com/en/copilot/responsible-use/agents>
  (ephemeral コンテナ + egress firewall、`copilot/` branch にしか push 不可)
- [S0] Gerrit, *refs/for namespace* —
  <https://gerrit-review.googlesource.com/Documentation/concept-refs-for-namespace.html> / GitHub,
  *Creating a pre-receive hook script* —
  <https://docs.github.com/en/enterprise-server@3.16/admin/enforcing-policies/enforcing-policy-with-pre-receive-hooks/creating-a-pre-receive-hook-script>
  (store-and-forward と サーバ側 policy 強制の先行実装)
- [S0] SLSA v1.2, *Build requirements* — <https://slsa.dev/spec/v1.2/build-requirements>
  (secret はユーザ定義ビルドステップの実行環境からアクセス不能であること + ephemeral 環境必須)
- [S0] Cursor, *Cloud agent security and network* — <https://cursor.com/docs/cloud-agent/security-network>
  (secret の 3 種別分離、egress allowlist、prompt-injected agent の exfil を明示的リスクとして記載)
- [S2] Rehberger, prompt injection から任意コード実行までの実証 (2026-08) — Web 要約の依頼のみで
  60–80% の成功率。**injection は防げないという前提の根拠**。
- [S3] Claude Code の network sandbox に null-byte injection によるバイパスが 5.5 ヶ月存在したという
  報告。**単層の allowlist を信じないという判断の根拠**。
- [S0] GitHub, *Create an installation access token for an app* —
  <https://docs.github.com/en/rest/apps/apps?apiVersion=2022-11-28#create-an-installation-access-token-for-an-app>
  (発行時に repositories / permissions を絞れる。TTL 60 分固定)
- [S0] gitleaks — <https://github.com/gitleaks/gitleaks> / trufflehog —
  <https://github.com/trufflesecurity/trufflehog> (scanner 実装の候補。stdin / baseline / ignore の対応)
