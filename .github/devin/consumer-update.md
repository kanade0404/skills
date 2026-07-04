# Consumer 更新タスク (rulesync pin 追随)

kanade0404/skills の新しいリリースタグが発行された。上に記載されたタグに、以下の consumer リポジトリの pin / 取得内容を追随させ、**各リポ 1 つの PR** を作成すること。PR の merge はしない (人間が行う)。

## 共通ルール

- 変更は feature ブランチ (`chore/skills-<tag>` 等) で行い、default ブランチに直 push しない。
- 生成物 (`.claude/`, `.codex/`, `.agents/` 等) は必ずリポ既存のスクリプトで再生成する。手で編集しない。
- CI がある場合は green を確認してから PR を ready にする。落ちたら root cause を特定して直す。直せない場合は PR コメントに原因分析を書いて escalate する。
- PR body に「何を・なぜ (新タグへの追随)・生成し直したもの」を記載し、リリースタグの diff リンク (https://github.com/kanade0404/skills/compare/<旧>...<新>) を貼る。

## 対象 1: kanade0404/agegis

- `package.json` の `rulesync:fetch` script が commit SHA で pin されている (`kanade0404/skills@<sha>`)。これを新タグ (`kanade0404/skills@<tag>`) に書き換える。
- `rulesync:fetch` を実行して `.rulesync/` を更新し、リポ既存の generate 手順 (package.json の rulesync 系 script を確認) で各ツール設定を再生成する。
- fetch/generate で意図しない大量差分が出た場合は、差分の内訳を PR body で説明する。

## 対象 2: kanade0404/dotfiles

- `rulesync.jsonc` は declarative source (git URL) 方式。`rulesync:skills:update` と `rulesync:skills:claude:update` (package.json 参照) を実行して最新タグ内容に更新・再生成する。
- **追加確認**: `scripts/patch-rulesync-skill-frontmatter.ts` の役割を読むこと。これが「rulesync generate で落ちる allowed-tools を後付け補正する」ためのパッチである場合、skills v0.5.0 以降は canonical 側が `claudecode:` target-section 形式になり generate が allowed-tools を保持するため、**パッチが不要になっている可能性が高い**。不要と確認できたらパッチスクリプトと package.json の該当ステップを削除して簡素化する (確認できない場合は削除せず、PR コメントに調査結果を書く)。

## 完了報告

作成した PR の URL 一覧と、各リポで行った判断 (特に dotfiles のパッチ削除可否) を報告すること。
