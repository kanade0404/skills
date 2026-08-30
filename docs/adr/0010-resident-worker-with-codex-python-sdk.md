# Codex 実行を常駐 worker + Codex Python SDK で駆動し、codex-action を別設計の contingency とする

Status: accepted (2026-08-29)

Driver: [-ilities 2 有界性](0009-ility-priority-order.md) (同時 thread 数と thread 時間に数値の上限を
置けること、abort を一級操作として持てること)。同 1 可監査性・回復可能性 (実行体の進行が GitHub から
復元できること) にも従う。用語は [CONTEXT.md](../../CONTEXT.md) に従う。

## Context

Codex を人間が張り付かない形で駆動する経路として、以下の 5 つを調査した。判断は 2026-08-29 時点の
実測とソース実読に基づく。

- **codex-action** — GitHub Actions 上で Codex を走らせる。動く。
- **@codex mention** — issue / PR のコメントで Codex を呼ぶ。**issue コメントへの対応が公式に確認
  できない** (確認できたのは PR コメント経路のみ)。
- **Agent HQ からの assign** — UI からは可能だが、**API 経由の assign が不可であることを実測した**。
- **cloud exec** — Codex の cloud 実行。**environment ID を外から解決する経路が無く**、これは
  #24777 として open のまま。
- **Codex SDK を自前の実行体から呼ぶ** — TS 版と Python 版がある。

SDK の 2 版はソースを実読した結果、別物であることが分かった。**Python SDK (`openai-codex`) は
`codex app-server` を 1 プロセス常駐させて JSON-RPC で多重化し、「one client can consume multiple
active turns concurrently」と並行 turn を公式にサポートする。`thread/start` は thread ごとの `cwd`
を受け取るため、thread ごとに worktree を割り当てられる。TS SDK は turn ごとに `codex exec` を
spawn する設計で、並行は非文書化である。** この 2 点が言語選択の決定的な根拠になった。

Codex の hooks には依存できない。#27133 により worktree では hooks discovery が壊れ、これは sandbox
の設定に依存しないため bypass 経路でも残る。

