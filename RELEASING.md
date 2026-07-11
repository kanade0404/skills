# RELEASING

このリポジトリのリリースは **git タグ `vX.Y.Z`（semver）だけ**。ビルド成果物は無い。
consumer は `rulesync fetch kanade0404/skills@vX.Y.Z --features skills,...` でそのタグの
`skills/<name>/` を固定取得する。

## バージョンの上げ方

| 種別 | いつ上げるか |
|---|---|
| MAJOR | 互換が壊れる変更: skill の削除 / リネーム（ディレクトリ名 = `name` の変更）/ 既存 skill の挙動契約の変更 |
| MINOR | 後方互換のある追加: 新規 skill、既存 skill の機能追加 |
| PATCH | 挙動を変えない修正: description / trigger 調整、本文の言い回し、typo、references 追記 |

## 手順

1. `master` が clean で、リリースに含めたい変更が全て merge 済みであることを確認する。
2. 次バージョンを決める（上表）。直近タグは `git tag -l --sort=-v:refname | head -1` で確認。
3. 注釈付きタグを切る:
   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z: <要約>"
   git push origin vX.Y.Z
   ```
4. consumer への伝播は **pull 型**。各 consumer リポジトリが
   `.github/workflows/consumer-update.yml`（本リポジトリの reusable workflow）を
   schedule で呼び出し、新タグを検知すると pin 更新 + 再生成の PR を自動作成する
   （merge は人間）。急ぐ場合は consumer 側で workflow_dispatch を手動実行する。
   **master に merge しただけでは consumer に届かない** — installed skill
   (`~/.claude/skills` 等の fetch 済みコピー) はタグ更新までは古い版のまま
   固定されている。タグを切り、consumer 側で `rulesync fetch` が走って初めて
   反映される。

## consumer 側の配線

caller は以下の形（`update_command` は各リポの流儀で pin 書き換え〜再生成まで行う。
最新タグは環境変数 `SKILLS_TAG` で渡される）:

```yaml
name: skills update
on:
  schedule:
    - cron: '17 21 * * *'
  workflow_dispatch:
permissions:
  contents: write
  pull-requests: write
jobs:
  update:
    # write 権限を渡す reusable は SHA pin が必須 (下記の注意を参照)
    uses: kanade0404/skills/.github/workflows/consumer-update.yml@<commit-SHA> # vX.Y.Z
    with:
      update_command: |
        <pin 書き換え + fetch + generate>
```

注意:

- **参照は `@master` でなく commit SHA に pin する**。この caller は
  `contents: write` / `pull-requests: write` を渡すため、branch 参照だと
  参照先の将来の変更が consumer 側のレビューを経ずに write 権限で実行される
  (GitHub 公式も "Using the commit SHA is the safest option" としている)。
  追随は Renovate 等の github-actions manager による bump PR で行う。
  read 権限しか渡さない caller (例: 計測送信) は @master 追随でもよい。
- reusable workflow は caller token の権限を **downgrade しかできない**ため、
  caller 側で `contents: write` / `pull-requests: write` を明示する。
- repo 設定 "Allow GitHub Actions to create and approve pull requests" を有効にする。
- GITHUB_TOKEN が作る PR は CI をトリガしない。PR 上で CI を回したい consumer は
  `secrets: pr_token` に fine-grained PAT を渡す。

## まだやっていないこと

- タグ push の自動化（GitHub Actions 等）は未対応。手動でタグを切る。
