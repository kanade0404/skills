# -ilities に安全性 (Secure by Design) を加え、可監査性と同格の第 1 位とする

Status: accepted (2026-08-29), revised (2026-08-30) — [ADR 0009](0009-ility-priority-order.md) を
amend する。0009 は supersede せず、Status は accepted のまま据え置く。現在の -ilities は 0009 と本 ADR
と [ADR 0016](0016-quantum-scoped-fitness-functions.md) を合わせたものである。

**本 ADR は #117 の未 merge バッチ内の ADR であり、同バッチが merge される前に上記の日付で改訂された。** 初版を参照した第三者はまだ存在しないため、accepted 済み ADR の不変性 ([ADR 0014](0014-add-security-to-ility-priority-order.md) の Considered Options) を破らずに本文を直接改訂している。merge 後の変更は amend ADR か Erratum で積む。

Driver: 本 ADR は [ADR 0009](0009-ility-priority-order.md) と同じく、下流の決定に対して Driver を供給
する側である。用語は [CONTEXT.md](../../CONTEXT.md) に従う。

## Context

[ADR 0009](0009-ility-priority-order.md) は -ilities の優先順を
「可監査性・回復可能性 > 有界性 > 並行性 > レイテンシ」と定めた。

第一陣の ADR を起票した後の点検で、**[ADR 0005](0005-dual-track-security-review.md) (セキュリティ
レビューの二重化) の Driver が -ilities の中に無い**ことが分かった。0005 の Driver 行は「可監査性
(検出が単一点に依存しないこと)」と書かれているが、本文で実際に決定を動機づけているのは「脆弱性は
merge されて稼働した時点で外部に露出し、見落としの代償が非対称である」という安全性の議論である。
可監査性はその代理として使われているにすぎない。

第二陣でも同じ穴が現れた。[ADR 0013](0013-role-separated-tokens-and-credentials.md) が払っているコスト
— consumer セットアップの増加、token 発行の自前実装、secret store への依存 — は、可監査性でも有界性
でも説明できない。[ADR 0012](0012-write-authority-by-lease-and-sha-cas.md) の「副作用面は fence され
ない」という残余の扱いも同様である。

代理の -ility で書き続けると、安全性がその他の特性と競合したときに順序が決まらない。しかも両者は素朴
には正面から衝突する — 可監査性は「全状態を GitHub 上で人間に見える形に置け」と言い、安全性は
「secret を書くな」と言う。順序と競合規則の両方が要る。

## Decision

-ilities の一覧に **安全性 (Secure by Design)** を加え、**可監査性・回復可能性と同格の第 1 位**とする。
改訂後の優先順は以下。

1. **可監査性・回復可能性** および **安全性 (Secure by Design)** — 同格
2. **有界性**
3. **並行性**
4. **レイテンシ** (およびマルチテナント効率) — 明示的に捨てる

安全性の内容は次の 4 点とする。

- 権限は既定で最小 (必要が示されたものだけを足す)。
- 信頼境界を明示する (何を指示として扱い、何をデータとして扱うか)。
- **境界の強制は機構で行う** — token のスコープ・コンテナ・ruleset。規約や約束に頼らない。
- **残余リスクは消さずに書き出す**。消せない残余を「対処済み」と書かない。

### 同格の 2 つが競合したときの規則

**安全性が「何を権威面に書かないか」の制約を課し、可監査性はその制約の下で最大化する。**

具体的には、secret の値そのものは GitHub 上のどの面にも書かない。書くのは参照・発行の事実・失効の事実
である。これは 0009 の「例外領域ゼロ」を弱めない — 例外になるのは secret の**値**だけで、secret に
関する**状態**は権威面に載るからである。

**この制約は「機構で行う」(4 点目) の対象でもある**。Codex が書いた差分・PR 本文・コメントは GitHub へ
送信されるため、secret の値がそこに混入する経路がある。