無人運用の credential については、調査の途中で認識を訂正した。refresh token は厳密な単回使用では
なく、OpenAI 側が「約 1 時間の再利用窓があり race はサーバ側で緩和される」と明言している (#10332)。
したがって当初懸念していた「並行実行が refresh を壊す」は本命ではない。**本命は #26303 — 逐次実行
でも `token_invalidated` が発生するという報告で、open かつ未応答である。直列化という回避策が効かない
種類のリスクである。** なお #15410 は別 `CODEX_HOME` への `auth.json` コピーの問題で、単一プロセス
構成には当たらない。#27418 は built-in Linux sandbox × worktree の gitdir 問題で、外部コンテナ +
bypass 経路なら直撃しない見込みだが、これは推測であって実測ではない。

価格の事実として、Business は最低 2 席で月払い $50/月 (年払い $40/月相当)。この数値は第三者プロキシ
経由で取得したものなので、購入前に原本の再確認が要る。「Business と Plus のレート同一」は未確認。

## Decision

Codex の実行を、**常駐 worker (VM daemon) 内の 1 プロセス・1 client・N threads** の構成とする。
実装は **Codex Python SDK** を使う。

- thread ごとに worktree を割り当てる (`thread/start` の `cwd`)。
- issue ↔ `thread_id` の対応は権威面に記録する ([ADR 0011](0011-authority-state-in-dedicated-state-repo.md)
  の `state.json` の runtime フィールド)。worker のメモリ上の対応表はキャッシュである。
- **thread resume を第一候補**とし、thread を失った場合にのみ stateless 再構成にフォールバックする。
- **codex hooks は使わない** (#27133)。**境界の強制はコンテナの外側だけで行う** — コンテナ隔離・
  credential の分離 ([ADR 0013](0013-role-separated-tokens-and-credentials.md))・broker の受理検査
  ([ADR 0015](0015-capability-broker-instead-of-container-credentials.md))・ruleset。コンテナ内で動く
  ものは境界にしない、が要点である。
- **abort を一級操作として持つ**。版上げ・cap 超過・needs-human 遷移・lease 喪失時に、worker は当該
  issue に紐づく thread とコンテナを kill する。
- **codex-action は fallback ではなく contingency (別設計への切替) として保持する**。

### 未決: 無人運用の auth 方式

本 ADR は auth 方式を確定しない。決定の構造を以下に固定する。

- **問い**: 無人運用の credential を A (Business access token、最低 2 席 $50/月) / B (個人 Pro) /
  C (API key) のどれにするか。
- **default**: **B (個人 Pro) で PoC する**。追加費用ゼロで、SDK の公式 API surface の内側に収まる。
  Business に劣るのは「unattended な常駐運用の契約上の明示性」だけである。
- **deadline / 決定条件**: PoC で **#26303 の再現有無を実測してから**決める。A へ移行するのは、
  ① PoC が安定して常設のインフラになった段階、または ② auth 失効が運用障害として実際に顕在化した
  時点。どちらも来なければ B のままでよい。
- **移行を設計に波及させない**: worker は auth 方式を知らない抽象で書き、移行は credential の差し替え
  だけで済むようにする。失効検知 → needs-human (人間による再認証要求) の経路は、どの方式を選んでも
  必須とする。

## Considered Options

- **@codex mention** — パイプラインの起点はタスク issue であり、issue コメントからの起動が公式に
  確認できない経路に、唯一の起動手段を賭けることはできない。却下。
- **Agent HQ への assign** — API 経由の assign が不可であることを実測した。無人運用は API 起動が
  前提なので、UI からしか押せない経路は使えない。却下。
- **cloud exec** — environment ID の解決経路が無く (#24777 open)、外部から environment を特定して
  実行を投げられない。却下。
- **TS SDK** — turn ごとに `codex exec` を spawn する設計で、並行 turn は非文書化。「同時 thread 3」
  という有界性の数値を、非文書化の挙動の上に置くことになる。却下。
- **codex-action (Actions 上で Codex を走らせる)** — **これは同一設計の縮退ではない。** 単一の書き込み
  点・clock の所有・鍵の単独保持・abort 可能な子プロセスの管理のいずれも引き継げず、鍵を Actions
  secret に置くことになる。引き継げるのは契約 (issue 規約・ラベル・ac-verify) だけで、切替コストは
  worker 層の書き直しである。**contingency として位置づけを保持するが、安価な保険ではない。**

## Consequences

### Positive

- 1 プロセスで N thread を並行に持てるため、同時 thread 数を数値で上限できる (-ilities 2)。
- thread ごとに `cwd` = worktree を割り当てられるので、issue ごとの作業が隔離される。
- thread resume により、worker の再起動をまたいで会話文脈を維持できる。失った場合の縮退経路
  (stateless 再構成) も残っている。
- 実行体を自分で持つため abort が一級操作になる。Actions 経由では「書き手が走り続ける」を止められ
  ない。
- Codex の実行状態 (`thread_id` / `container_id`) を権威面に書けるため、実行体の内部だけが GitHub
  から見えない例外領域になることを避けられる。

### Negative

- **常駐 VM の運用が発生する**。session の GC と process の reap を自前で持つことになり、SDK も
  GitHub もこれを代行しない。落ちれば heartbeat 失効として現れるが、復旧作業は人間に来る。
- **codex hooks に依存できない** (#27133)。Codex 側のライフサイクルに割り込む公式の口が無いため、
  境界は外側からしか強制できず、内側の観測は薄いままになる。
- **ChatGPT auth の無人運用リスクが残る**。#26303 は open・未応答で、逐次実行でも起きるため直列化と
  いう回避策が無い。失効すれば人間の再認証が要り、その間パイプラインは停止する。**この停止は設計で
  消せない。**
- **worktree × 外部コンテナ (DinD) が未検証**。#27418 が直撃しないという判断は推測であり、PoC の必須
  ゲートである。ここが不成立なら実行モデルごと見直しになる。
- auth 方式が未決のまま Phase 2 に入る。default (個人 Pro) は、個人アカウントの契約で無人常駐を回す
  ことの契約上の明示性が無い状態である。
- **contingency が別設計であるため、切替コストが高い**。「駄目なら codex-action」は移行ではなく再実装
  であり、この ADR はそれを承知で単一の経路に賭けている。

### Neutral

- Python SDK / TS SDK の選択は two-way door だが、TS へ戻る動機は現時点で無い。
- auth 方式は two-way door (credential の差し替え) — そのために worker を auth 非依存に書くことが、
  本決定の一部である。
- PoC 3 件 (単一 client での多 thread 並行の安定性 / worktree × 外部コンテナ / lease プロトコルの
  競合試験) は Phase 2 前の必須ゲートであり、いずれも撤退基準を持つ。
