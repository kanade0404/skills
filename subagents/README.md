# subagents/

rulesync 配布元の **subagents** feature 枠。

- consumer は `rulesync fetch kanade0404/skills@<tag> --features subagents` で取得し、
  `rulesync generate --targets claudecode,codexcli --simulate-subagents` で
  各ツール形式（Claude Code = Markdown+frontmatter / Codex = TOML）へ変換する。
- ここには **rulesync canonical 形式** の subagent 定義を 1 ファイル 1 エージェントで置く。
- `problem-solver.md`: Pólya の 4 段階法 (`skills/problem-solving/`) を、自己完結した
  一発解決型の問題に単発 dispatch するための subagent。
- `code-reviewer.md`: 差分を白紙の目でレビューし Critical / Important / Minor で
  findings を返す (`skills/code-review/`)。コードは修正しない。
- `test-reviewer.md`: テストコード専用レビュアー。Meszaros smells / Khorikov 4 属性 /
  AI 生成テストのアンチパターンを一貫適用する (`skills/test-review/`)。テストは書き換えない。
- `codebase-explorer.md`: 読み取り専用の探索係。file:line 参照付きの結論だけを返し、
  ファイル内容をダンプしない。breadth (medium / very thorough) を呼出側が指定する。
- `ci-log-analyst.md`: CI 失敗ログから root cause 仮説を立てて返す
  (`skills/ci-self-heal/`)。修正はしない (NO FIXES WITHOUT ROOT CAUSE)。

dotfiles `.claude-plugin/agents/` 等からの移行は今後も継続。

## ツール権限の方針

read-only 契約を**本文の禁止事項で担保しない** — ツールを渡さないことで構造的に成立させる。
`code-reviewer` / `test-reviewer` / `codebase-explorer` の `tools` は `Read` / `Grep` /
`Glob` のみで、`Bash` を持たない。書き込み・コード実行・ネットワークの手段がそもそも無いので、
「書き換えるな」という指示が破られる余地が構造的に無い。
(`problem-solver` は read-only 契約のエージェントではない — 実際に解いて直すのが役割なので
`Bash` / `Write` / `Edit` を持つ。)

`Bash` を allowlist / denylist で絞る方式 (本 repo の `Explore` エージェントが
`scripts/explore-readonly-guard.sh` で行っている方式) は採らない。`find -exec` /
`find -delete` / `rg --pre` / `git diff --output=` のようにメタ文字を使わずに任意コマンド実行や
ファイル書き込みへ到達できる経路が実測で確認されており、パスの制約も無いため
`cat ~/.ssh/id_rsa` のような読み出しも通る。フィルタを増やすより、渡さないほうが確実。

例外は `ci-log-analyst` のみ。CI ログ取得に `gh` が要るため `Bash` を持つ。ただし
`gh` は `gh pr comment` / `gh pr merge` / `gh run rerun` のような書き込み面を同じ binary に
併せ持つので、本文で read-only subcommand に限定する規律を明記してある
(`gh api` は `-f` / `-F` / `--raw-field` / `--input` を 1 つでも付けると `-X` 無しでも
POST になるため、field flag 無しの `gh api <path>` のみ許可、という形で明記)。加えて
(1) 呼出側がログを渡せば `Bash` を使わずに済む代替経路を契約に含め、(2) 配布先では
OS レベル sandbox (Claude Code の sandbox 機能) の併用を推奨する。プロンプト上の規律は
enforcement boundary ではない、というのが前提。sandbox 対象は `ci-log-analyst` 1 体では
足りない — `problem-solver` は実装のため `Bash` / `Write` / `Edit` を持つので、
blast radius としては両方を見ること。

`Bash` を持たない 3 件 (`code-reviewer` / `test-reviewer` / `codebase-explorer`) は
自分で差分を取得したり `git` を叩いたりできないため、必要な入力は**呼出側が渡す契約**に
なっている。ただし渡すものはエージェントごとに違う — dispatch チェックリストとして
使うときは取り違えないこと。

- **diff スコープ契約** (`code-reviewer` / `test-reviewer`): base ref / レビュー対象の
  パス (変更ファイル一覧 or テストファイルのパス) / diff 本文またはそのファイルパス。
  `code-reviewer` は diff もファイル一覧も無ければ `FAIL` を返し、`test-reviewer` は
  `NEEDS_DISCUSSION` を返す (推測でスコープを作らない契約)。flakiness レビューでは
  加えて断続的な失敗出力と再現頻度を渡す。
- **breadth 契約** (`codebase-explorer`): base ref も diff も不要。渡すのは探索の
  breadth (`medium` / `very thorough`) で、**未指定だと本文の既定により `medium` に
  縮む**。網羅探索させたい場合は `very thorough` を明示すること。

`model` / `effort` / `tools` は必ず `claudecode:` セクションの下に置く — トップレベルに
書くと rulesync の `fromRulesyncSubagent` が読まず、`.claude/agents/` へ転写されない。
