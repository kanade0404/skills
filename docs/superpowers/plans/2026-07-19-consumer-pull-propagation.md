# Consumer Pull 型リリース追随 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** skills のリリース追随を Devin push 型から consumer 側 cron の pull 型に置き換え、Devin credit 依存を構成から消す。

**Architecture:** skills リポが reusable workflow `consumer-pull.yml` (workflow_call) を配布し、consumer (agegis / dotfiles) は cron + workflow_dispatch の薄い wrapper から `@master` 参照で呼ぶ。PR 作成は GitHub App の installation token (CI が起動するため)。検証完了後に skills 側の Devin 経路を削除する段階移行。

**Tech Stack:** GitHub Actions (reusable workflow), `actions/create-github-app-token`, gh CLI, bash。consumer 側は agegis = pnpm / dotfiles = bun。

**参照:** spec `docs/superpowers/specs/2026-07-05-consumer-pull-propagation-design.md` / ADR `docs/adr/0001-consumer-pull-release-propagation.md`

## Global Constraints

- ブランチ名は `chore/skills-<tag>`、cron は `17 21 * * *` (UTC、正時回避)
- reusable workflow の参照は `@master` 固定
- 生成物 (`.rulesync/`, `.claude/` 等) は consumer リポ既存スクリプトで再生成し手編集しない
- PR は作るだけで merge しない (人間が merge)
- タグ解決は `v*` 全件の semver ソート最大値、プレリリース (`-` 付き) 除外、MAJOR も自動 PR + body 警告
- CI 内で GitHub API を叩くツールには `GH_TOKEN` / `GITHUB_TOKEN` を明示的に渡す (匿名クォータ 60req/h で枯渇する。`.claude/rules/bash-and-api-discipline.md`)
- 機械処理は `NO_COLOR=1` / `CLICOLOR_FORCE=0` を設定する (同 rule)
- 段階移行: Task 1-2 (skills に追加) → Task 3-6 (App + consumer 検証) → Task 7 (Devin 経路削除)。順序を入れ替えない

**現状スナップショット (2026-07-19 実機確認済み):**

- skills 最新タグ: `v0.8.0`
- agegis: `package.json` の `rulesync:fetch` = `rulesync fetch kanade0404/skills@v0.8.0 --features skills,rules` (最新に追随済み)。pnpm (`packageManager: pnpm@10.34.5`、`pnpm-lock.yaml`)。rulesync は devDependency
- dotfiles: `rulesync.jsonc` と `rulesync-claude/rulesync.jsonc` の 2 箇所に `"ref": "v0.6.0"` (**2 マイナー遅れ**)。bun (`bun.lockb`)。両 JSONC ともコメント入りのため jq 不可、sed/grep で扱う。skills 以外の source (planetscale) に `ref` キーは無い (sed の一括置換が skills の ref だけに当たる前提)

---

### Task 1: reusable workflow `consumer-pull.yml` を skills リポに追加

**Files:**
- Create: `.github/workflows/consumer-pull.yml`

**Interfaces:**
- Consumes: なし (先行タスクなし)
- Produces: workflow_call interface — inputs `update-command` (string, required) / `current-ref-command` (string, optional, default `''`) / `pr-notes` (string, optional, default `''`) / `runtime` (string, optional, default `node`) / `app-id` (string, required)、secrets `app-private-key` (required)。Task 4 / 5 の wrapper はこの名前に依存する

- [ ] **Step 1: workflow ファイルを書く**

`.github/workflows/consumer-pull.yml` を以下の内容で作成:

