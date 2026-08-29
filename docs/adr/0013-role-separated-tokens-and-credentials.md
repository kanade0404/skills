# トークンと credential を役割で分離する

Status: accepted (2026-08-29)

Driver: [安全性 (Secure by Design)](0014-add-security-to-ility-priority-order.md) — 権限は既定で最小、
境界の強制は規約ではなく機構で行う。同時に
[-ilities 1 可監査性・回復可能性](0009-ility-priority-order.md) が要求する権威の一意性を、token の層で
支える。用語は [CONTEXT.md](../../CONTEXT.md) に従う。

## Context

このパイプラインには書き手が 3 種いる。権威面と派生面を書く worker、実装差分を書く Codex コンテナ、
merge する人間である。3 者が同じ credential を共有すると、
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

## Decision

credential を役割で分離する。

| credential | 保持者 | スコープ | 用途 |
|---|---|---|---|
| GitHub App の秘密鍵 | **worker のみ** (ディスクに置かず起動時に secret store から取得) | — | installation token の発行 |
| Codex コンテナ token | Codex コンテナ (job 別・短命) | **code repo の contents + pull requests のみ。`issues:write` を持たない** | 実装差分の push と PR 作成 |
| 権威面 (state repo) への書き込み | **worker のみ** | state repo | lease・遷移・予算・heartbeat |
| Codex の auth (ChatGPT auth または API key) | **worker のみ** | — | Codex SDK の認証 ([ADR 0010](0010-resident-worker-with-codex-python-sdk.md)) |

- **Codex コンテナから `issues:write` を外すのは、契約ブロック偽造の遮断が目的**である。コメントと
  ラベルは worker だけが書く。Codex が起草した実装 issue 本文はデータとして扱い、worker が契約スキーマ
  で検証・正規化して起票する。
- **push 先の制限は ruleset で表現する** — `codex/**` 以外への push 禁止、force-push 禁止、
  `.github/workflows/**` および契約スキーマ・ac-verify スクリプトの変更は required review。
- **人間の merge ゲートは branch protection + ruleset で機構的に担保**し、**App に merge 系の権限を
  与えない** ([ADR 0004](0004-two-human-approval-gates.md) の機構的前提)。
- **token のスコープでは merge を独立に禁止できない**ことを明記する — GitHub の権限モデルでは、PR の
  merge は `Contents: write` の下に属し、push に必要な `Contents: write` と分離できない。したがって
  human-only merge の実効的な強制点は **branch protection の required review 数のみ**であり、Codex
  コンテナの token スコープはこれを独立には守らない。したがって code repo の ruleset は、**Codex の
  App/token を bypass actor に登録せず**、**review dismissal を人間アクターに限定する**ことで、この
  境界を token スコープではなく ruleset 側で機構的に成立させる。**worker の Integration bypass は
  state repo の ruleset に対してのみ張り**、code repo 側では Codex を含めどの credential も bypass
  actor に登録しない ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md) の Considered
  Options が「code repo の ruleset へ権威用の bypass を張ると人間の merge ゲートが緩む」ことを S1
  却下理由に挙げている、その裏返し)。
- token の撤回経路は App の installation suspend とする。鍵の rotation 手順と侵害時の suspend 手順を
  運用文書に持つ。
- **App 化は Phase 1 の完了条件**とする。PAT の暫定運用は Phase 0-1 の開発中に限り、**権威分離の無い
  dev モードであることを明示する**。パイロットの本運用 (Phase 2 の開始条件) に App 化を含める。

## Considered Options

- **単一の PAT で全てを賄う** — 実測で bypass actor の粒度が人間と一致してしまい、worker と人間の書き
  込みを分離できない。[ADR 0012](0012-write-authority-by-lease-and-sha-cas.md) の lease が token の層
  では支えられず、「書けるが書かない約束」に退化する。開発中の暫定としてのみ許す。却下。
- **Codex コンテナに `issues:write` を与え、自分で実装 issue を起票させる** — 起票の手間は減るが、Codex
  が起草した本文がそのまま指示として読まれる経路ができる。信頼のロンダリングであり、repo 境界では塞げ
  ない。却下。
- **token で push 先の branch を絞る** — installation token は branch を絞れない。表現できない制約を
  token に期待せず、ruleset で表現する。却下。
- **App の秘密鍵を VM のディスクに置く** — 使い捨て VM の前提と噛み合わず、イメージやスナップショットに
  鍵が残る経路ができる。起動時に secret store から取得する形にする。却下。
- **App に merge 権限を与えて「機械 merge + 事後レビュー」にする** — [ADR 0004](0004-two-human-approval-gates.md)
  の承認ゲート 2 点と正面から衝突し、人間ゲートの機構的前提を自ら壊す。却下。
- **Codex コンテナに state repo への読み取り権限だけ与える** — 読めれば lease の内容が Codex の context
  に入り、権威面の語彙が「データとして引用される」経路に載る。必要が無いので与えない。却下。

## Consequences

### Positive

- 権威面への書き込みが credential の層で 1 者に閉じる。lease は約束ではなく「**書けないから書かない**」
  で支えられる。
- Codex が契約ブロックを偽造する経路が token のスコープで塞がる。信頼境界 (指示 / データ) の線が、機構
  の線と一致する。
- 侵害時の撤回が installation suspend という 1 操作になる。撤回対象を探し回らなくてよい。
- token が短命なので、コンテナの残骸から漏れた token の有効期間が有界である。

### Negative

- **consumer のセットアップが増える**。GitHub App の作成・インストール・権限設定・secret store の用意
  が、パイプラインを使い始める前の前提になる。PAT を貼るだけでは動かない。
- **token 発行の実装を自前で持つ**。JWT 署名・installation token の取得・失効・job への受け渡しが worker
  のコードになり、**そこ自体が新しい攻撃面**である。
- **secret store という外部依存が増える** ([ADR 0010](0010-resident-worker-with-codex-python-sdk.md) の
  常駐 VM と合わせて 2 つ目)。secret store が落ちれば worker は起動すらできない。
- App 化が Phase 1 の完了条件なので、**Phase 0-1 は権威分離が無い状態で走る**。この期間の実測結果は、
  権威分離の検証としては使えない。
- **書き込み範囲が token の権限表と ruleset の 2 箇所に分かれている**。token の権限だけを読んでも実際に
  どこへ書けるかは分からず、監査は常に両方を突き合わせる必要がある。
- 秘密鍵が worker に集中するため、**worker の VM 自体が最も価値の高い攻撃対象になる**。分離した代償と
  して、1 点が破られたときの被害は大きい。
- 短命 token の失効タイミングと長時間の Codex thread が噛み合わないと、実行の途中で push できなくなる。
  token の寿命は thread の時間上限との関係で決めねばならず、独立に短くはできない。

### Neutral

- 権限表の細部 (どの job にどのスコープを配るか) は two-way door。**GitHub App というアーキテクチャの
  選択は one-way door に近い**。
- 本 ADR は [ADR 0009](0009-ility-priority-order.md) が「未起票の下流決定」として挙げていた token の分離
  に相当する。