初版はこの経路を塞ぐ機構として、GitHub Conformist ([ADR 0008](0008-github-conformist.md)) に沿って
**GitHub 標準の secret scanning + push protection を既定線**にしていた。**2026-08-30 の調査でこれが
成立しないことが確定した** — GitHub Secret Protection は org / enterprise 限定で、**個人アカウントの
private repo では無償・有償のいずれでも利用できない**。無償の push protection for users も public repo
限定であることが公式に明文化されている。純正機構を既定線にできない以上、自前の既定線は選好ではなく必然
である。

### secret 検出の既定線 — 自前 4 層

| 層 | 機構 | 要点 |
|---|---|---|
| 1 | **構造的排除** | Crucible (Codex 実行コンテナ) には GitHub credential を一切配布しない。CI の実行 context には**書き込み credential と secrets を注入しない** ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md))。実 credential を要する統合テストはコンテナ外へ出す |
| 2 | **broker pre-flight** | **GitHub へ出る payload を漏れなく検査対象にする** — push される差分・PR の title と body・コメント・issue・state repo への CAS 書き込みまで。どこで何をどう走査するかは [ADR 0015](0015-capability-broker-instead-of-container-credentials.md) が決める。本 ADR が課すのは**対象の網羅**と、**scanner の設定・baseline・ignore は worker 配備版のみを使い repo 内の設定は無視する**ことの 2 点 |
| 3 | **CI required check** | PR diff への scanner を required check として置く。**人間の直接 push にも効く第 2 の網**。config は worker 配備版から供給し、契約の「3 点同時 bump」に編入する (drift 防止) |
| 4 | **GitHub 純正** | public repo では有効化する。private repo は org 移行時の追加層 — 条件付きの将来オプションであり、既定線には数えない |

- **層 2 が repo 内の scanner 設定を無視するのは、被検査側が検査を無効化する経路を塞ぐため**である。
  `.gitleaksignore` や設定ファイルの追加・改変は保護パス検査 ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md)
  の受理手順 3) の対象でもあり、触れる diff は needs-human に倒れる。
- **Fable が起票するタスク issue は層 2 の対象外である**。Fable は App として code repo に直接書き込む
  書き手であり ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md) の表)、worker の代行経路に
  乗らないので、**broker の pre-flight という強制点が存在しない**。層 2 は「書き込みの前に検査する」
  予防的統制なので、ここに数えると層 2 が実際より強く見える。
  **この経路は検出的統制として別に持つ** — worker が取り込む時点で scan し、検出したら `needs-human` +
  隔離 ([ADR 0013](0013-role-separated-tokens-and-credentials.md))。**検出時には既に本文が GitHub 上に
  あるため、混入した secret は漏洩として扱う**。予防と検出を同じ層に混ぜない。
- **escalation のレコードに secret の値を書かない**。載せてよいのは rule id・パス・行番号・fingerprint
  だけで、値・周辺行・payload 本文の転記は禁止する — escalation 経路自体を漏洩経路にしない。誤検知の
  解除は worker 配備版の baseline 管理で行い、自動 unblock はしない。
- **層 1 は CI については「credential ゼロ」ではない**。`pull_request` workflow に渡る read-only の
  `GITHUB_TOKEN` は**それ自体が credential であり、脅威モデルに含める**。read でも、private repo の
  コード・issue・PR を CI が読める範囲は侵害時の情報取得経路になる。Crucible の「credential ゼロ」と
  CI の「書き込み credential ゼロ」を同じ言葉で呼ばない。
- **残余として明示する**: 人間が GitHub へ直接書き込む経路は層 2 の対象外で、層 3 の CI check だけが網
  である。自前 scanner はエントロピーを持たない独自形式の credential を取りこぼす。**CI の read-only
  `GITHUB_TOKEN` が読める範囲は残る** — 完全に無くすには self-hosted runner が要る。いずれも「対処済み」
  とは書かない。

### CI (GitHub Actions) を信頼境界として数える

信頼境界の一覧に **CI runner** を加える。それまで数えていた境界 (Fable / Crucible / worker / GitHub) は
どれも CI を含んでおらず、**設計上の数え落とし**だった。Codex が書いたテストは PR イベントで Actions と
して実行され、そこには `GITHUB_TOKEN` と無制限の egress がある。**Crucible に課した egress 制限を、CI が
丸ごと迂回する**。境界として扱い、次を課す。

