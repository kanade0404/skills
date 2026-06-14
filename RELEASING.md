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
4. consumer に告知（pin 先を `@vX.Y.Z` に上げてもらう）。

## まだやっていないこと

- タグ push の自動化（GitHub Actions 等）は未対応。手動でタグを切る。
