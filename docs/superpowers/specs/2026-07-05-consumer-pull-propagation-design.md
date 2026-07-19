# consumer pull 型リリース追随 (Devin push 廃止) — 設計

日付: 2026-07-05
状態: 承認済み (ADR 0001 として決定を記録: docs/adr/0001-consumer-pull-release-propagation.md)

## 背景 / 問題

現行の release propagate は push 型: skills のタグ push を契機に
`.github/workflows/release-propagate.yml` が Devin API (v3) に session を作成し、
consumer リポ (agegis / dotfiles) への追随 PR 作成を Devin に委任している
(playbook: `.github/devin/consumer-update.md`)。

この構成は **Devin のクレジットが切れると propagate が全停止する** 単一依存を持つ。
また実作業の大半 (pin 書き換え → rulesync fetch/generate → PR 作成) は機械的で、
AI エージェントを常用する必然性がない。

## 決定

push 型 (skills → Devin → consumer) を廃止し、**pull 型** に置き換える:
各 consumer リポが自分の cron workflow で skills の新タグを検知し、
自リポに追随 PR を作る。実行は AI を使わない決定的スクリプト。

これにより Devin credit 依存は「fallback が要る問題」ではなく「構成から消える問題」になる。

### 検討した代替案と却下理由

| 案 | 却下理由 |
|---|---|
| Devin 失敗時に Claude Code Action へ自動 fallback | 実行主体が変わるだけで credit/rate limit 依存は残る。コスト増。 |
| Devin 失敗時に issue 起票して人間へ | 自動化が途切れる。作業自体は機械的なので人手に落とす理由がない。 |
| Renovate custom manager + 再生成 workflow | bump と rulesync 再生成が 2 段構えになり複雑。hosted Renovate は任意コマンドを実行できない。 |
| repository_dispatch (skills → consumer) | 実行は consumer 側になるが cross-repo PAT の管理が残り、純粋な pull でない。遅延要件 (日次で十分) に対して過剰。 |
| workflow を各 consumer にコピー配置 | 2 リポで重複し修正が 2 度手間。skills は rulesync 配布元でもあり、共通 workflow の配布元を兼ねるのが自然。 |

## アーキテクチャ

```
kanade0404/skills
├── .github/workflows/consumer-pull.yml   # 新設: reusable workflow (workflow_call)
├── .github/workflows/release-propagate.yml   # 削除
└── .github/devin/consumer-update.md           # 削除

kanade0404/agegis          kanade0404/dotfiles
└── .github/workflows/      └── .github/workflows/
    skills-pull.yml             skills-pull.yml
    (cron + dispatch の薄い wrapper。uses: kanade0404/skills/.github/workflows/consumer-pull.yml@master)
```

## コンポーネント

### 1. reusable workflow `consumer-pull.yml` (skills リポ、新設)

`on: workflow_call`。1 job で以下を順に行う:

1. **最新タグ解決**: `gh api repos/kanade0404/skills/tags` で `v*` タグ全件を取得し、
   **semver ソートの最大値**を採用する (tags API は commit 順で semver 順を保証しないため
   API の並び順に依存しない)。プレリリース形式 (`v1.0.0-rc1` 等) は除外。
   GitHub Release の作成には依存しない (このリポのリリースは git タグのみ)。
   MAJOR bump も自動で PR を立てる — breaking の防波堤は「人間が merge する」ゲートに
   既にあり、PR body に MAJOR 警告 (削除/リネームを compare リンクで確認せよ) を自動で載せる。
2. **冪等チェック**: ブランチ `chore/skills-<tag>` の open PR が既にある場合はスキップ終了
   (多重 PR 防止)。
3. **no-op 判定 (2 方式)**:
   - `current-ref-command` (optional input) がある場合: それを実行して現 pin を stdout で
     受け取り、最新タグと一致すれば成功終了。
     **実機確認の結果、両 consumer ともこちらを使える**: agegis は `package.json` の
     `rulesync:fetch` script に `@vX.Y.Z`、dotfiles は `rulesync.jsonc` と
     `rulesync-claude/rulesync.jsonc` の 2 箇所に `"ref": "vX.Y.Z"` を明示している。
   - 無い場合: 判定を後段に回し、update 実行後に `git diff` が空なら成功終了。
     現 consumer では使わないが、pin を持たない将来の consumer 向けの安全弁として残す
     (update が実質空振りだった場合に空 PR を作らない防御を兼ねる)。
