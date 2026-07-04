---
name: pr-conflict-resolver
description: |
  GitHub PR で発生した merge conflict を自動修正するための手順とベストプラクティス。
  PR コメントで `@claude` 経由で呼ばれた conflict 解決タスクや、ローカルでの rebase / merge
  conflict を解決するときに必ず参照する。チェックアウト → merge / rebase → conflict 解決 →
  lock ファイル再生成 → テスト → push までの一連の安全な流れを定義する。
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Glob
  - Grep
---
# PR conflict resolver

PR のターゲットブランチ (base) と head ブランチの間で発生した merge conflict を、機能を欠落させず
安全に解決するための手順。Claude Code Action から呼び出される前提で、すべて非対話で完結すること。

## 0. 前提情報の確認

呼び出し元のコメントから以下を取得する:

- **PR 番号** (`#N`)
- **head ブランチ** (作業対象)
- **base ブランチ** (merge 元)
- 解決方針の明示的な指示があれば優先する (例: 「lock ファイルは theirs を採用」)

不明な場合は `gh pr view <N> --json number,headRefName,baseRefName,title` で取得する。

## 1. ブランチをチェックアウト

```bash
git fetch origin "$PR_BASE" "$PR_HEAD"
git checkout "$PR_HEAD"
git pull --ff-only origin "$PR_HEAD"
```

`--ff-only` は他者の push を上書きしないための安全策。fast-forward できないときは
push が走った可能性があるので一度 `git status` で状態を確認し、想定外であれば停止する。

## 2. base を merge

rebase ではなく **merge** を既定とする。理由:

- PR の commit 履歴と署名 (Co-Authored-By 等) を壊さない
- PR がレビュー中の場合、force-push は差分を見失わせるため避ける

```bash
merge_status=0
git merge --no-ff --no-edit "origin/$PR_BASE" || merge_status=$?
if [ "$merge_status" -ne 0 ]; then
  if git diff --name-only --diff-filter=U | grep -q .; then
    echo "merge conflict を検知。解決ステップへ進行。"
  else
    echo "merge が conflict 以外の理由で失敗。処理を中断。" >&2
    exit "$merge_status"
  fi
fi
```

`|| true` で全エラーを握り潰すと、認証エラーや壊れた ref 等の異常時にも誤って
解決処理へ進んでしまう。上記のように **conflict (未マージファイルあり) のときだけ継続**し、
それ以外の非ゼロ終了は中断する。特別な指示で rebase を希望される場合のみ
`git rebase origin/$PR_BASE` を選択する。

## 3. conflict 箇所の特定

```bash
git diff --name-only --diff-filter=U
```

ファイル種別別に処理戦略を切り替える:

