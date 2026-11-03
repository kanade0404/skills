# CodeRabbit の振る舞い

判定スクリプト `coderabbit_gate.py` が前提にする CodeRabbit の振る舞いを、出典と一緒にまとめる。
出典は、CodeRabbit の文書（docs.coderabbit.ai）と、kanade0404/skills の PR #186・#187・#188 を `gh api` で読んだ実データの 2 つに分かれる。
実データは 2026 年 11 月 3 日に取った。CodeRabbit の出力の形は予告なく変わるので、判定が合わなくなったらここから見直す。

## チェックの状態

CodeRabbit は head のコミットに `CodeRabbit` という名前のチェックを付ける。
GitHub と CodeRabbit Cloud の組み合わせでは、`reviews.review_progress` が既定の `true` なら check run になる。
`false` ならコミットステータスになる（[Status check](https://docs.coderabbit.ai/pr-reviews/status-check)）。
このリポジトリでは check run で、アプリの slug は `coderabbitai` だった（#186・#187・#188）。

文書が挙げる状態は次のとおりである（[Status check](https://docs.coderabbit.ai/pr-reviews/status-check)）。

| 状態 | GitHub での表示 | 必須チェックとしての扱い |
| --- | --- | --- |
| 待ち（Review queued、Review in progress） | pending | 終わるまで merge を止める |
| 完了（Finished） | success | 通る |
| 飛ばした（Review skipped、Review paused、No files to review など） | skipped | 通るが、レビューはしていない |
| レート制限（Review rate limited） | neutral | 通るが、レビューはしていない |
| 失敗（Review failed） | `reviews.fail_commit_status` が `true` のときだけ failure | 既定では通る |
| 取り消し（Review cancelled） | cancelled | 通らない |
| 報告なし | チェックが無い | 必須なら待ち続ける |

チェックが success でも skipped でも、それだけでは head をレビューしたかが分からない。
skipped と neutral は「通る」扱いなので、チェックの状態だけで判定すると、レビューされていない head を READY にしてしまう。

実データでは次の動きを見た。

- #187 の head `28568f0` では、CI が 00:01:11 に終わった。CodeRabbit のチェックは 00:00:21 に始まり、00:05:10 に終わった。CI が通ってから約 4 分後にレビューが届いた。
- #186 の head `7f76197` では、CI の終わり（06:42:48）の 6 秒後に CodeRabbit のチェックが skipped（Review paused）で付いた。
- #188 の head `d80d136` も skipped（Review paused）だった。

## CodeRabbit の見分け方

CodeRabbit のアカウントは、GraphQL では `coderabbitai`、REST では `coderabbitai[bot]` として返る（#186・#187・#188）。
判定スクリプトは、名前がこの 2 つのどちらかと文字単位で一致し、GraphQL の `__typename` が `Bot` のアカウントだけを CodeRabbit とみなす。
前方一致にすると、`coderabbitai-evil` のような別のアカウントがレビューを書いて判定を通せてしまう。
check run も、アプリの slug が `coderabbitai` で名前が `CodeRabbit` のものだけを見る。
`automerge-gate/all-passed` は、`github-actions` のアプリが出したものだけを使う。

## 自動の一時停止

`reviews.auto_review.auto_pause_after_reviewed_commits` の既定値は 5 とされている。
レビューしたコミットがこの数に達すると、CodeRabbit は自動の差分レビューを止める（[Automatic review](https://docs.coderabbit.ai/configuration/auto-review)）。
止まったあとは、`@coderabbitai resume` で自動レビューを戻すか、`@coderabbitai review` で 1 回だけレビューさせる（同ページ、[Status check](https://docs.coderabbit.ai/pr-reviews/status-check)）。

#186 と #188 は、どちらも 6 個目のコミットで止まり、head がレビューされないまま merge された。
#188 の要約コメントには次の目印と行があった。

```
<!-- This is an auto-generated comment: review paused by coderabbit.ai -->
- Last reviewed commit: 9d454a35fea854ebddd612c61a23fbfa62564160
- Latest commit, not reviewed: d80d1366f366cae37b197c231924525086aaaf3d
```

## head をレビューしたかの見分け方

次のどれかがあれば、head をレビューしたとみなす。

1. CodeRabbit のレビュー（`reviews` の 1 件）のうち、`commit.oid` が head と同じで、本文にレビュー本体の目印を持つもの。目印は次の 3 つで、どれか 1 つがあればよい。
   - `auto-generated comment by CodeRabbit for review status`
   - `coderabbit-review-publication`
   - `Actionable comments posted`
2. 要約コメント（`summarize by coderabbit.ai` を含むコメント）にある `final_review_risk_coverage` の目印。`kind` が `reviewed` で、`coveredCommitId` が head のものに限る。
3. 要約コメントの `<!-- recent_review_start -->` から `<!-- recent_review_end -->` のあいだにある「between X and Y」で、Y が head のもの。ただし、同じコメントにレビュー中かレート制限の目印があれば使わない。

1 の条件で本文を見るのは、スレッドへの返信もレビューとして記録されるためだ。
#188 では head `d80d136` を指すレビューが 2 件あった（5745954988 と 5745956129）。どちらも本文が空の返信で、head はレビューされていなかった。

指摘のないレビューは、レビューのオブジェクトを作らない。
#187 の head `28568f0` にはレビューが 1 件も無い。要約コメントに「No actionable comments were generated in the recent review.」と、head を指す `final_review_risk_coverage` だけがあった。
そのため、2 と 3 の要約コメントの目印も根拠にする。

CodeRabbit は 1 回のレビューを複数のレビューに分けて出すことがある。
分けたときは、本文の `coderabbit-review-publication` の目印に `batch=i/n` が付く。
同じ publication の 1 から n までがそろうまでは、出し終えていない。

## レビュー中の目印

CodeRabbit は、レビュー中に要約コメントへ `review in progress by coderabbit.ai` の目印を入れ、終わると消す。
この目印の文字列と、レート制限の通知の形は、[akin-oz/harrier の reviewfollowup.py](https://github.com/akin-oz/harrier/blob/main/services/api/src/harrier/reviewfollowup.py)が実際の PR から読み取った値に合わせた。
このリポジトリの PR では、レビュー中の要約コメントを取れていない。

## 差分レビューと新しい push

`auto_incremental_review` が既定の `true` なら、push のたびに新しい差分をレビューする（[Automatic review](https://docs.coderabbit.ai/configuration/auto-review)）。
`@coderabbitai review` も差分だけをレビューする。全体をやり直すときは `@coderabbitai full review` を使う（[Commands](https://docs.coderabbit.ai/guides/commands)）。
差分レビューは新しい変更だけを見るので、前のレビューの指摘を繰り返さない。
後のレビューに指摘が無いことは、その指摘が直った証拠にならない。

## スレッドにならない指摘

CodeRabbit の指摘には、スレッドにならず、レビュー本文にだけ載るものがある。
これは `reviewThreads` をいくら調べても見えない。

- #186 のレビュー 5726592079 は「Actionable comments posted: 2」と書き、続く `[!CAUTION]` の枠に「Outside diff range comments (1)」を載せた。この 1 件（`verify.sh:67`）にスレッドは無い。数字の 2 は行に付いたコメントだけを数えていた。
- #188 のレビュー 5745727805 は「## Follow-up Comments」の見出しの下に、Outside diff の印の付いた指摘を 5 件載せた。スレッドは 1 件も無い。
- #188 で本文の 5 件に答えたのは 1 件だけだった（コメント 6592729857 が 1 件目に対応）。残りの 4 件に答えないまま merge された。

文書が挙げる「Nitpick comments」「Duplicate comments」の枠は、このリポジトリの設定（review profile が CHILL）では出なかった。
判定スクリプトは、指摘の位置より前にある最後の見出しで種類を決める。この 2 つは見出しの文字列だけで扱っている。

指摘には、行に付いたものも本文のものも、`<!-- cr-comment:v1:<16 進の id> -->` の目印が付く。
本文の指摘から、行に付いたコメントにもある id を除いたものが、本文にだけある指摘になる。
id の無い古い形式で「Outside diff range comments (N)」のような見出しだけがあれば、レビュー単位で `review-<レビューの id>` として扱う。

CodeRabbit は、行に付いたコメントが直ったと見ると、本文の末尾に「✅ Addressed in commit dff1782」と書き足す（#188 のコメント 4449318038）。
スレッドは自分では解決しないので、解決は作者がする。

## 本文の指摘に答えたかの追い方

本文の指摘には、スレッドのような解決の状態が無い。
リポジトリに書き込めるアカウント（`authorAssociation` が OWNER、MEMBER、COLLABORATOR）のコメントに指摘の id があれば、その指摘に答えたとみなす。
公開リポジトリでは誰でもコメントできるので、外部の人のコメントでは答えたことにしない。
対象にするのは、PR のコメント、レビューの本文、スレッドの最初と最後のコメントだ。

「後のレビューに同じ指摘が無ければ直った」とはみなさない。
差分レビューは前の指摘を繰り返さないので、無いことは何も示さない。

## レート制限

レート制限にかかると、CodeRabbit は PR に通知のコメントを書き、チェックは「Review rate limited」で通る（[Rate limits](https://docs.coderabbit.ai/management/rate-limits)）。
レビューが行われなかったことは、このコメントで分かる。
待ち時間の決まりは無く、古いレビューが窓から外れるにつれて枠が戻る。
`@coderabbitai rate limit` で次に使える時刻を確かめ、枠が戻ったら `@coderabbitai review` を頼む。
通知の目印は `rate limited by coderabbit.ai` で、待ち時間は「Next review available in: **38 minutes**」のような行に書かれる（harrier の実装による）。

このリポジトリの要約コメントには、直近 7 日の利用から決まった枠が「10 reviews per hour」と書かれていた（#187）。

## draft と除外したパス

`reviews.auto_review.drafts` の既定は `false` で、draft の PR はレビューしない（[Automatic review](https://docs.coderabbit.ai/configuration/auto-review)）。
`@coderabbitai full review` は draft などの設定による省略を上書きする（[Status check](https://docs.coderabbit.ai/pr-reviews/status-check)）。

`reviews.path_filters` で除いたファイルはレビューしない。
このリポジトリの `.coderabbit.yaml` は、上流から写したスキルのディレクトリを除いている。
レビューする対象が無いと、チェックは「No files to review」で skipped になる。待っても何も来ないので、判定スクリプトはこれをレビュー済みと同じに扱う。

## automerge-gate との関係

automerge-gate は check run の結論のうち success、skipped、neutral を通すと見なす（[src/conclusion.ts](https://github.com/pkgdeps/automerge-gate/blob/v5.0.5/src/conclusion.ts)）。
そのため、CodeRabbit のチェックを集約に入れても、レビューされたことの保証にはならない。
このリポジトリの `.github/workflows/automerge-gate.yml` は CodeRabbit のチェックを `ignore-checks` で除き、レビューの確認はこのスキルが受け持つ。