```yaml
name: consumer pull

# kanade0404/skills のリリースタグへ追随する PR を consumer リポに作る reusable workflow。
# consumer は cron + workflow_dispatch の薄い wrapper から uses: で呼ぶ。
# 設計: docs/adr/0001-consumer-pull-release-propagation.md /
#       docs/superpowers/specs/2026-07-05-consumer-pull-propagation-design.md
on:
  workflow_call:
    inputs:
      update-command:
        description: 'SKILLS_TAG 環境変数を受けて pin 更新 + rulesync fetch/generate を行うコマンド'
        required: true
        type: string
      current-ref-command:
        description: '現在追随している skills の ref を stdout へ出すコマンド。空なら update 実行後の差分有無で no-op 判定'
        required: false
        type: string
        default: ''
      pr-notes:
        description: 'PR body 末尾「要確認」節に載せる markdown (機械判断できない項目のチェックリスト)'
        required: false
        type: string
        default: ''
      runtime:
        description: 'node (setup-node lts + corepack enable) / bun (setup-bun) / none'
        required: false
        type: string
        default: 'node'
      app-id:
        description: 'PR 作成用 GitHub App の App ID'
        required: true
        type: string
    secrets:
      app-private-key:
        description: 'PR 作成用 GitHub App の private key (PEM)'
        required: true

permissions:
  contents: read
  pull-requests: read

jobs:
  pull:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    concurrency:
      group: skills-pull-${{ github.repository }}
      cancel-in-progress: false
    env:
      NO_COLOR: '1'
      CLICOLOR_FORCE: '0'
    steps:
      - name: Create GitHub App token
        id: app
        uses: actions/create-github-app-token@d72941d797fd3113feb6b93fd0dec494b13a2547 # v1.12.0
        with:
          app-id: ${{ inputs.app-id }}
          private-key: ${{ secrets.app-private-key }}

      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.3.1
        with:
          persist-credentials: false

      - name: Resolve latest skills tag
        id: latest
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          # tags API は semver 順を保証しないため全件を semver ソートして最大を採る。
          # プレリリース (v1.0.0-rc1 等ハイフン付き) は除外。
          tag=$(gh api --paginate repos/kanade0404/skills/tags --jq '.[].name' \
            | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true; } | sort -V | tail -n1)
          [ -n "$tag" ] || { echo "::error::kanade0404/skills に v* タグが見つからない"; exit 1; }
          echo "tag=$tag" >> "$GITHUB_OUTPUT"

      - name: Gate (idempotency / no-op)
        id: gate
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ steps.latest.outputs.tag }}
          CURRENT_REF_COMMAND: ${{ inputs.current-ref-command }}
        run: |
          set -euo pipefail
          branch="chore/skills-${TAG}"
          open_prs=$(gh pr list -R "$GITHUB_REPOSITORY" --head "$branch" --state open --json number --jq length)
          if [ "$open_prs" != "0" ]; then
            echo "branch $branch の open PR が既にあるためスキップ (冪等)"
            echo "skip=true" >> "$GITHUB_OUTPUT"; exit 0
          fi
          current=""
          if [ -n "$CURRENT_REF_COMMAND" ]; then
            current=$(bash -c "set -euo pipefail; $CURRENT_REF_COMMAND")
            if [ "$current" = "$TAG" ]; then
              echo "現 pin ($current) が最新タグと一致、スキップ (no-op)"
              echo "skip=true" >> "$GITHUB_OUTPUT"; exit 0
            fi
          fi
          resume=false
          branch_exists=false
          if out=$(gh api "repos/${GITHUB_REPOSITORY}/branches/${branch}" 2>&1); then
            branch_exists=true
          elif ! printf '%s' "$out" | grep -q 'HTTP 404'; then
            echo "::error::branch 存在確認が 404 以外で失敗: ${out}"
            exit 1
          fi
          if [ "$branch_exists" = "true" ]; then
            closed_prs=$(gh pr list -R "$GITHUB_REPOSITORY" --head "$branch" --state closed --json number --jq length)
            if [ "$closed_prs" != "0" ]; then
              # 人間が close (または merge 後 branch 未削除) した PR を毎日作り直さない。
              # 意図的に再実行したい場合は branch を削除する。
              echo "branch $branch には closed/merged PR の履歴があるためスキップ (PR は再作成しない)"
              echo "skip=true" >> "$GITHUB_OUTPUT"; exit 0
            fi
            # 前回 run が「push 成功 → PR 作成前に失敗」した形跡。既存 branch から PR 作成のみ再試行する
            echo "branch $branch は存在するが PR 履歴が無いため、PR 作成のみ再実行する (自動復旧)"
            resume=true
          fi
          {
            echo "resume=$resume"
            echo "current=$current"
            echo "skip=false"
          } >> "$GITHUB_OUTPUT"

      - name: Setup node
        if: ${{ steps.gate.outputs.skip == 'false' && steps.gate.outputs.resume == 'false' && inputs.runtime == 'node' }}
        uses: actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0
        with:
          node-version: 'lts/*'

      - name: Enable corepack
        if: ${{ steps.gate.outputs.skip == 'false' && steps.gate.outputs.resume == 'false' && inputs.runtime == 'node' }}
        run: corepack enable

      - name: Setup bun
        if: ${{ steps.gate.outputs.skip == 'false' && steps.gate.outputs.resume == 'false' && inputs.runtime == 'bun' }}
        uses: oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6 # v2.2.0

      - name: Run update command
        if: ${{ steps.gate.outputs.skip == 'false' && steps.gate.outputs.resume == 'false' }}
        env:
          SKILLS_TAG: ${{ steps.latest.outputs.tag }}
          UPDATE_COMMAND: ${{ inputs.update-command }}
          # rulesync fetch 等が GitHub API を叩く。匿名クォータ (60req/h) 回避のため明示的に渡す
          GH_TOKEN: ${{ github.token }}
          GITHUB_TOKEN: ${{ github.token }}
        run: |
          set -euo pipefail
          bash -c "set -euo pipefail; $UPDATE_COMMAND"

      - name: Create PR
        if: ${{ steps.gate.outputs.skip == 'false' }}
        env:
          GH_TOKEN: ${{ steps.app.outputs.token }}
          TAG: ${{ steps.latest.outputs.tag }}
          CURRENT: ${{ steps.gate.outputs.current }}
          PR_NOTES: ${{ inputs.pr-notes }}
          APP_SLUG: ${{ steps.app.outputs.app-slug }}
          BASE_BRANCH: ${{ github.event.repository.default_branch }}
          RESUME: ${{ steps.gate.outputs.resume }}
        run: |
          set -euo pipefail
          branch="chore/skills-${TAG}"
          if [ "$RESUME" != "true" ]; then
            if [ -z "$(git status --porcelain)" ]; then
              echo "update 後の差分が無いため PR を作らない (no-op)"; exit 0
            fi
            git config user.name "${APP_SLUG}[bot]"
            git config user.email "${APP_SLUG}[bot]@users.noreply.github.com"
            git switch -c "$branch"
            # 生成物のパスは consumer ごとに異なるため全差分を commit 対象にする
            git add -A
            git commit -m "chore: kanade0404/skills ${TAG} へ追随"
            git push "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" "HEAD:refs/heads/${branch}"
          fi
          {
            echo "kanade0404/skills のリリース **${TAG}** への追随 (pull workflow による自動生成 PR)。"
            echo
            echo "- pin / 取得内容を \`${TAG}\` に更新し、リポ既存スクリプトで設定を再生成"
            if [ -n "$CURRENT" ]; then
              echo "- 差分: https://github.com/kanade0404/skills/compare/${CURRENT}...${TAG}"
              cur_major="${CURRENT#v}"; cur_major="${cur_major%%.*}"
              new_major="${TAG#v}"; new_major="${new_major%%.*}"
              if [ "$cur_major" != "$new_major" ]; then
                echo
                echo "> [!WARNING]"
                echo "> **MAJOR bump** です。skill の削除 / リネーム / 挙動契約の変更を含みます。上の compare リンクで確認してから merge してください。"
              fi
            else
              echo "- リリースタグ: https://github.com/kanade0404/skills/releases/tag/${TAG}"
            fi
            if [ -n "$PR_NOTES" ]; then
              echo
              echo "## 要確認 (機械判断できない項目)"
              echo
              echo "$PR_NOTES"
            fi
          } > pr-body.md
          gh pr create -R "$GITHUB_REPOSITORY" \
            --base "$BASE_BRANCH" --head "$branch" \
            --title "chore: skills ${TAG} への追随" \
            --body-file pr-body.md
```