| ファイル種別 | 戦略 |
|------------|------|
| ソースコード | 後述「ソース conflict 解決ガイドライン」に従い手で解決 |
| lock ファイル (`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `bun.lock`, `Cargo.lock`, `Gemfile.lock`, `poetry.lock`, `uv.lock`, `go.sum`) | conflict マーカーごと削除し、対応するパッケージマネージャで再生成 |
| 生成物 (`dist/`, `build/`, `*.min.js`, snapshot) | `.gitignore` 漏れがないか確認した上で、可能ならビルドし直して上書き |
| バイナリ | `git checkout --theirs` または `--ours` で base を採用するのが基本。理由をコメントで残す |

### ソース conflict 解決ガイドライン

1. **両側の意図を読む**: `git log --merge -p <file>` で双方の commit 履歴と差分を確認
2. **関数/ブロック単位で統合**: 片側削除を安易に選ばない。同じ箇所への重複編集は **両方の機能を残す**
3. **import / 型定義 / API シグネチャ** は両側の変更を合算
4. **テストファイル** は両側のケースを残し、重複は名前を変えて両立させる
5. 解決後、conflict マーカー (`<<<<<<<`, `=======`, `>>>>>>>`) が残っていないことを `grep -rE '^<<<<<<<|^=======|^>>>>>>>' .` で確認

判断が難しい場合 (相反する仕様変更、両方残すと壊れるロジック等) は **無理に解決せず**、
4 章「無理しない撤退判断」に従って PR にコメントしてエスカレーションする。

### lock ファイルの再生成

各エコシステムごとに対応コマンドが異なる:

```bash
# package-lock.json
rm -f package-lock.json && npm install --package-lock-only --ignore-scripts

# pnpm
rm -f pnpm-lock.yaml && pnpm install --lockfile-only --ignore-scripts

# yarn (berry / classic で挙動が違うので存在するコマンドを使う)
rm -f yarn.lock && yarn install --mode update-lockfile 2>/dev/null || yarn install --ignore-scripts

# bun
rm -f bun.lock bun.lockb && bun install --frozen-lockfile=false --ignore-scripts

# Cargo
cargo update --workspace --offline 2>/dev/null || cargo generate-lockfile

# uv
uv lock

# poetry
poetry lock --no-update

# go
go mod tidy
```

`--ignore-scripts` を付けるのは untrusted な postinstall を防ぐため。
リポジトリ固有の `package.json` scripts に必要な処理があれば、CI 上で別途実行されるのでここでは省略してよい。

## 4. 検証

push 前に最低限以下を実行し、conflict 解決の副作用がないことを確認する。

```bash
# 静的検証 (リポジトリにある場合のみ)
[ -f package.json ] && npx --no-install tsc --noEmit 2>/dev/null || true
[ -f package.json ] && npm run lint --if-present
[ -f package.json ] && npm test --if-present -- --run 2>/dev/null || npm test --if-present
[ -f Makefile ] && grep -qE '^test:' Makefile && make test
```

タイムアウトが発生したり、テスト基盤の起動に外部依存がある場合は **無理に通そうとせず**、
PR コメントでその旨を報告して push に進む。CI 側で再走するため二重に時間を使わない。

## 5. push と報告

```bash
git push origin "$PR_HEAD"
```

force-push (`--force-with-lease` も含む) は merge コミットを使う限り不要。
push 後、PR に以下フォーマットで結果コメントを残す:

```markdown
### conflict 解決完了

- 解決方針: <merge / rebase>
- 解決したファイル:
  - `path/to/file.ts` — <統合内容を 1 行で>
  - `package-lock.json` — 再生成
- 検証結果:
  - tsc: ✅
  - lint: ⏭ (CI に委譲)
  - test: ⚠ <一部 skip した場合の理由>
- 残課題: <あれば箇条書き / なければ「なし」>

push: `<commit-sha>`
```

## 無理しない撤退判断

次の条件のいずれかに該当するときは **conflict を解決せず**、PR に方針をコメントして撤退する:

1. 機能仕様レベルで相反する変更 (双方の機能を両立させると要件矛盾になる)
2. 同一テストケースに対する期待値が両側で食い違う
3. 巨大な自動生成物 (e.g. protobuf, GraphQL schema, OpenAPI) の手解決が現実的でない
4. `secrets` / 認証情報 / migration 順序など、誤った解決が破壊的になるもの
5. 5 回以上修正を試みても CI / 型チェックが通らない

撤退コメントには以下を含める:

- conflict が起きているファイルと衝突箇所の概要
- 両側の変更の意図 (commit log を参照した推定で可)
- 推奨される解決方針 (どちらを採用すべきか、または人手で merge すべき理由)
- 自動解決を断念した理由

## チェックリスト (push 前)

- [ ] `git diff --name-only --diff-filter=U` が空である (= 全 conflict 解決済み)
- [ ] `grep -rE '^<<<<<<<|^=======|^>>>>>>>' .` で残マーカーなし
- [ ] lock ファイルは整合性が取れている
- [ ] 型 / lint / 単体テストが通る (もしくは通らない理由を報告予定)
- [ ] commit メッセージは `.gitmessage` の絵文字プレフィックス規約に従っている
  - merge commit の場合は既定メッセージで可
  - 追加修正の commit は `:recycle: Resolve conflicts with <base>` などを使う
- [ ] secrets / 認証情報を含むファイルを誤って追加していない