4. **resume 判定**: open PR は無いが同ブランチ `chore/skills-<tag>` が既に存在する場合、
   「前回 run が push 成功 → PR 作成前に失敗」した形跡とみなし resume モードに入る。
   resume 中は更新実行 (次項) を丸ごとスキップし、既存ブランチから PR 作成のみを再実行する
   (自動復旧)。
5. **更新実行**: resume でない場合のみ、input の `update-command` を環境変数
   `SKILLS_TAG=<新タグ>` 付きで実行。pin 書き換え・rulesync fetch・generate は全て
   consumer 側リポ既存のスクリプトに委譲し、生成物を手編集しない
   (現 playbook の不変条件を引き継ぐ)。
6. **PR 作成**: resume でない場合は `chore/skills-<tag>` ブランチに commit・push してから、
   resume の場合は既存ブランチのまま、PR を 1 件作成。body には
   - 何を・なぜ (新タグへの追随)・再生成したもの
   - diff リンク `https://github.com/kanade0404/skills/compare/<旧>...<新>`
   - input `pr-notes` の内容 (「要人間確認」チェックリスト。機械判断できない項目を残す)

**interface (inputs / secrets):**

| 名前 | 種別 | 内容 |
|---|---|---|
| `current-ref-command` | input (optional) | 現在の pin を stdout に出すシェルコマンド。無指定時は update 後の差分有無で no-op 判定 |
| `update-command` | input (required) | `SKILLS_TAG` を受けて pin 更新 + fetch + generate を行うコマンド |
| `pr-notes` | input (optional) | PR body に追記する markdown (人間確認チェックリスト等) |
| `runtime` | input (optional, default `node`) | `node` (setup-node lts + corepack enable。agegis は pnpm) / `bun` (setup-bun。dotfiles) / `none` |
| `app-id` | input (required) | PR 作成用 GitHub App の App ID |
| `app-private-key` | secret (required) | 同 App の private key (PEM) |

**GITHUB_TOKEN でなく GitHub App token を使う理由**: `GITHUB_TOKEN` で作成した PR には
CI が自動起動しない (GitHub の再帰防止仕様)。consumer 側は「CI green を確認して merge」の
運用なので、CI が走るトークンで PR を作る必要がある。fine-grained PAT は有効期限必須
(最長 1 年) で年次更新チョアと失効リスクを抱えるため、**無期限の GitHub App** を採用する:

- App は個人アカウント配下に 1 つ作成し、権限は Contents / Pull requests の
  Read & Write のみ。インストール先は agegis / dotfiles に限定する。
- token 発行 (`actions/create-github-app-token`、1 時間有効の installation token) は
  reusable workflow 側に埋め込み、consumer は `APP_ID` / `APP_PRIVATE_KEY` を
  渡すだけにする。
- PR author は `<app名>[bot]` になり、自動化 PR と一目で区別できる。
- App の description に用途 (skills release pull 追随) を明記する
  (将来の「この App 何だっけ」防止)。
- workflow 本体は `permissions: contents: read` を明示し、外部 action は full commit SHA に
  pin する。checkout は `persist-credentials: false` とし、App token は push / PR 作成の
  瞬間のみ供給する (untrusted な依存インストールや update-command 実行中に token を
  git config に残さない)。

### 2. consumer 側 wrapper `skills-pull.yml` (agegis / dotfiles、各 1 本)

- trigger: `schedule` (日次 cron `17 21 * * *` UTC = JST 朝 6:17。正時は GitHub の cron
  混雑で遅延・スキップが起きやすいため半端な分を使う。時刻自体に意味はなく変更自由) +
  `workflow_dispatch` (リリース直後に即時反映したい時)
- 本体は `uses: kanade0404/skills/.github/workflows/consumer-pull.yml@master` に
  自リポ固有の inputs を渡すだけ。
  - agegis (pnpm): pin は `package.json` の `rulesync:fetch` script 内
    `kanade0404/skills@vX.Y.Z`。update は sed で pin 書き換え (jq だと整形差分が出る) +
    `pnpm run rulesync:fetch` + `pnpm run rulesync:generate`。
  - dotfiles (bun): pin は `rulesync.jsonc` と `rulesync-claude/rulesync.jsonc` の
    2 箇所の `"ref": "vX.Y.Z"`。JSONC (コメント入り) のため書き換えは sed、
    読み取りは grep で行い jq に依存しない。update は両ファイルの ref 書き換え +
    `bun run rulesync:skills:update` + `bun run rulesync:skills:claude:update`。
    `pr-notes` に `scripts/patch-rulesync-skill-frontmatter.ts` の削除可否確認
    (skills v0.5.0 以降不要の可能性) をチェックリストとして渡す。