- [ ] **Step 2: actionlint で検証**

Run: `command -v actionlint >/dev/null || brew install actionlint; actionlint .github/workflows/consumer-pull.yml`
Expected: 出力なし (エラーゼロ)。shellcheck 由来の warning が出たら該当 shell を修正する

- [ ] **Step 3: タグ解決ロジックを手元で単体実行して確認**

Run:

```bash
gh api --paginate repos/kanade0404/skills/tags --jq '.[].name' \
  | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n1
```

Expected: `v0.8.0` (実行時点の最新 semver タグ)

- [ ] **Step 4: Commit**

commit skill の手順 (観測 → 明示パス staging → ファイル経由メッセージ) に従う:

```bash
git add .github/workflows/consumer-pull.yml
git commit -F <scratch>/commit-msg.txt
# メッセージ要約: "ci: consumer pull 型追随の reusable workflow を追加"
```

---

### Task 2: skills 側 PR の作成 (spec / ADR / Task 1 を含む)

**Files:**
- なし (push と PR 作成のみ)

**Interfaces:**
- Consumes: Task 1 の commit 済みブランチ `docs/consumer-pull-propagation-design`
- Produces: merge 可能な PR。merge 後に `consumer-pull.yml@master` が consumer から参照可能になる (Task 4 / 5 の前提)

