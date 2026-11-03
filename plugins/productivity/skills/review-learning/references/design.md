# 記録の形と判定の決まり

`review_learning.py` が前提にする形と決まりをまとめる。
しきい値の出典も書く。

## 記録

`harness/review-learnings.jsonl` に、1 行に 1 件の JSON を追記する。
同じ `id` の行が複数あれば、最後の行を使う。
古い行は書き換えず、削除しない。
取り込み直しても、中身が同じなら行は増えない。

| 項目 | 型 | 中身 |
| --- | --- | --- |
| `id` | 文字列 | CodeRabbit は `coderabbit:<cr-comment の id>`。id の無いスレッドは `coderabbit:comment-<コメントの id>`。ローカルは `local:<tool>:<ハッシュ>`、人は `human:<ハッシュ>` |
| `source` | 文字列 | `coderabbit`、`local:<tool>`、`human` |
| `repo`、`pr` | 文字列、整数 | `owner/repo` と PR の番号 |
| `head_sha` | 文字列 | 指摘が付いたコミット |
| `path`、`line` | 文字列、整数か null | 指摘の場所 |
| `class` | 文字列か null | 種類。英小文字とハイフン |
| `claim` | 文字列 | 指摘の題名の抜き出し。信頼しないデータ |
| `outcome` | 文字列か null | `fixed`、`declined`、`already-fixed`。決まらなければ null |
| `fix_commit` | 文字列か null | 直したコミット |
| `coderabbit_reviewed_head` | 真偽か null | ローカルの指摘だけ。CodeRabbit が `head_sha` をレビューしたか |
| `missed_by_coderabbit` | 真偽か null | CodeRabbit の指摘は false。ローカルの指摘は下の判定による |
| `note` | 文字列 | 重さで昇格させた理由などの補足 |
| `recorded_at` | 文字列 | 追記した日（UTC） |

ADR（`docs/adr/2026-11-03-product-harness-phase0.md`）の決定 5 は、学びの記録を `harness/learnings.md` に置くとした。
決定 5 はハーネスの実行の失敗を扱い、この記録はレビューの指摘を扱う。
レビューの指摘は機械で数えて昇格させるので、同じ `harness/` の下に JSON Lines で置いた。

## 結果の決め方

CodeRabbit の指摘の結果は、次の順で決める。

1. CodeRabbit が行の指摘の本文に「Addressed in commit <SHA>」と書き足していれば、`fixed` とする。
2. それが無ければ、書き込める人（`authorAssociation` が OWNER、MEMBER、COLLABORATOR）の返信を読む。本文の指摘は、その id から次の id までを読む。
   - 「取り込まない」「見送」「対応しない」「won't fix」「declin」を含めば `declined`
   - 「対応済み」「既に」「すでに」「already」を含めば `already-fixed`
   - 数字を含む 7〜40 桁の 16 進があれば、それを SHA として `fixed`
3. どれにも当たらなければ null にする。

一度決まった結果は、取り込み直しでは変えない。
変えるときは `amend` を使う。
書き込める人以外の返信は読まない。
公開リポジトリでは誰でもコメントでき、結果を書き換えられるからだ。

## ローカルの指摘の入力

`capture-local` の `--input` には、次の形の JSON を渡す。
1 回の実行で 1 つのツールと 1 つの head の指摘を渡す。

```json
{
  "version": 1,
  "tool": "security-review",
  "head_sha": "<レビューしたコミットの 40 桁の SHA>",
  "findings": [
    {
      "path": "plugins/productivity/scripts/coderabbit_gate.py",
      "line": 168,
      "class": "prefix-match-identity",
      "claim": "指摘の要約",
      "outcome": "fixed",
      "fix_commit": "<直したコミットの SHA>",
      "note": "重さで昇格させるなら、その理由"
    }
  ]
}
```