- reusable workflow の参照は `@master` 固定。skills 自体のタグは skill 配布の
  バージョニングであり、workflow の互換管理には使わない (自己所有リポ間なので許容)。

### 3. skills 側の削除・文書更新

- `.github/workflows/release-propagate.yml` を削除。
- `.github/devin/consumer-update.md` を削除。playbook の内容は
  reusable workflow のロジック + 各 consumer の `pr-notes` に移す。
- `RELEASING.md` の手順 4「consumer に告知」を
  「consumer 側 cron が日次で追随 PR を作る。急ぐ場合は consumer 側で
  `skills-pull.yml` を workflow_dispatch する」に更新。
- secrets `DEVIN_API_KEY` / `DEVIN_ORG_ID` は不要になる (削除は人間が GitHub 設定で行う)。

## データフロー

```
(日次 cron / 手動 dispatch @ consumer)
  → 最新タグ解決 (skills は public、読み取りに認証不要)
  → chore/skills-<tag> ブランチの open PR 既存 → 終了 (冪等)
  → 現 pin と比較 (current-ref-command がある場合) ─ 同じ → 終了 (no-op)
  → chore/skills-<tag> ブランチが open PR 無しで既存 → resume (PR 作成のみ再実行、自動復旧)
  → (resume でなければ) update-command 実行 (pin 更新 + rulesync fetch + generate)
  → git diff 空 (current-ref-command 無しの場合の no-op) → 終了
  → PR 作成 (GitHub App installation token) → consumer CI 起動 → 人間が review / merge
```

## エラーハンドリング

- workflow 失敗はそのまま red run として consumer リポに残る (GitHub の通知に乗る)。
  追加の通知チャネルは作らない (YAGNI)。
- update-command が非ゼロ終了 → PR を作らず fail。翌日の cron が再試行する。
- 生成差分が空 (pin だけ変わって生成物が同一) の場合も pin 変更自体が commit 対象なので
  PR は作る。
- タグが日次間隔より速く連続した場合: 古いタグの open PR が残っていても、新タグは
  別ブランチ名なので新 PR が立つ。古い PR の close は人間の判断に委ねる。
- 「push 成功 → PR 作成前に失敗」の部分失敗は、次回 run が既存 branch から PR 作成のみを
  再実行して自動復旧する。

## テスト / 検証

- ドライラン: consumer 側で `workflow_dispatch` を実行し、(a) no-op 時に PR が
  立たないこと、(b) pin が古い状態で正しい PR が 1 件立つこと、(c) 再実行で
  重複 PR が立たないこと (冪等) を確認する。
- skills 側は workflow lint (actionlint 等、手元実行) のみ。build/test 基盤は無い。

## 移行手順 (段階移行)

一気に切り替えず、consumer 側の動作確認が取れるまで既存の Devin 経路を残す:

1. skills リポに `consumer-pull.yml` (reusable workflow) を追加する PR。
   この時点で `release-propagate.yml` は削除しない (共存に害はない)。
2. agegis / dotfiles に wrapper (`skills-pull.yml`) を追加し、GitHub App の作成・
   インストール・secrets 設定を行い、workflow_dispatch でドライラン検証
   (no-op / PR 作成 / 冪等スキップの 3 ケース)。
3. 検証が通ったら skills 側で `release-propagate.yml` / `.github/devin/consumer-update.md`
   を削除し RELEASING.md を更新する PR。secrets `DEVIN_API_KEY` / `DEVIN_ORG_ID` の
   削除もこの時点で行う。

## スコープ外

- consumer リポ (agegis / dotfiles) への wrapper 追加、GitHub App の作成・インストール・
  secrets (`APP_ID` / `APP_PRIVATE_KEY`) 設定は各リポ / アカウント設定での作業。
  本リポの変更とは別 PR (実装計画には含めるが、この repo の PR には入らない)。
- dotfiles のパッチスクリプト削除そのものは、初回 pull PR のチェックリストで
  人間が判断する一回性のタスク。
- 即時性 (タグ push 後数分での追随) が将来必要になったら repository_dispatch の
  追加を検討する。今回は日次 + 手動 dispatch で足りる。
