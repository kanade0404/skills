"""pr-review-respond の `prr fetch` 正規化 (normalize_fetch.jq) を fixture で検証する.

背景 (2026-10-03): 「指摘確認して」に対し行コメント (reviewThreads) しか読まず、
CodeRabbit がレビュー本文の折りたたみ <details> に書いた "Outside diff range" 指摘を
取りこぼした。fetch_threads.sh は reviewThreads と issues/{n}/comments だけを
取得しており、pulls/{n}/reviews のレビュー本文が丸ごと欠落していた。

本テストは GitHub を叩かず、API ペイロード相当の fixture を normalize_fetch.jq に
通して次を固定する:
- review_bodies がレビュー本文全文を保持する (空 body / PENDING は除外)
- CodeRabbit の Outside diff range / Nitpick セクションが embedded_findings に
  ファイル・行範囲・本文単位で分解される (Review details 等は分解しない)
- 指摘内にネストした <details> (Prompt for AI Agents 等) でセクション追跡が壊れない
- issue_comments は body 全文を保持する
- counts がソース別件数を返す
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = REPO_ROOT / "skills" / "pr-review-respond" / "scripts" / "normalize_fetch.jq"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pr_review_respond"

# (A) 旧レイアウト: カテゴリ <details> > ファイル <details> > "`range`: **Title**"
CODERABBIT_BODY = (FIXTURES / "coderabbit_review_body.md").read_text(encoding="utf-8")
# (B) 現行レイアウト: "> [!CAUTION]" callout 内の太字見出し > 指摘ごとの <details>
CODERABBIT_CALLOUT_BODY = (FIXTURES / "coderabbit_review_body_callout.md").read_text(encoding="utf-8")
CODERABBIT_SUMMARY = (
    "<!-- walkthrough_start -->\n## Walkthrough\n\nAdds retry.\n\n"
    "<details>\n<summary>📝 Actionable</summary>\n\nCheck `handler.ts` null guard.\n\n</details>\n"
)

META = {"number": 7, "title": "t", "url": "https://github.com/o/r/pull/7", "head_oid": "def456", "base": "main"}
THREADS = [
    {
        "id": "PRRT_1",
        "isResolved": False,
        "isOutdated": False,
        "comments": {
            "nodes": [
                {
                    "databaseId": 101,
                    "body": "Devin: null check missing",
                    "path": "src/payment/handler.ts",
                    "line": 10,
                    "startLine": None,
                    "originalLine": 10,
                    "url": "https://github.com/o/r/pull/7#discussion_r101",
                    "createdAt": "2026-10-01T00:00:00Z",
                    "author": {"login": "devin-ai-integration"},
                },
                {
                    "databaseId": 102,
                    "body": "Fixed in abc",
                    "path": "src/payment/handler.ts",
                    "line": 10,
                    "startLine": None,
                    "originalLine": 10,
                    "url": "https://github.com/o/r/pull/7#discussion_r102",
                    "createdAt": "2026-10-01T01:00:00Z",
                    "author": {"login": "me"},
                },
            ]
        },
    },
    {
        "id": "PRRT_2",
        "isResolved": True,
        "isOutdated": False,
        "comments": {
            "nodes": [
                {
                    "databaseId": 201,
                    "body": "old",
                    "path": "a.ts",
                    "line": 1,
                    "startLine": None,
                    "originalLine": 1,
                    "url": "u",
                    "createdAt": "2026-09-30T00:00:00Z",
                    "author": {"login": "coderabbitai"},
                }
            ]
        },
    },
    # 未解決・最新・未返信 → Phase A の triage 対象 (eligible)
    {
        "id": "PRRT_3",
        "isResolved": False,
        "isOutdated": False,
        "comments": {
            "nodes": [
                {
                    "databaseId": 301,
                    "body": "Devin: race on retry counter",
                    "path": "src/payment/retry.ts",
                    "line": 40,
                    "startLine": None,
                    "originalLine": 40,
                    "url": "https://github.com/o/r/pull/7#discussion_r301",
                    "createdAt": "2026-10-02T00:00:00Z",
                    "author": {"login": "devin-ai-integration"},
                },
                # PR 作者以外の追記は self_replied にしない
                {
                    "databaseId": 302,
                    "body": "+1",
                    "path": "src/payment/retry.ts",
                    "line": 40,
                    "startLine": None,
                    "originalLine": 40,
                    "url": "https://github.com/o/r/pull/7#discussion_r302",
                    "createdAt": "2026-10-02T01:00:00Z",
                    "author": {"login": "someone-else"},
                },
            ]
        },
    },
    # 未解決だが outdated → Phase A で skip (未解決数には入るが最終 gate の分母には入らない)
    {
        "id": "PRRT_4",
        "isResolved": False,
        "isOutdated": True,
        "comments": {
            "nodes": [
                {
                    "databaseId": 401,
                    "body": "old line",
                    "path": "src/payment/client.ts",
                    "line": None,
                    "startLine": None,
                    "originalLine": 3,
                    "url": "https://github.com/o/r/pull/7#discussion_r401",
                    "createdAt": "2026-09-29T00:00:00Z",
                    "author": {"login": "coderabbitai"},
                }
            ]
        },
    },
]
REVIEWS = [
    {
        "id": 9001,
        "user": {"login": "coderabbitai[bot]"},
        "state": "COMMENTED",
        "body": CODERABBIT_BODY,
        "submitted_at": "2026-10-02T00:00:00Z",
        "html_url": "https://github.com/o/r/pull/7#pullrequestreview-9001",
        "commit_id": "def456",
    },
    {
        "id": 9002,
        "user": {"login": "devin-ai-integration[bot]"},
        "state": "COMMENTED",
        "body": "Found 1 issue outside the diff: `config.ts` default timeout is 0.",
        "submitted_at": "2026-10-02T01:00:00Z",
        "html_url": "https://github.com/o/r/pull/7#pullrequestreview-9002",
        "commit_id": "def456",
    },
    {
        "id": 9005,
        "user": {"login": "coderabbitai[bot]"},
        "state": "COMMENTED",
        "body": CODERABBIT_CALLOUT_BODY,
        "submitted_at": "2026-10-02T03:00:00Z",
        "html_url": "https://github.com/o/r/pull/7#pullrequestreview-9005",
        "commit_id": "def456",
    },
    # 行コメントだけを束ねたレビュー (本文空) は review_bodies に出さない
    {
        "id": 9003,
        "user": {"login": "devin-ai-integration[bot]"},
        "state": "COMMENTED",
        "body": "   \n",
        "submitted_at": "2026-10-02T02:00:00Z",
        "html_url": "x",
        "commit_id": "def456",
    },
    # 未提出の自分の下書きは他者に見えないので対象外
    {
        "id": 9004,
        "user": {"login": "me"},
        "state": "PENDING",
        "body": "draft",
        "submitted_at": None,
        "html_url": "y",
        "commit_id": "def456",
    },
]
ISSUE_COMMENTS = [
    {
        "id": 5001,
        "user": {"login": "coderabbitai[bot]"},
        "body": CODERABBIT_SUMMARY,
        "html_url": "https://github.com/o/r/pull/7#issuecomment-5001",
        "created_at": "2026-10-02T00:00:00Z",
    }
]


@unittest.skipUnless(shutil.which("jq"), "jq is required")
class TestNormalizeFetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for name, value in {
                "meta": META,
                "threads": THREADS,
                "reviews": REVIEWS,
                "issue_comments": ISSUE_COMMENTS,
            }.items():
                path = Path(tmp) / f"{name}.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                paths[name] = str(path)
            out = subprocess.run(
                [
                    "jq", "-n", "-f", str(NORMALIZER),
                    "--slurpfile", "meta", paths["meta"],
                    "--slurpfile", "threads", paths["threads"],
                    "--slurpfile", "reviews", paths["reviews"],
                    "--slurpfile", "issue_comments", paths["issue_comments"],
                    "--arg", "pr_author", "me",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        cls.doc = json.loads(out.stdout)

    def test_review_bodies_keep_full_body_and_drop_blank_and_pending(self) -> None:
        bodies = self.doc["review_bodies"]
        self.assertEqual([b["id"] for b in bodies], [9001, 9002, 9005])
        self.assertEqual(bodies[0]["body"], CODERABBIT_BODY)
        self.assertEqual(
            set(bodies[0]),
            {"id", "author", "vendor", "state", "body", "submitted_at", "url", "commit_id", "embedded_findings"},
        )
        self.assertEqual(bodies[0]["vendor"], "coderabbit")
        self.assertEqual(bodies[1]["vendor"], "devin")
        self.assertEqual(bodies[1]["url"], "https://github.com/o/r/pull/7#pullrequestreview-9002")

    def test_outside_diff_range_findings_are_decomposed(self) -> None:
        findings = self.doc["review_bodies"][0]["embedded_findings"]
        outside = [f for f in findings if f["category"] == "outside_diff_range"]
        self.assertEqual(
            [(f["path"], f["start_line"], f["end_line"], f["title"]) for f in outside],
            [
                ("src/payment/retry.ts", 88, 96, "Retry loop never backs off on 429"),
                ("src/payment/retry.ts", 120, 120, "Swallowed error hides timeout"),
                ("README.md", 12, 14, "Setup steps reference removed script"),
            ],
        )
        # 指摘内のネスト <details> (Prompt for AI Agents) は本文として残る
        self.assertIn("honor the Retry-After header", outside[0]["body"])
        self.assertIn("ignores `Retry-After`", outside[0]["body"])
        # 区切り線や閉じタグが本文に混入しない
        self.assertNotIn("---", outside[0]["body"].splitlines())
        self.assertNotIn("</blockquote>", outside[1]["body"])
        self.assertEqual(outside[1]["body"], "`catch {}` drops the timeout error; rethrow or log it.")

    def test_nitpick_findings_are_decomposed_and_review_details_ignored(self) -> None:
        findings = self.doc["review_bodies"][0]["embedded_findings"]
        self.assertEqual(
            [(f["category"], f["path"], f["start_line"], f["title"]) for f in findings if f["category"] != "outside_diff_range"],
            [("nitpick", "src/payment/client.ts", 5, "Unused import")],
        )
        self.assertEqual(len(findings), 4)

    def test_callout_layout_outside_diff_findings_are_decomposed(self) -> None:
        callout = self.doc["review_bodies"][2]
        self.assertEqual(callout["body"], CODERABBIT_CALLOUT_BODY)
        findings = callout["embedded_findings"]
        self.assertEqual(
            [(f["category"], f["path"], f["start_line"], f["end_line"], f["title"]) for f in findings],
            [
                ("outside_diff_range", "packages/cli/src/headless.ts", 23, 26,
                 "Always lay out the requested page before export."),
                # summary にタイトルが無ければ本文の最初の文で補う
                ("outside_diff_range", "python/cuda/impl.pyx", 1184, 1184,
                 "suggestion: `locality_domain_count` still raises `ValueError(... \"(see stderr)\")`."),
            ],
        )
        self.assertIn("skips `computeAllLayouts`", findings[0]["body"])
        # callout の引用記号や閉じタグが本文に残らない
        self.assertNotIn("> ", findings[0]["body"])
        self.assertNotIn("</details>", findings[1]["body"])
        # Review info 配下 (Files selected 等) は指摘として分解しない
        self.assertFalse(any("Files selected" in f["body"] for f in findings))

    def test_non_coderabbit_body_has_no_embedded_findings_but_keeps_body(self) -> None:
        devin = self.doc["review_bodies"][1]
        self.assertEqual(devin["embedded_findings"], [])
        self.assertIn("outside the diff", devin["body"])

    def test_issue_comments_keep_full_body(self) -> None:
        comments = self.doc["issue_comments"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["body"], CODERABBIT_SUMMARY)
        self.assertEqual(comments[0]["vendor"], "coderabbit")
        self.assertEqual(comments[0]["url"], "https://github.com/o/r/pull/7#issuecomment-5001")

    def test_threads_shape_unchanged(self) -> None:
        threads = self.doc["threads"]
        self.assertEqual([t["thread_id"] for t in threads], ["PRRT_1", "PRRT_2", "PRRT_3", "PRRT_4"])
        self.assertTrue(threads[0]["self_replied"])
        self.assertFalse(threads[1]["self_replied"])
        self.assertFalse(threads[2]["self_replied"])
        self.assertTrue(threads[3]["is_outdated"])
        self.assertEqual(threads[0]["root_comment"]["vendor"], "devin")
        self.assertEqual(threads[0]["root_comment"]["id"], 101)

    def test_counts_per_source(self) -> None:
        self.assertEqual(
            self.doc["counts"],
            {
                "threads": 4,
                # PRRT_1 (self-replied) + PRRT_3 + PRRT_4 (outdated)
                "unresolved_threads": 3,
                # Phase A の triage 対象は PRRT_3 だけ。最終 gate はこれで照合する (skills#157 Devin 指摘)
                "eligible_threads": 1,
                "skipped_threads": 2,
                "review_bodies": 3,
                "embedded_findings": 6,
                "issue_comments": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