- `pull_request` workflow の permissions は **`contents: read` のみ**。codex 由来 PR の workflow に
  secrets を注入しない (注入は environment secrets + 人間承認の経路に限る)。
- **`pull_request_target` は使用禁止**。
- egress 制限アクション (block mode + allowlist、action は SHA pin) を必須ステップに置く。ただしこれは
  **縮小策であって機構ではない** — GitHub-hosted runner の job は sudo を持つため bypass できる。完全な
  機構は self-hosted runner + egress proxy のみで、org 移行と同時期の条件付きオプションとする。
- worker が CI artifact (ac-report) を権威面へ転記する際は、契約スキーマで機械検証する。

### 下流への効き方

- [ADR 0005](0005-dual-track-security-review.md) セキュリティ二重化 — 実質的な Driver は本 ADR である。
  0005 の本文は変更しないが、以後この決定を読むときは Driver をこちらに読み替える。
- [ADR 0013](0013-role-separated-tokens-and-credentials.md) credential の役割分離 — 直接の帰結。
  「権限は既定で最小」と「境界の強制は機構で」の適用そのもの。
- [ADR 0015](0015-capability-broker-instead-of-container-credentials.md) capability broker — 「境界の
  強制は機構で行う」の最も強い形 (権限を最小にするのではなく、credential 自体を置かない)。上の 4 層の
  層 1 と、層 2 の実装点を供給する。
- [ADR 0012](0012-write-authority-by-lease-and-sha-cas.md) lease と sha-CAS — 「副作用面は fence され
  ない」を残余として明示するのは、4 点目の適用。
- [ADR 0010](0010-resident-worker-with-codex-python-sdk.md) 常駐 worker — Crucible の隔離、および
  ChatGPT auth の残余リスクの明示。

## Considered Options

- **0009 を supersede して書き直す** — 0009 が定めた順序自体は正しく、下流の ADR 群が既にそれを引用して
  いる。全部を書き換える価値が無いうえ、**「何が、いつ、なぜ変わったか」が履歴から読めなくなる**。
  却下。
- **0009 のファイルを直接編集する** — accepted の ADR を書き換えると、過去に参照した時点で何が書いて
  あったかが分からなくなる。ADR の不変性を壊す。却下。
- **安全性を 2 位 (可監査性の下、有界性の上) に置く** — 可監査性が無条件に上位だと、「見えるようにする
  ために書く」が secret に対しても既定になり、衝突のたびに個別判断が要る。安全性は「書かない」側に制約
  を課す特性なので、同格に置いて競合規則を明示する方が判断が決まる。却下。
- **安全性を最上位 (可監査性より上) に置く** — 無条件に上位だと「見せない方が安全」という論法で可監査性
  をいくらでも削れる。人間が張り付かない運用では、見えないことこそが最大のリスクである。却下。
- **安全性を -ilities に入れず、決定ごとの個別判断に留める** — 0009 が「優先順を決めず個別に判断する」
  を却下したのと同じ理由。しかも今回は、0005 で代理の Driver が書かれるという形で失敗が既に観測されて
  いる。却下。
- **secret 検出の既定線を GitHub 標準の secret scanning + push protection に置く (本 ADR の初版)** —
  GitHub Conformist ([ADR 0008](0008-github-conformist.md)) に最も忠実で、維持コストも他人持ちになる。
  **調査で前提が崩れた** — Secret Protection は org / enterprise 限定で、個人アカウントの private repo
  では購入すらできず、無償の user push protection も public repo 限定である。conform する相手が存在
  しない。却下 (public repo と org 移行後の追加層としてのみ残す)。
- **CI runner を信頼境界に数えず、コンテナの egress 制限だけで足りるとする** — 実際には Codex が書いた
  テストが Actions 上で `GITHUB_TOKEN` と無制限 egress を持って走る。境界を 1 つ数え落としているだけで、
  攻撃者にとっては最も安い迂回路になる。却下。
- **egress 制限アクションを「機構」として数える** — GitHub-hosted runner の job は sudo を持つので、
  同じ job から無効化できる。機構と呼べば残余の記述が消え、4 点目 (残余を消さずに書き出す) に反する。
  縮小策として採用し、機構としては数えない。却下。

