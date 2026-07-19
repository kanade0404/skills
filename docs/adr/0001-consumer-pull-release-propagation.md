# リリース追随を push 型 (Devin) から consumer pull 型に転換する

Status: accepted (2026-07-19)

skills のリリースタグを consumer リポ (agegis / dotfiles) に追随させる仕組みは、当初
skills 側の tag push を契機に Devin API へ session を作成する push 型だった。Devin の
クレジット枯渇で propagate が全停止する単一依存があり、かつ実作業 (pin 更新 →
rulesync fetch/generate → PR 作成) はほぼ機械的で AI 実行の必然性が無かった。そこで
各 consumer が日次 cron で新タグを検知して自リポに追随 PR を作る **pull 型**
(AI を使わない決定的スクリプト。共通ロジックは skills が reusable workflow
`consumer-pull.yml` として配布し consumer は薄い wrapper のみ) に置き換える。

## Considered Options

- **Devin 失敗時に Claude Code Action へ fallback** — 実行主体が変わるだけで
  credit / rate limit 依存は残るため却下。
- **issue 起票して人間へ fallback** — 作業が機械的で人手に落とす理由が無いため却下。
- **Renovate custom manager + 再生成 workflow** — pin bump と rulesync 再生成の
  2 段構えになり、hosted Renovate は任意コマンドを実行できないため却下。
- **repository_dispatch (tag push 起点で consumer 実行)** — cross-repo token の管理が
  残り純粋な pull にならず、日次で足りる遅延要件に対して過剰なため却下。

## Consequences

- Devin credit / 外部 AI サービスへの依存が構成から消える (fallback 問題自体が消滅)。
- PR 作成には CI が起動するよう GitHub App (無期限) の installation token を使う。
  fine-grained PAT の年次失効チョアを避けられる代わりに、App という管理対象が 1 つ増える。
- 追随の最大遅延は cron 間隔 (日次)。急ぐ場合は consumer 側の workflow_dispatch。
- 機械判断できない作業 (例: dotfiles のパッチスクリプト削除可否) は自動化から
  外れ、PR body の「要人間確認」チェックリストとして人間に残る。

詳細設計: [docs/superpowers/specs/2026-07-05-consumer-pull-propagation-design.md](../superpowers/specs/2026-07-05-consumer-pull-propagation-design.md)