- [ ] **Step 1: ブランチを push して PR 作成**

`shipping` skill (無ければ `gh pr create`) で PR を作る。body には spec / ADR / workflow 追加の 3 点と、「この PR では既存 `release-propagate.yml` を削除しない (段階移行、削除は検証後の別 PR)」を明記

- [ ] **Step 2: CI green を確認し、レビュー対応後、人間の merge を待つ**

`.claude/rules/pr-push-discipline.md` に従い CI 完了確認と監視 (`pr-monitor`) を設置。**merge されるまで Task 4 以降に進まない** (`@master` 参照が 404 になるため)

---

### Task 3: GitHub App の作成と consumer への配線 (人間の手作業)

**Files:** なし (GitHub 設定のみ)

**Interfaces:**
- Produces: 両 consumer リポの variable `SKILLS_PULL_APP_ID` / secret `SKILLS_PULL_APP_PRIVATE_KEY`。Task 4 / 5 の wrapper はこの名前に依存する

人間向けチェックリスト (エージェントは URL 提示と確認のみ、設定操作は人間):

- [ ] https://github.com/settings/apps/new で App 作成。名前: `skills-release-pull`、description: 「kanade0404/skills のリリースタグに consumer リポを追随させる pull workflow の PR 作成用」、Webhook: 無効、権限: Repository permissions で Contents = Read and write / Pull requests = Read and write のみ
- [ ] App ID を控える (App 設定画面の About に表示)
- [ ] Private key を生成して PEM をダウンロード
- [ ] App を自分のアカウントにインストールし、対象を **agegis と dotfiles の 2 リポに限定**
- [ ] agegis / dotfiles 両方の Settings → Secrets and variables → Actions に登録:
  - Variables: `SKILLS_PULL_APP_ID` = App ID
  - Secrets: `SKILLS_PULL_APP_PRIVATE_KEY` = PEM 全文 (**改行を壊さずコピーする** — 貼り付けミスが典型的なハマりどころ)

---

### Task 4: agegis に wrapper `skills-pull.yml` を追加 (別リポ作業)

**Files:**
- Create (kanade0404/agegis): `.github/workflows/skills-pull.yml`

**Interfaces:**
- Consumes: Task 1 の workflow_call interface (`@master` 経由)、Task 3 の variable / secret
- Produces: agegis の日次 pull workflow。Task 6 の「no-op ケース」検証対象

- [ ] **Step 1: agegis に feature ブランチを切り、以下を作成**

```yaml
name: skills pull

# kanade0404/skills の新リリースタグへ日次で追随する (pull 型)。
# 実体は skills リポ配布の reusable workflow。
# 設計: kanade0404/skills の docs/adr/0001-consumer-pull-release-propagation.md
on:
  schedule:
    - cron: '17 21 * * *' # JST 朝 6:17。正時は GitHub cron が混雑するため回避
  workflow_dispatch:

jobs:
  pull:
    uses: kanade0404/skills/.github/workflows/consumer-pull.yml@master
    with:
      app-id: ${{ vars.SKILLS_PULL_APP_ID }}
      runtime: node
      current-ref-command: |
        grep -oE 'kanade0404/skills@v[0-9]+\.[0-9]+\.[0-9]+' package.json | head -n1 | cut -d@ -f2
      update-command: |
        sed -i -E 's|(kanade0404/skills@)v[0-9]+\.[0-9]+\.[0-9]+|\1'"$SKILLS_TAG"'|' package.json
        pnpm install --frozen-lockfile
        pnpm run rulesync:fetch
        pnpm run rulesync:generate
    secrets:
      app-private-key: ${{ secrets.SKILLS_PULL_APP_PRIVATE_KEY }}
```

