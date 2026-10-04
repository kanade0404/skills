"""pr-review-respond の prr スクリプトを、偽 gh (fixture) で GitHub を叩かずに検証する.

背景 (2026-10-03, skills#157 のレビュー対応):
- `prr fetch` をクローン外 (別リポジトリの cwd) から実行すると `gh repo view` が cwd の
  リポジトリを返し、同番号の PR がそこに存在すると rc=0 のまま threads / reviews /
  comments が全部空で返った (「指摘ゼロ」と区別不能)。さらに `gh repo view` は GH_REPO を
  無視し `gh pr view` は尊重するため、GH_REPO を指定すると PR メタと本体が別リポジトリから
  取られていた。→ `prr -R owner/repo` を追加し、全 gh 呼び出しを 1 つの解決結果に揃える。
  PR が解決先に無ければ非ゼロ終了する。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRR = REPO_ROOT / "skills" / "pr-review-respond" / "scripts" / "prr"
FAKE_GH = Path(__file__).resolve().parent / "fixtures" / "pr_review_respond" / "fake_gh.sh"


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "jq and bash are required")
class PrrTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        bindir = self.tmp / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        shutil.copyfile(FAKE_GH, gh)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.env = {
            k: v for k, v in os.environ.items() if k not in ("GH_REPO", "PRR_REPO_SOURCE")
        }
        self.env.update(
            PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
            FAKE_GH_STATE=str(self.state),
            # the "clone" we are standing in is a different repository
            FAKE_GH_CWD_REPO="cwd/other",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def add_pr(self, repo: str, number: int) -> None:
        with (self.state / "prs").open("a", encoding="utf-8") as f:
            f.write(f"{repo}#{number}\n")

    def prr(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(PRR), *args],
            env={**self.env, **(env or {})},
            cwd=self.tmp,
            capture_output=True,
            text=True,
        )

    def calls(self) -> list[str]:
        log = self.state / "calls.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


class TestFetchRepositoryResolution(PrrTestCase):
    def assert_fetched_from(self, proc: subprocess.CompletedProcess[str], repo: str) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["pr"]["repo"], repo)
        self.assertEqual(doc["pr"]["url"], f"https://github.com/{repo}/pull/7")
        self.assertIn(f"repository {repo}", proc.stderr)
        owner, name = repo.split("/")
        calls = self.calls()
        graphql = [c for c in calls if " api graphql " in f" {c} "]
        self.assertTrue(graphql)
        for c in graphql:
            self.assertIn(f"owner={owner}", c)
            self.assertIn(f"repo={name}", c)
        rest = [c for c in calls if " api --paginate " in c]
        self.assertEqual(len(rest), 2)
        for c in rest:
            self.assertIn(f"repos/{repo}/", c)
        pr_views = [c for c in calls if " pr view " in f" {c} "]
        self.assertTrue(pr_views)
        for c in pr_views:
            self.assertIn(f"-R {repo}", c)

    def test_dash_r_before_subcommand_targets_that_repo(self) -> None:
        self.add_pr("o/r", 7)
        self.add_pr("cwd/other", 7)  # same number exists in the cwd repo too
        self.assert_fetched_from(self.prr("-R", "o/r", "fetch", "7"), "o/r")

    def test_dash_r_after_subcommand_targets_that_repo(self) -> None:
        self.add_pr("o/r", 7)
        self.assert_fetched_from(self.prr("fetch", "-R", "o/r", "7"), "o/r")

    def test_gh_repo_env_is_used_for_every_call(self) -> None:
        # 旧実装は gh repo view (GH_REPO 無視) と gh pr view (GH_REPO 尊重) が食い違った
        self.add_pr("o/r", 7)
        self.add_pr("cwd/other", 7)
        self.assert_fetched_from(self.prr("fetch", "7", env={"GH_REPO": "o/r"}), "o/r")

    def test_cwd_repo_without_the_pr_fails_loudly(self) -> None:
        self.add_pr("o/r", 7)
        proc = self.prr("fetch", "7")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("cwd/other", proc.stderr)
        self.assertIn("-R owner/repo", proc.stderr)

    def test_graphql_null_pull_request_fails_loudly(self) -> None:
        self.add_pr("o/r", 7)
        proc = self.prr("-R", "o/r", "fetch", "7", env={"FAKE_GH_GRAPHQL_NULL": "1"})
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertIn("o/r", proc.stderr)

    def test_cwd_repo_is_reported_when_no_repo_given(self) -> None:
        self.add_pr("cwd/other", 7)
        proc = self.prr("fetch", "7")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("repository cwd/other (source: cwd)", proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["pr"]["repo"], "cwd/other")

    def test_malformed_repo_option_is_rejected(self) -> None:
        proc = self.prr("-R", "not-a-repo", "fetch", "7")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("owner/repo", proc.stderr)
        self.assertEqual(self.calls(), [])

    def test_non_github_com_host_is_rejected_before_any_call(self) -> None:
        # skills#157 Devin 指摘: HOST/OWNER/REPO の HOST を捨てて github.com の o/r を読んでいた
        self.add_pr("o/r", 7)
        proc = self.prr("-R", "ghe.example.com/o/r", "fetch", "7")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("ghe.example.com", proc.stderr)
        self.assertEqual(self.calls(), [])

    def test_explicit_github_com_host_is_normalized_to_owner_repo(self) -> None:
        self.add_pr("o/r", 7)
        self.assert_fetched_from(self.prr("-R", "github.com/o/r", "fetch", "7"), "o/r")


class TestOtherSubcommandsHonorRepo(PrrTestCase):
    def test_reply_posts_to_the_explicit_repo(self) -> None:
        body = self.tmp / "body.md"
        body.write_text("Fixed in abc", encoding="utf-8")
        proc = self.prr("-R", "o/r", "reply", "7", "101", str(body))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        posts = [c for c in self.calls() if " -X POST " in c]
        self.assertEqual(len(posts), 1)
        self.assertIn("repos/o/r/pulls/7/comments/101/replies", posts[0])
        self.assertFalse(any(" repo view" in c for c in self.calls()))

    def test_summary_comments_on_the_explicit_repo(self) -> None:
        body = self.tmp / "body.md"
        body.write_text("## Review Response Summary", encoding="utf-8")
        proc = self.prr("summary", "7", str(body), env={"GH_REPO": "o/r"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        comments = [c for c in self.calls() if " pr comment " in f" {c} "]
        self.assertEqual(len(comments), 1)
        self.assertIn("-R o/r", comments[0])


REVIEW_URL = "https://github.com/o/r/pull/7#pullrequestreview-9001"


class TestDeferPerFindingKey(PrrTestCase):
    """skills#157 Devin 指摘: 同一レビュー本文の 2 指摘が review URL を共有すると、
    `prr defer` の既存 issue 検索 (body に URL を含むか) が 1 件目の issue を 2 件目にも返していた。"""

    def setUp(self) -> None:
        super().setUp()
        self.add_pr("o/r", 7)
        self.body = self.tmp / "body.md"
        self.body.write_text("summary + why out of scope", encoding="utf-8")

    def defer(self, url: str) -> subprocess.CompletedProcess[str]:
        return self.prr("-R", "o/r", "defer", "7", url, "Title", str(self.body))

    def issue_number(self, proc: subprocess.CompletedProcess[str]) -> int:
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return int(proc.stdout.split()[0])

    def test_two_findings_of_one_review_get_separate_issues(self) -> None:
        first = self.issue_number(self.defer(f"{REVIEW_URL}#finding-outside_diff_range-src/a.ts-10-15-1"))
        second = self.issue_number(self.defer(f"{REVIEW_URL}#finding-nitpick-src/b.ts-3-3-2"))
        self.assertNotEqual(first, second)
        self.assertEqual(len(list((self.state / "issues").glob("*.json"))), 2)

    def test_retry_of_the_same_finding_reuses_its_issue(self) -> None:
        url = f"{REVIEW_URL}#finding-outside_diff_range-src/a.ts-10-15-1"
        first = self.issue_number(self.defer(url))
        self.assertEqual(self.issue_number(self.defer(url)), first)
        self.assertEqual(len(list((self.state / "issues").glob("*.json"))), 1)

    def test_key_that_is_a_prefix_of_another_key_is_not_confused(self) -> None:
        tenth = self.issue_number(self.defer(f"{REVIEW_URL}#finding-nitpick-a.ts-1-1-10"))
        first = self.issue_number(self.defer(f"{REVIEW_URL}#finding-nitpick-a.ts-1-1-1"))
        self.assertNotEqual(tenth, first)

    def test_bare_review_or_comment_url_is_rejected(self) -> None:
        for url in (REVIEW_URL, "https://github.com/o/r/pull/7#issuecomment-5001"):
            with self.subTest(url=url):
                proc = self.defer(url)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("#finding-", proc.stderr)
        self.assertEqual(list((self.state / "issues").glob("*.json")), [])

    def test_inline_thread_url_still_works_and_dedups(self) -> None:
        url = "https://github.com/o/r/pull/7#discussion_r101"
        first = self.issue_number(self.defer(url))
        self.assertEqual(self.issue_number(self.defer(url)), first)
        created = json.loads((self.state / "issues" / f"{first}.json").read_text(encoding="utf-8"))
        self.assertTrue(created["body"].rstrip().endswith(f"review thread: {url}"))

    def test_retry_finds_its_issue_beyond_the_first_100_candidates(self) -> None:
        # skills#157 Devin 指摘: 検索は review URL 単位なので、同じレビューの issue が 100 件を
        # 超えると --limit 100 の外に既存 issue が落ち、再実行で重複 issue を作っていた
        target = f"{REVIEW_URL}#finding-nitpick-a.ts-1-1-1"
        issues = self.state / "issues"
        issues.mkdir(exist_ok=True)
        for n in range(1, 102):
            url = target if n == 1 else f"{REVIEW_URL}#finding-nitpick-a.ts-1-1-{n}"
            (issues / f"{n}.json").write_text(
                json.dumps(
                    {
                        "repo": "o/r",
                        "number": n,
                        "url": f"https://github.com/o/r/issues/{n}",
                        "body": f"s\n\nDeferred from PR https://github.com/o/r/pull/7 review thread: {url}\n",
                    }
                ),
                encoding="utf-8",
            )
        self.assertEqual(self.issue_number(self.defer(target)), 1)
        self.assertEqual(len(list(issues.glob("*.json"))), 101)


if __name__ == "__main__":
    unittest.main()
