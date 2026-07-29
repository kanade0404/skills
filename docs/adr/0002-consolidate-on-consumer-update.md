# リリース追随を稼働系 consumer-update.yml に一本化する

Status: accepted (2026-07-28) / supersedes [0001](0001-consumer-pull-release-propagation.md)

ADR 0001 の設計 (reusable workflow `consumer-pull.yml`、GitHub App token、`@master` 参照) を
PR #84 で実装・merge した時点で、**同機能の並行実装が既に稼働していた**ことが判明した:
別トラック (v0.9.0、2026-07-11 頃) で `consumer-update.yml` (GITHUB_TOKEN + 任意 PAT 方式、
consumer からは SHA pin + Renovate 追随) が導入され、agegis / dotfiles 両 consumer の
wrapper (`skills-update.yml`) が配線・運用済みで、Devin 経路の削除も完了していた。
原因は PR #84 の作業が 2026-07-05 時点の stale な worktree 基点で行われ、実装開始時に
リモート master を再確認しなかったこと。同機能の reusable workflow が 2 本並立し、
ドキュメントが実態と乖離したため、**稼働実績のある `consumer-update.yml` に一本化し、
`consumer-pull.yml` を削除する** (agegis への重複 wrapper PR #277 も close 済み)。

## Considered Options

- **consumer-pull.yml (App 方式) へ移行し incumbent を置換** — PR 上で CI が自動起動する・
  write token を consumer コマンドから分離できる利点はあるが、稼働中システムの置換コストと
  GitHub App という管理対象の追加が、限界的な利得に見合わないため却下。
- **両方を残す** — 用途が同一で保守が二重になり、将来の読者を確実に混乱させるため却下。

## Consequences

- token モデルは GITHUB_TOKEN (+ 必要時 `pr_token` PAT) 方式になる。PR 上で CI を回したい
  consumer は PAT を渡す (RELEASING.md に記載済み)。
- consumer からの参照は `@master` でなく **SHA pin + Renovate bump** (0001 の `@master` 決定の
  逆転)。write 権限を渡す caller では branch 参照だと参照先の将来の変更がレビューなしで
  write 権限実行される、という incumbent 側の論拠を採用する。
- `consumer-pull.yml` 固有の機能 (App token による CI 起動、branch 存在確認の 404 判別、
  MAJOR bump 警告、pin 食い違い検知) は必要になった時点で `consumer-update.yml` へ
  個別に移植する。実装は git 履歴 (PR #84) に残る。
- issue #90 (update-command と権限付き push/PR の job 分離) は `consumer-update.yml` にも
  同様に当てはまるため、対象を読み替えて存続する。
- ADR 0001 に付随する設計 spec / 実装計画 (docs/superpowers/) は歴史的記録として残すが、
  冒頭に superseded 注記を付す。