- [ ] **Step 2: actionlint で検証**

Run: `actionlint .github/workflows/skills-pull.yml`
Expected: 出力なし

- [ ] **Step 3: commit し、PR 作成 → CI green → 人間が merge**

commit 要約: `ci: skills リリースへの日次 pull 追随 workflow を追加`。agegis の CI 慣例に従う

---

### Task 5: dotfiles に wrapper `skills-pull.yml` を追加 (別リポ作業)

**Files:**
- Create (kanade0404/dotfiles): `.github/workflows/skills-pull.yml`

**Interfaces:**
- Consumes: Task 1 の workflow_call interface (`@master` 経由)、Task 3 の variable / secret
- Produces: dotfiles の日次 pull workflow。Task 6 の「PR 作成 / 冪等ケース」検証対象 (現 pin v0.6.0 は 2 マイナー遅れのため初回実行で必ず PR が立つ)

- [ ] **Step 1: dotfiles に feature ブランチを切り、以下を作成**

```yaml
name: skills pull

# kanade0404/skills の新リリースタグへ日次で追随する (pull 型)。
# 実体は skills リポ配布の reusable workflow。
# 設計: kanade0404/skills の docs/adr/0001-consumer-pull-release-propagation.md
#
# pin は rulesync.jsonc / rulesync-claude/rulesync.jsonc の 2 箇所の "ref"。
# 両ファイルとも JSONC (コメント入り) のため jq でなく sed / grep で扱う。
# skills 以外の source (planetscale) に ref キーは無い前提 (追加時は sed の対象を見直す)。
# ref は 2 ファイルの一致を確認し、食い違いは update 実行で自己修復する。
on:
  schedule:
    - cron: '17 21 * * *' # JST 朝 6:17。正時は GitHub cron が混雑するため回避
  workflow_dispatch:

jobs:
  pull:
    uses: kanade0404/skills/.github/workflows/consumer-pull.yml@master
    with:
      app-id: ${{ vars.SKILLS_PULL_APP_ID }}
      runtime: bun
      current-ref-command: |
        r1=$(grep -oE '"ref": *"v[0-9]+\.[0-9]+\.[0-9]+"' rulesync.jsonc | head -n1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+')
        r2=$(grep -oE '"ref": *"v[0-9]+\.[0-9]+\.[0-9]+"' rulesync-claude/rulesync.jsonc | head -n1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+')
        if [ "$r1" = "$r2" ]; then echo "$r1"; else echo "refs-diverged"; fi
      update-command: |
        sed -i -E 's|"ref": *"v[0-9]+\.[0-9]+\.[0-9]+"|"ref": "'"$SKILLS_TAG"'"|' rulesync.jsonc rulesync-claude/rulesync.jsonc
        bun install --frozen-lockfile
        bun run rulesync:skills:update
        bun run rulesync:skills:claude:update
      pr-notes: |
        - [ ] `scripts/patch-rulesync-skill-frontmatter.ts` の削除可否を確認する。skills v0.5.0 以降は canonical 側が `claudecode:` target-section 形式になり generate が allowed-tools を保持するため、このパッチは不要になっている可能性が高い。生成差分で確認し、不要ならパッチと package.json の該当ステップを削除する commit をこの PR に追加する (確認できなければ削除せず、調査結果をコメントに残す)
    secrets:
      app-private-key: ${{ secrets.SKILLS_PULL_APP_PRIVATE_KEY }}
```

- [ ] **Step 2: actionlint で検証**

Run: `actionlint .github/workflows/skills-pull.yml`
Expected: 出力なし

- [ ] **Step 3: commit し、PR 作成 → CI green → 人間が merge**

commit 要約: `ci: skills リリースへの日次 pull 追随 workflow を追加`。dotfiles の CI 慣例に従う

---

### Task 6: エンドツーエンド検証 (3 ケース)