## Consequences

### Positive

- 安全性のためのコストを、代理の -ility を経由せずに正当化できる。
  [ADR 0013](0013-role-separated-tokens-and-credentials.md) の Driver が 1 行で書ける。
- 可監査性と安全性の衝突に規則があるので、「これは GitHub に書いてよいか」を決定のたびに議論しなくて
  よい。
- 0009 を不変に保ったまま順序を進化させた記録が残る。以後の -ility 追加も同じ形式で積める。
- **secret 検出が自前の層になったことで、検出点が自分の手の内に入った**。プランや org の所属に依存せず、
  検査の対象範囲 (git push だけでなく API payload 全体) と失敗時の挙動を自分で決められる。
- **検査点が 1 つの関数に集まる**。**worker の代行経路と Fable の起票経路が同じ scan 関数を共有する**
  ため、「どの経路が検査されているか」を経路ごとに数えなくてよい。書き手は 2 つだが、検査は 1 つである。
- 信頼境界の一覧に CI runner が入ったことで、**「コンテナは締めたが CI は素通り」という非対称が設計文書
  の上で見えるようになった**。

### Negative

- **同格が 2 つある順序は、単純な全順序より判断が重い**。競合規則で解けない組合せが出たときに、決め手が
  無い。0009 の「毎回議論しなくてよい」という利点を、この 1 組については部分的に手放している。
- **[ADR 0005](0005-dual-track-security-review.md) の Driver 行が本文と食い違ったまま残る**。読み手は
  0005 と本 ADR の 2 つを読まないと正しい Driver に辿り着けない。**履歴の正しさを読みやすさより優先した
  結果**である。
- 安全性が第 1 位になったことで、「見えるから安全」という論法が使えなくなる。既存の決定を読み直すコスト
  が一度だけ発生する。
- **「既定で最小権限」が第 1 位に来ると、権限を足す変更のたびに正当化が要る**。開発速度は下がり、
  「とりあえず広めに取って後で絞る」ができなくなる。
- -ility が 1 つ増えたことで、各 ADR の Driver 行の選択肢が増えた。**代理の Driver を書く誤りは、今回の
  修正では構造的には防げていない** — 防いでいるのは今回見つかった 1 種類だけである。
- **secret 検出のルール保守が恒久コストになった**。純正機構なら他人が更新し続けるルールを、自前で
  持ち続ける。エントロピーを持たない独自形式の credential は原理的に取りこぼす。
- **[ADR 0008](0008-github-conformist.md) GitHub Conformist から意図的に外れた領域が 1 つできた**。
  conform する相手が個人アカウントには存在しないという理由での逸脱だが、org へ移行すれば「純正と自前の
  どちらを正とするか」を決め直すことになる。
- **CI を境界に数えた結果、そこに完全な機構が無いことが確定した**。GitHub-hosted runner では egress を
  機構的に強制できず、self-hosted + egress proxy への移行は org 移行と同時期の未着手項目である。境界を
  1 つ増やして、閉じられない残余を 1 つ増やした。
- 検査点が 1 つの関数に集中したことで、**その関数が単一障害点にもなった**。検査の false negative は全
  経路に等しく効く。

### Neutral

- 本 ADR は 0009 を supersede しない。**0009 と本 ADR を合わせたものが現在の -ilities である。**
  0009 はさらに [ADR 0016](0016-quantum-scoped-fitness-functions.md) にも amend されている — 本 ADR が
  一覧と順序を、0016 が適用単位と測定方法を扱う。
- さらに -ility を追加する場合も、同じく amend ADR を積む形式を採る。0009 を頂点とする 1 本の系列として
  読めるように保つ。
- 上の 4 層のうち層 2 の**実装点**は [ADR 0015](0015-capability-broker-instead-of-container-credentials.md)
  の broker であり、本 ADR は「何を検査すべきか」だけを決める。scanner の実装 (gitleaks 等) は interface
  の背後にあり差し替え可能で、本 ADR の決定には含めない。
