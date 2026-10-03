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


if __name__ == "__main__":
    unittest.main()