- `tool` は英小文字、数字、ハイフンで 40 文字まで。`code-review`、`security-review`、`security-guidance`、`claude-review` を使う。人の指摘は `human` にする
- `path` と `claim` は必須
- `line` は正の整数か null
- `class` は英小文字とハイフンか null。null なら、`harness/review-rules.json` の `patterns` で `claim` から決める
- `outcome` は `fixed`、`declined`、`already-fixed` か null
- `fix_commit` は 7〜40 桁の SHA か null

形が合わなければ、何も取り込まずに終了コード 1 で止まる。
この検査は、GitHub へ問い合わせる前にする。

## 見逃しの判定

ローカルの指摘を CodeRabbit が見逃したとみなすのは、次をすべて満たすときだけにする。

1. CodeRabbit が、その指摘の `head_sha` をレビューした。根拠は `coderabbit_gate.head_evidence` と同じで、そのコミットを指すレビュー本体か、要約コメントの目印を使う。
2. 指摘に種類が付いている。
3. 同じ PR の CodeRabbit の指摘に、同じパス、同じ種類で、行の差が 10 行以内のものが無い。

1 か 2 を満たさなければ、見逃しかは分からないので null にする。
どちらかの行が分からなければ、同じ箇所とみなす。
種類を直したときと、CodeRabbit の指摘を取り込み直したときは、見逃しを判定し直して追記する。

この判定には限界がある。

- CodeRabbit が別のコミットで同じ指摘をしていても、行が 10 行を超えてずれていれば、見逃しとみなす。
- head より前のコミットだけにあった間違いは、CodeRabbit がそのコミットをレビューしていなければ null になる。#190 の前方一致は、PR を開く前の 4d7fe4c で直したので、CodeRabbit は一度も見ていない。
- 種類は人かエージェントが付けるので、付け方がぶれると一致しない。
- 要約コメントの目印は最新のレビューしか指さないので、古いコミットは、そのコミットを指すレビュー本体が無いと null になる。

## 上限

| 対象 | 上限 | CodeRabbit の上限 |
| --- | --- | --- |
| `path_instructions` の件数 | 5 | 決まっていない |
| `path_instructions` の 1 件の長さ | 1500 文字 | 20000 文字 |
| `custom_checks` の件数 | 3 | 決まっていない |
| `custom_checks` の 1 件の長さ | 2000 文字 | 10000 文字 |
| `custom_checks` の名前 | 50 文字 | 50 文字 |

指示は増えるほど守られにくくなる。
上限は CodeRabbit のものより低くし、上限に当たったら古い規則を削るか、上の段に移す。

## しきい値の出典

- 昇格は 2 件から。pstack の `correct` は「2 回起きたら種類として扱う」とする。agentpatterns.ai は 3 件以上を勧め、1〜2 件での昇格は仮説を固めると警告する（Review-Feedback-to-Rule Loop の When this backfires）。ここでは CodeRabbit がまた同じ指摘をする前に止めたいので 2 件にし、見送りの割合で外れた規則を見つける。
- 見送りの割合の 10% は、Google の Tricorder が解析を止める線（Sadowski 2018）に合わせた。
- 規則を差分の時点で動かすのは、差分の時点の指摘が 70% 直り、まとめた一覧が 0% だった報告（Distefano 2019）による。

## ast-grep の規則の置き場所

CodeRabbit の文書は、ast-grep のパッケージについて `rules` と `utils` を予約された名前としている。
取り違えを避けるため、規則は `harness/ast-grep/review-rules/` に置いた。
CodeRabbit は v0.50.0 の ast-grep を動かすので、CI でも `pyproject.toml` で同じ版に固定した。
CI は Semgrep も動かすが、CodeRabbit では Semgrep を動かさないので、両方で同じに効くのは ast-grep の規則になる。

## CodeRabbit が読む指示ファイル

CodeRabbit の `knowledge_base.code_guidelines` は、`CLAUDE.md`、`AGENTS.md`、`.claude/rules/**/*.md` などを既定で読み、外す設定は無い。
このリポジトリは、これらをリポジトリに入れていない。
手元の `.claude/rules/` は git の管理の外なので、CodeRabbit には届かない。
規則は、指示ファイルではなく `harness/review-rules.json` から `.coderabbit.yaml` に写す。
