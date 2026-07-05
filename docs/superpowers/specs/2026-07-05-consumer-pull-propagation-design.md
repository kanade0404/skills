# consumer pull 型リリース追随 (Devin push 廃止) — 設計

日付: 2026-07-05
状態: 承認待ち

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

1. **最新タグ解決**: `gh api repos/kanade0404/skills/tags` から `v*` (semver) の最新を選ぶ。
   GitHub Release の作成には依存しない (このリポのリリースは git タグのみ)。
2. **冪等チェック**: ブランチ `chore/skills-<tag>` が既に存在する、または同ブランチの
   open PR がある場合はスキップ終了 (多重 PR 防止)。
3. **no-op 判定 (2 方式)**:
   - `current-ref-command` (optional input) がある場合: それを実行して現 pin を stdout で
     受け取り、最新タグと一致すれば成功終了 (agegis はこちら。pin が `package.json` に
     明示されている)。
   - 無い場合: 判定を後段に回し、update 実行後に `git diff` が空なら成功終了
     (dotfiles はこちら。declarative source 方式で「現在の pin」を持たないため、
     実際に更新を走らせて差分の有無で判定する)。
4. **更新実行**: input の `update-command` を環境変数 `SKILLS_TAG=<新タグ>` 付きで実行。
   pin 書き換え・rulesync fetch・generate は全て consumer 側リポ既存のスクリプトに委譲し、
   生成物を手編集しない (現 playbook の不変条件を引き継ぐ)。
6. **PR 作成**: `chore/skills-<tag>` ブランチに commit し、PR を 1 件作成。body には
   - 何を・なぜ (新タグへの追随)・再生成したもの
   - diff リンク `https://github.com/kanade0404/skills/compare/<旧>...<新>`
   - input `pr-notes` の内容 (「要人間確認」チェックリスト。機械判断できない項目を残す)

**interface (inputs / secrets):**

| 名前 | 種別 | 内容 |
|---|---|---|
| `current-ref-command` | input (optional) | 現在の pin を stdout に出すシェルコマンド。無指定時は update 後の差分有無で no-op 判定 |
| `update-command` | input (required) | `SKILLS_TAG` を受けて pin 更新 + fetch + generate を行うコマンド |
| `pr-notes` | input (optional) | PR body に追記する markdown (人間確認チェックリスト等) |
| `setup-node` | input (optional, default true) | `actions/setup-node` を実行するか (両 consumer とも npm script 前提) |
| `pr-token` | secret (required) | PR 作成・push 用の repo 限定 fine-grained PAT |

**PAT を要求する理由**: `GITHUB_TOKEN` で作成した PR には CI が自動起動しない
(GitHub の再帰防止仕様)。consumer 側は「CI green を確認して merge」の運用なので、
CI が走るトークンで PR を作る必要がある。PAT は各 consumer リポに repo 限定・
contents/pull-requests write の最小権限で置く。

### 2. consumer 側 wrapper `skills-pull.yml` (agegis / dotfiles、各 1 本)

- trigger: `schedule` (日次 cron) + `workflow_dispatch` (リリース直後に即時反映したい時)
- 本体は `uses: kanade0404/skills/.github/workflows/consumer-pull.yml@master` に
  自リポ固有の inputs を渡すだけ。
  - agegis: pin は `package.json` の `rulesync:fetch` script 内 `kanade0404/skills@<ref>`。
    update は fetch script の ref 書き換え + `rulesync:fetch` + generate 系 script。
  - dotfiles: `rulesync.jsonc` の declarative source 方式。
    `rulesync:skills:update` / `rulesync:skills:claude:update` を実行。
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
  → chore/skills-<tag> ブランチ / open PR 既存 → 終了 (冪等)
  → 現 pin と比較 (current-ref-command がある場合) ─ 同じ → 終了 (no-op)
  → update-command 実行 (pin 更新 + rulesync fetch + generate)
  → git diff 空 (current-ref-command 無しの場合の no-op) → 終了
  → PR 作成 (PAT) → consumer CI 起動 → 人間が review / merge
```

## エラーハンドリング

- workflow 失敗はそのまま red run として consumer リポに残る (GitHub の通知に乗る)。
  追加の通知チャネルは作らない (YAGNI)。
- update-command が非ゼロ終了 → PR を作らず fail。翌日の cron が再試行する。
- 生成差分が空 (pin だけ変わって生成物が同一) の場合も pin 変更自体が commit 対象なので
  PR は作る。
- タグが日次間隔より速く連続した場合: 古いタグの open PR が残っていても、新タグは
  別ブランチ名なので新 PR が立つ。古い PR の close は人間の判断に委ねる。

## テスト / 検証

- ドライラン: consumer 側で `workflow_dispatch` を実行し、(a) no-op 時に PR が
  立たないこと、(b) pin が古い状態で正しい PR が 1 件立つこと、(c) 再実行で
  重複 PR が立たないこと (冪等) を確認する。
- skills 側は workflow lint (actionlint 等、手元実行) のみ。build/test 基盤は無い。

## スコープ外

- consumer リポ (agegis / dotfiles) への wrapper 追加と PAT 設定は各リポでの作業。
  本リポの変更とは別 PR (実装計画には含めるが、この repo の PR には入らない)。
- dotfiles のパッチスクリプト削除そのものは、初回 pull PR のチェックリストで
  人間が判断する一回性のタスク。
- 即時性 (タグ push 後数分での追随) が将来必要になったら repository_dispatch の
  追加を検討する。今回は日次 + 手動 dispatch で足りる。
