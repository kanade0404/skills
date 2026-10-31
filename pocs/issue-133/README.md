# #133 DO統合PoCの引継ぎ用コード

[issue #133](https://github.com/kanade0404/skills/issues/133) の作業用ディレクトリから、最新の作成・デプロイ一体化候補と依存ソースを抽出した公開用アーカイブ。新しい実装や実環境への適用は、このコミットでは行っていない。

## 状態

2026-10-24の実環境検証は、読取り・plan成功後に書込みAPIの403で停止した。一体化構成の成功、DO namespace生成、再開処理の実環境での完走は未確認。以前のnamespace未provisionエラー100406とは別の失敗であり、対策が実環境で有効だったとは扱わない。

非公開の適用と事後確認までは承認済み。公開・受付開始・無関係な変更は含まない。403の原因を権限と対象scopeから調査し、現状を再読取りしてから再開する。

## 収録内容

- `poc133-renew-private-20261024/terraform/`: authority versionの `deploy=true`、edge依存順序、元の証明書を保持する更新候補と12件のmock test。
- 同ディレクトリの `run.mjs`・`prepare.mjs`・`guard*`: 失敗後の再開手順と差分検査のソース。以下の非公開入力に依存するため、このcheckoutからそのまま実行できない。
- `poc133-live-do-preparation-20261011/`: authority、認証edge、隔離scope、ローカル結合テスト。
- 過去日付の各ディレクトリ: 上記の依存実装、認証・lease・再送ポリシー、テスト。
- `poc133-provider-workaround-20261017/provider-schema.patch`: Cloudflare provider v5.27.0のexports schemaにoptionalなcontainerを追加する4行修正。回帰テストは同ディレクトリの `build-tools/terraform-provider-cloudflare-5.27.0/poc-schema-test/exports_test.go`。
- `source-manifest.json`: 抽出・匿名化後のソース一覧とSHA-256。実環境のsource sealの代用にはならない。

authorityの暗黙deploymentは独立したTerraformリソースではない。最終no-opだけでは外部操作によるactive versionの変更を検出できないので、実APIのactive version・配分・namespaceを別途照合する。

## 公開用コピーと非公開原本の違い

実アカウント・zone ID、ホスト名、Worker名、checkpoint hashをダミー値へ置換した。buildと結合テストの依存importをnpmパッケージ名に変更し、ソース末尾の余分な空行を除いた。アサーションは維持している。元の作業ディレクトリは変更していない。

このアーカイブには、トークン、秘密鍵、実証明書・CSR、生のstate・plan、実環境ログ、canonical root、providerバイナリ、個人の絶対パスを含めない。現在の実環境を復旧するための完全なバックアップではない。

非公開原本では最新の `RESULTS.md`、`STATUS.md`、`source-seal.json`、`state-verification.json`、失敗後checkpoint、実環境の設定と鍵が必要。これらは元の作業者のローカル保存先から、非公開の経路で引き継ぐこと。公開issueには転載しない。

`guard.test.mjs` の16件は、非公開のplan・reference・state・CSR fixtureに依存するため、公開アーカイブの `npm test` には含めていない。fixture不足をテスト成功と扱わない。実環境runnerも、原本のcheckpointとsource sealがない状態でguardを外して実行しない。初回モードの再実行や古いstateへの巻戻しは禁止。

## 公開コピーのローカル検証

```sh
cd pocs/issue-133
npm ci
npm test
npm run build
EVIDENCE_DIR="$(mktemp -d)" npm run test:integration
```

結合テストはMiniflareのローカルruntimeを使う。実Cloudflareへの通信・デプロイはしない。固定した依存バージョンは元の検証環境に合わせている。

Terraform mock testはTerraform 1.16.5と修正版provider 5.27.0が必要。モジュールは `../dist/` のバンドルを参照するため、ビルド後に `poc133-live-do-preparation-20261011/dist/` の2ファイルを `poc133-renew-private-20261024/dist/` に配置する。providerは別途公式v5.27.0ソースに同梱パッチを適用してビルドし、一時CLI設定の `dev_overrides` でのみ指定する。公式providerと同一扱いにしたり、グローバル設定を書き換えたりしない。

この公開コピーの設定を既存canonical rootに上書きしない。実環境での再開は、非公開原本と最新stateを照合してから行う。

## 公開コピーでの再確認（2026-10-31）

| 検証 | 結果 |
| --- | --- |
| `npm test` | 186 passed / 0 failed / 0 skipped |
| `npm run build` | authority・edgeのbundle生成成功 |
| `npm run test:integration` | 18 passed / 0 failed / 0 skipped |
| Terraform 1.16.5 + PoC修正版providerの `terraform test -no-color` | 12 passed / 0 failed |
| JS/MJS構文チェック | 32ファイル成功 |
| staged diffのgitleaks検査 | 検出なし |

結合テストの初回は実行sandboxがlocalhost待受けを拒否して停止した。同じテストをlocalhost待受け可能な環境で実行し、18件の成功を確認した。テストのskipやassertionの緩和はしていない。

これらは公開コピーのローカル検証結果であり、実Cloudflareへの適用成功を示すものではない。非公開fixture依存のrenewal guard 16件と、providerのGo回帰テストはこの公開コピーでは再実行していない。