**Files:** なし (workflow_dispatch 実行と観察のみ)

**Interfaces:**
- Consumes: Task 4 / 5 の merge 済み wrapper、Task 3 の App 配線
- Produces: 検証結果の記録。**Task 7 のゲート** — 3 ケース全部が期待どおりでない限り Task 7 に進まない

- [ ] **Step 1: no-op ケース (agegis)**

Run: `gh workflow run skills-pull.yml -R kanade0404/agegis` → `gh run watch --exit-status -R kanade0404/agegis` で完了確認
Expected: run 成功。Gate step のログに「現 pin (v0.8.0) が最新タグと一致、スキップ (no-op)」(agegis は既に最新のため)。PR は作られない

- [ ] **Step 2: PR 作成ケース (dotfiles)**

Run: `gh workflow run skills-pull.yml -R kanade0404/dotfiles` → `gh run watch --exit-status -R kanade0404/dotfiles`
Expected: run 成功。`chore/skills-v0.8.0` ブランチで PR が 1 件でき、body に compare リンク (`compare/v0.6.0...v0.8.0`) とパッチ削除可否のチェックリストがある。author は `skills-release-pull[bot]`。**PR 上で dotfiles の CI が起動している** (App token の狙いどおり)

- [ ] **Step 3: 冪等ケース (dotfiles 再実行)**

Run: 再度 `gh workflow run skills-pull.yml -R kanade0404/dotfiles`
Expected: run 成功。Gate step のログに「open PR が既にあるためスキップ (冪等)」。PR は増えない

- [ ] **Step 4: dotfiles の追随 PR 自体をレビューして merge する (人間)**

パッチスクリプト削除可否のチェックリストを処理し、CI green を確認して merge。merge 後にもう一度 dispatch し、no-op になることを確認 (agegis と同じ経路の再確認)

---

### Task 7: skills 側の Devin 経路削除と文書更新 (Task 6 完了がゲート)

**Files:**
- Delete: `.github/workflows/release-propagate.yml`
- Delete: `.github/devin/consumer-update.md`
- Modify: `RELEASING.md` (手順 4)

**Interfaces:**
- Consumes: Task 6 の検証完了
- Produces: Devin 依存ゼロの skills リポ

- [ ] **Step 1: skills リポで新ブランチを切り、2 ファイルを削除**

```bash
git switch -c ci/remove-devin-propagate master
git rm .github/workflows/release-propagate.yml .github/devin/consumer-update.md
```

- [ ] **Step 2: RELEASING.md の手順 4 を置換**

現行:

```markdown
4. consumer に告知（pin 先を `@vX.Y.Z` に上げてもらう）。
```

置換後:

```markdown
4. 追随は consumer 側 (agegis / dotfiles) の `skills-pull.yml` (日次 cron) が新タグを
   検知して追随 PR を自動作成する。急ぐ場合は各 consumer リポで同 workflow を
   workflow_dispatch する。仕組みは
   [ADR 0001](docs/adr/0001-consumer-pull-release-propagation.md) を参照。
```

- [ ] **Step 3: commit・PR 作成・CI green・merge**

commit 要約: `ci: Devin push 型 propagate を削除 (pull 型へ移行完了)`。body に ADR 0001 と検証結果 (Task 6) をリンク

- [ ] **Step 4: 残骸の掃除 (人間)**

skills リポの Settings → Secrets から `DEVIN_API_KEY` / `DEVIN_ORG_ID` を削除

---

## Self-Review 済み確認事項

- spec の全要件 (タグ解決 / 冪等 / no-op 2 方式 / GitHub App / cron / MAJOR 警告 / pr-notes / 段階移行 / RELEASING.md / secrets 掃除) に対応するタスクがある
- Task 4 / 5 の `current-ref-command` / `update-command` は 2026-07-19 時点の実リポ内容 (agegis package.json / dotfiles rulesync.jsonc ×2) を gh api で確認して書いた。実行時に差異があれば実物に合わせて調整し、spec との乖離は PR に書く
- 検証 3 ケース (no-op / PR 作成 / 冪等) は現状の pin 状態 (agegis=v0.8.0=最新, dotfiles=v0.6.0=遅れ) をそのまま使い、擬似状態を作らない
