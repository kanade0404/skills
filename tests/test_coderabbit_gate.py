from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

import pytest

import coderabbit_gate as gate
from support import write_executable

if TYPE_CHECKING:
    from collections.abc import Sequence

# kanade0404/skills の PR #186・#187・#188 から取った実データ(本文、要約コメント、check run)。
REAL = gate.as_dict(gate.parse_json((Path(__file__).parent / "fixtures" / "coderabbit_gate" / "real.json").read_text()))
HEAD = gate.text(REAL["head_187"])
OTHER = "0" * 40
TARGET = gate.Target(owner="kanade0404", name="skills", number=1)
PAGE = 2
SUMMARY = "<!-- summarize by coderabbit.ai -->\n"


def no_gh(_: str) -> None:
    return None


def real(key: str) -> str:
    return gate.text(REAL[key])


def real_run(key: str) -> dict[str, object]:
    return gate.as_dict(REAL[key])


def pull(
    *, state: str = "OPEN", draft: bool = False, mergeable: str = "MERGEABLE", head: str = HEAD
) -> dict[str, object]:
    return {"state": state, "isDraft": draft, "headRefOid": head, "mergeable": mergeable, "mergeStateStatus": "CLEAN"}


def comment(
    db_id: int, body: str, author: str = "kanade0404", typename: str = "", association: str = ""
) -> dict[str, object]:
    # GraphQL の形。CodeRabbit の名前なら Bot、それ以外は User にする。kanade0404 だけをリポジトリの所有者とする。
    bot = author in gate.CODERABBIT_LOGINS
    return {
        "databaseId": db_id,
        "author": {"login": author, "__typename": typename or ("Bot" if bot else "User")},
        "authorAssociation": association or ("OWNER" if author == "kanade0404" else "NONE"),
        "body": body,
        "url": f"https://example.test/c/{db_id}",
    }


def review(
    db_id: int, body: str, commit: str = HEAD, author: str = "coderabbitai", typename: str = ""
) -> dict[str, object]:
    return {**comment(db_id, body, author, typename), "commit": {"oid": commit}}


def thread(  # ruff: ignore[too-many-arguments] - スレッドの形を組み立てるテストの部品
    thread_id: str,
    *,
    resolved: bool = False,
    body: str = "**Minor** · **Fix the thing please.**",
    last_author: str = "coderabbitai",
    last_body: str = "",
    line: int | None = 3,
    first_author: str = "coderabbitai",
) -> dict[str, object]:
    first = comment(10, body, first_author)
    return {
        "id": thread_id,
        "isResolved": resolved,
        "isOutdated": False,
        "path": "a.py",
        "line": line,
        "originalLine": 7,
        "first": {"nodes": [first]},
        "last": {"nodes": [{**comment(11, last_body, last_author)}]},
    }


def check(  # ruff: ignore[too-many-arguments] - check run の形を組み立てるテストの部品
    name: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    app: str = "github-actions",
    title: str = "",
    run_id: int = 1,
) -> dict[str, object]:
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://example.test/run/{run_id}",
        "app": {"slug": app},
        "output": {"title": title, "summary": ""},
    }


def coderabbit_check(
    *, status: str = "completed", conclusion: str | None = "success", title: str = "Finished"
) -> dict[str, object]:
    return check("CodeRabbit", status=status, conclusion=conclusion, app="coderabbitai", title=title, run_id=2)


GREEN_CI = [check(gate.GATE_CHECK, run_id=3)]
HEAD_REVIEW = review(1, "<!-- This is an auto-generated comment by CodeRabbit for review status -->")


class FakeGitHub:
    def __init__(  # ruff: ignore[too-many-arguments] - 偽の GitHub が返す接続ごとの中身
        self,
        *,
        pull_request: dict[str, object] | None = None,
        threads: Sequence[dict[str, object]] = (),
        reviews: Sequence[dict[str, object]] = (),
        comments: Sequence[dict[str, object]] = (),
        check_runs: Sequence[dict[str, object]] = (),
        status: dict[str, object] | None = None,
    ) -> None:
        self.pull: dict[str, object] = pull_request or pull()
        self.connections: dict[str, list[dict[str, object]]] = {
            gate.THREADS_QUERY: list(threads),
            gate.REVIEWS_QUERY: list(reviews),
            gate.COMMENTS_QUERY: list(comments),
        }
        self.check_runs: list[dict[str, object]] = list(check_runs)
        self.status: dict[str, object] = status or {"state": "pending", "total_count": 0}
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        self.calls.append(list(args))
        if list(args[:2]) == ["api", "graphql"]:
            fields = {arg.partition("=")[0]: arg.partition("=")[2] for arg in args[2:] if "=" in arg}
            query = fields["query"]
            if query == gate.PR_QUERY:
                return json.dumps({"data": {"repository": {"pullRequest": self.pull}}})
            nodes = self.connections[query]
            start = int(fields.get("after", "0"))
            more = start + PAGE < len(nodes)
            page = {
                "pageInfo": {"hasNextPage": more, "endCursor": str(start + PAGE) if more else None},
                "nodes": nodes[start : start + PAGE],
            }
            return json.dumps({"data": {"repository": {"pullRequest": {"page": page}}}})
        if "--paginate" in args:
            chunks = [self.check_runs[i : i + PAGE] for i in range(0, len(self.check_runs), PAGE)] or [[]]
            return "\n".join(json.dumps({"total_count": len(self.check_runs), "check_runs": c}) for c in chunks)
        return json.dumps(self.status)


def evaluate(github: FakeGitHub, *, allow_no_ci: bool = False) -> dict[str, object]:
    return gate.evaluate(gate.fetch(github, TARGET), TARGET, allow_no_ci=allow_no_ci)


def section(result: dict[str, object], key: str) -> dict[str, object]:
    return gate.as_dict(result[key])


def items(result: dict[str, object], key: str) -> list[dict[str, object]]:
    return [gate.as_dict(item) for item in gate.as_list(result[key])]


def reasons(result: dict[str, object]) -> str:
    return "\n".join(gate.text(reason) for reason in gate.as_list(result["reasons"]))


class TestCodeRabbitState:
    def test_ready_with_review_for_head(self) -> None:
        result = evaluate(FakeGitHub(reviews=[HEAD_REVIEW], check_runs=[*GREEN_CI, coderabbit_check()]))
        assert result["verdict"] == gate.READY
        assert section(result, "coderabbit")["reviewed_head"] is True
        assert result["reasons"] == []

    def test_clean_review_is_read_from_summary_coverage(self) -> None:
        # #187: 指摘のないレビューはレビューのオブジェクトを作らず、要約コメントだけを更新する。
        github = FakeGitHub(
            comments=[comment(1, real("summary_reviewed"), "coderabbitai[bot]")],
            check_runs=[*GREEN_CI, real_run("check_run_finished")],
        )
        result = evaluate(github)
        assert result["verdict"] == gate.READY
        assert "final_review_risk_coverage" in gate.text(gate.as_list(section(result, "coderabbit")["evidence"])[0])

    def test_summary_between_line_is_a_fallback(self) -> None:
        body = f"{SUMMARY}<!-- recent_review_start -->between {OTHER} and {HEAD[:7]}.<!-- recent_review_end -->"
        result = evaluate(FakeGitHub(comments=[comment(1, body, "coderabbitai")], check_runs=GREEN_CI))
        assert result["verdict"] == gate.READY

    @pytest.mark.parametrize("marker", [gate.IN_PROGRESS_MARKER, gate.RATE_LIMIT_MARKER])
    def test_between_line_is_ignored_while_busy_or_limited(self, marker: str) -> None:
        recent = f"<!-- recent_review_start -->between {OTHER} and {HEAD}<!-- recent_review_end -->"
        body = f"{SUMMARY}<!-- {marker} -->\n{recent}"
        result = evaluate(FakeGitHub(comments=[comment(1, body, "coderabbitai")], check_runs=GREEN_CI))
        assert section(result, "coderabbit")["reviewed_head"] is False

    def test_broken_coverage_marker_is_not_evidence(self) -> None:
        body = "<!-- summarize by coderabbit.ai --><!-- final_review_risk_coverage:{broken} -->"
        result = evaluate(FakeGitHub(comments=[comment(1, body, "coderabbitai")], check_runs=GREEN_CI))
        assert section(result, "coderabbit")["state"] == "not_started"

    def test_paused_head_is_blocked_even_with_reply_reviews_on_head(self) -> None:
        # #188: head にはスレッドへの返信(本文が空のレビュー)しかなく、要約コメントは自動停止を示す。
        head = real("head_188")
        github = FakeGitHub(
            pull_request=pull(head=head),
            reviews=[review(1, "", head), review(2, real("review_status_only"), OTHER)],
            comments=[comment(1, real("summary_paused"), "coderabbitai")],
            check_runs=[check(gate.GATE_CHECK, run_id=3), real_run("check_run_paused")],
        )
        result = evaluate(github)
        assert result["verdict"] == gate.BLOCKED
        assert section(result, "coderabbit")["state"] == "paused"
        assert result["commands"] == ["@coderabbitai resume", "@coderabbitai review"]

    def test_skipped_check_with_review_for_head_is_ready(self) -> None:
        github = FakeGitHub(reviews=[HEAD_REVIEW], check_runs=[*GREEN_CI, real_run("check_run_paused")])
        assert evaluate(github)["verdict"] == gate.READY

    def test_rate_limited(self) -> None:
        notice = (
            "<!-- This is an auto-generated comment: rate limited by coderabbit.ai -->\n"
            "Next review available in: **38 minutes**\n"
            "<!-- end of auto-generated comment: rate limited by coderabbit.ai -->"
        )
        github = FakeGitHub(
            comments=[comment(1, notice, "coderabbitai")],
            check_runs=[*GREEN_CI, coderabbit_check(conclusion="neutral", title="Review rate limited")],
        )
        result = evaluate(github)
        assert result["verdict"] == gate.BLOCKED
        assert "38 minutes" in reasons(result)
        assert result["commands"] == ["@coderabbitai rate limit", "@coderabbitai review"]

    def test_rate_limited_without_readable_wait(self) -> None:
        github = FakeGitHub(check_runs=[*GREEN_CI, coderabbit_check(conclusion="neutral", title="Review rate limited")])
        result = evaluate(github)
        assert section(result, "coderabbit")["state"] == "rate_limited"
        assert "次は" not in reasons(result)

    @pytest.mark.parametrize(
        ("checks", "comments"),
        [
            ([coderabbit_check(status="in_progress", conclusion=None, title="Review in progress")], []),
            ([], [comment(1, f"<!-- {gate.IN_PROGRESS_MARKER} -->", "coderabbitai")]),
        ],
    )
    def test_in_progress_waits(self, checks: list[dict[str, object]], comments: list[dict[str, object]]) -> None:
        result = evaluate(FakeGitHub(check_runs=[*GREEN_CI, *checks], comments=comments))
        assert result["verdict"] == gate.WAITING
        assert section(result, "coderabbit")["state"] == "in_progress"

    def test_rereview_of_reviewed_head_waits(self) -> None:
        github = FakeGitHub(
            reviews=[HEAD_REVIEW], check_runs=[*GREEN_CI, coderabbit_check(status="queued", conclusion=None)]
        )
        assert evaluate(github)["verdict"] == gate.WAITING

    def test_nothing_from_coderabbit_yet_waits(self) -> None:
        result = evaluate(FakeGitHub(check_runs=GREEN_CI))
        assert result["verdict"] == gate.WAITING
        assert section(result, "coderabbit")["state"] == "not_started"

    def test_finished_check_without_published_review_waits(self) -> None:
        result = evaluate(FakeGitHub(check_runs=[*GREEN_CI, coderabbit_check()]))
        assert section(result, "coderabbit")["state"] == "publishing"
        assert result["verdict"] == gate.WAITING

    def test_draft_is_blocked(self) -> None:
        github = FakeGitHub(
            pull_request=pull(draft=True),
            check_runs=[*GREEN_CI, coderabbit_check(conclusion="skipped", title="Review skipped")],
        )
        result = evaluate(github)
        assert section(result, "coderabbit")["state"] == "draft"
        assert "@coderabbitai full review" in gate.as_list(result["commands"])

    def test_other_skip_is_blocked(self) -> None:
        github = FakeGitHub(check_runs=[*GREEN_CI, coderabbit_check(conclusion="skipped", title="")])
        result = evaluate(github)
        assert result["verdict"] == gate.BLOCKED
        assert "skipped" in reasons(result)

    def test_no_files_to_review_is_ready(self) -> None:
        github = FakeGitHub(check_runs=[*GREEN_CI, coderabbit_check(conclusion="skipped", title="No files to review")])
        result = evaluate(github)
        assert result["verdict"] == gate.READY
        assert section(result, "coderabbit")["state"] == "nothing_to_review"

    @pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
    def test_failed_review_is_blocked(self, conclusion: str) -> None:
        github = FakeGitHub(check_runs=[*GREEN_CI, coderabbit_check(conclusion=conclusion, title="Review failed")])
        result = evaluate(github)
        assert section(result, "coderabbit")["state"] == "failed"
        assert result["commands"] == ["@coderabbitai review"]

    def test_actionable_count_is_reported(self) -> None:
        github = FakeGitHub(
            reviews=[review(1, real("review_outside_diff"), real("review_outside_diff_commit"))], check_runs=GREEN_CI
        )
        reviews = items(section(evaluate(github), "coderabbit"), "reviews")
        assert reviews[0]["actionable_posted"] == 2


class TestPublication:
    def test_partial_publication_waits(self) -> None:
        first = review(1, "<!-- coderabbit-review-publication v1 publication=p1 attempt=a batch=1/2 -->")
        result = evaluate(FakeGitHub(reviews=[first], check_runs=[*GREEN_CI, coderabbit_check()]))
        assert result["verdict"] == gate.WAITING
        assert "p1" in reasons(result)

    def test_partial_publication_on_an_old_commit_does_not_block(self) -> None:
        old = review(1, "<!-- coderabbit-review-publication v1 publication=p0 attempt=a batch=1/2 -->", OTHER)
        github = FakeGitHub(reviews=[old, HEAD_REVIEW], check_runs=[*GREEN_CI, coderabbit_check()])
        assert evaluate(github)["verdict"] == gate.READY

    def test_complete_publication_is_reviewed(self) -> None:
        bodies = [f"<!-- coderabbit-review-publication v1 publication=p1 attempt=a batch={i}/2 -->" for i in (1, 2)]
        github = FakeGitHub(reviews=[review(1, bodies[0]), review(2, bodies[1])], check_runs=GREEN_CI)
        assert evaluate(github)["verdict"] == gate.READY


class TestIdentity:
    STATUS: ClassVar[str] = "<!-- auto-generated comment by CodeRabbit for review status -->"

    @pytest.mark.parametrize(
        ("author", "typename"),
        [
            ("coderabbitai-evil", "Bot"),
            ("CodeRabbitAI", "Bot"),
            ("coderabbitai", "User"),
            ("coderabbitai[bot]", "User"),
        ],
    )
    def test_lookalike_review_is_not_coderabbit(self, author: str, typename: str) -> None:
        github = FakeGitHub(reviews=[review(1, self.STATUS, author=author, typename=typename)], check_runs=GREEN_CI)
        result = evaluate(github)
        assert result["verdict"] == gate.WAITING
        assert section(result, "coderabbit")["state"] == "not_started"

    def test_lookalike_summary_is_not_evidence(self) -> None:
        spoof = comment(1, real("summary_reviewed"), "coderabbitai-evil", "Bot")
        assert (
            section(evaluate(FakeGitHub(comments=[spoof], check_runs=GREEN_CI)), "coderabbit")["reviewed_head"] is False
        )

    def test_rest_style_bot_login_is_coderabbit(self) -> None:
        github = FakeGitHub(reviews=[review(1, self.STATUS, author="coderabbitai[bot]")], check_runs=GREEN_CI)
        assert evaluate(github)["verdict"] == gate.READY

    def test_lookalike_check_runs_are_plain_ci(self) -> None:
        runs = [
            check("CodeRabbit", app="coderabbitai-evil", title="Finished", run_id=4),
            check(gate.GATE_CHECK, app="other-app", run_id=5),
        ]
        result = evaluate(FakeGitHub(check_runs=runs))
        assert section(result, "coderabbit")["check"] is None
        assert section(result, "ci")["gate"] is None
        assert section(result, "ci")["state"] == "success"

    def test_outsider_thread_cannot_hide_a_body_finding(self) -> None:
        reviews = [HEAD_REVIEW, review(2, real("review_outside_diff"), real("review_outside_diff_commit"))]
        spam = thread(
            "T9", resolved=True, body="<!-- cr-comment:v1:f9fe793faafbe7cc296b213d -->", first_author="someone"
        )
        result = evaluate(FakeGitHub(reviews=reviews, threads=[spam], check_runs=GREEN_CI))
        assert [f["id"] for f in items(result, "body_findings")] == ["f9fe793faafbe7cc296b213d"]

    def test_ack_from_outsider_does_not_count(self) -> None:
        reviews = [HEAD_REVIEW, review(2, real("review_outside_diff"), real("review_outside_diff_commit"))]
        drive_by = comment(1, "cr-comment:v1:f9fe793faafbe7cc296b213d", "someone", association="NONE")
        result = evaluate(FakeGitHub(reviews=reviews, comments=[drive_by], check_runs=GREEN_CI))
        assert result["addressed_body_findings"] == []
        assert result["verdict"] == gate.ACTION_NEEDED


class TestThreads:
    def test_unresolved_threads_need_action(self) -> None:
        github = FakeGitHub(
            reviews=[HEAD_REVIEW],
            threads=[thread("T1", body=real("inline_comment")), thread("T2", resolved=True), thread("T3", line=None)],
            check_runs=GREEN_CI,
        )
        result = evaluate(github)
        assert result["verdict"] == gate.ACTION_NEEDED
        threads = items(result, "unresolved_threads")
        assert [t["thread_id"] for t in threads] == ["T1", "T3"]
        assert threads[0]["title"] == "存在しないコマンドの失敗契約を追加してください。"
        assert threads[0]["comment_id"] == 10
        assert threads[1]["line"] == 7

    def test_title_falls_back_to_plain_text(self) -> None:
        github = FakeGitHub(
            reviews=[HEAD_REVIEW], threads=[thread("T1", body="<p>plain  note</p>")], check_runs=GREEN_CI
        )
        assert items(evaluate(github), "unresolved_threads")[0]["title"] == "plain note"


class TestBodyFindings:
    def test_outside_diff_in_caution_block(self) -> None:
        # #186 の古い形式。行番号の指定が引用の記号 > をまたいで折り返している。
        github = FakeGitHub(
            reviews=[HEAD_REVIEW, review(2, real("review_outside_diff"), real("review_outside_diff_commit"))],
            check_runs=GREEN_CI,
        )
        result = evaluate(github)
        assert result["verdict"] == gate.ACTION_NEEDED
        (finding,) = items(result, "body_findings")
        assert finding["id"] == "f9fe793faafbe7cc296b213d"
        assert finding["kind"] == "outside_diff"
        assert (finding["path"], finding["line"]) == (".claude/skills/verify-ja-writing/scripts/verify.sh", 67)
        assert "Keep session records separate" in gate.text(finding["title"])

    def test_nitpick_title_is_the_bold_heading(self) -> None:
        body = (
            "<!-- coderabbit-ai-generated:v1 -->\n**Actionable comments posted: 0**\n"
            "<details>\n<summary>Nitpick comments: 1</summary><blockquote>\n"
            "<details>\n<summary>pyproject.toml: 1</summary><blockquote>\n"
            "`11-12`: **Trivial** · **Maintainability & Code Quality** · **Quick win**\n"
            "**Update the ast-grep pin or the parity claim. CodeRabbit now runs v0.51.1.**\n"
            "<details>\n<summary>Proposed change</summary>\n</details>\n"
            "Review comment at @pyproject.toml around lines 11 - 12:\n"
            "<!-- cr-comment:v1:00168dfab1ed5dab9d0314a1 -->\n"
        )
        github = FakeGitHub(reviews=[HEAD_REVIEW, review(2, body)], check_runs=GREEN_CI)
        (finding,) = items(evaluate(github), "body_findings")
        assert finding["title"] == "Update the ast-grep pin or the parity claim. CodeRabbit now runs v0.51.1."

    def test_follow_up_comments(self) -> None:
        # #188 の新しい形式。5 件とも本文だけにあり、スレッドを持たない。
        github = FakeGitHub(
            reviews=[HEAD_REVIEW, review(2, real("review_follow_up"), real("review_follow_up_commit"))],
            check_runs=GREEN_CI,
        )
        findings = items(evaluate(github), "body_findings")
        assert [(f["line"], f["end_line"]) for f in findings] == [
            (132, 143),
            (57, None),
            (35, 36),
            (63, None),
            (94, 101),
        ]
        assert {f["kind"] for f in findings} == {"outside_diff"}
        assert findings[0]["title"] == "完了条件に、B の必須要件をすべて対応付けてください。"

    def test_reply_citing_the_id_addresses_a_finding(self) -> None:
        reviews = [HEAD_REVIEW, review(2, real("review_follow_up"), real("review_follow_up_commit"))]
        github = FakeGitHub(
            reviews=reviews,
            comments=[
                comment(1, "対応した。cr-comment:v1:903913addc60f6ad14d827ef"),
                comment(2, "d0a8e6e9c849f79eb227f156", "coderabbitai[bot]"),
            ],
            threads=[thread("T1", resolved=True, last_author="kanade0404", last_body="db037873be12082acc6d64dc")],
            check_runs=GREEN_CI,
        )
        result = evaluate(github)
        assert result["addressed_body_findings"] == ["903913addc60f6ad14d827ef", "db037873be12082acc6d64dc"]
        assert len(items(result, "body_findings")) == 3

    def test_inline_ids_and_duplicates_are_skipped(self) -> None:
        body = (
            "<!-- auto-generated comment by CodeRabbit for review status -->\n"
            "<details><summary>🧹 Nitpick comments (2)</summary><details><summary>a.py (2)</summary>"
            "<details><summary>Rename x.</summary>Review comment at @a.py at line 3:\nfix\n"
            "<!-- cr-comment:v1:aaaaaaaaaaaa --></details>"
            "<details><summary>Inline one</summary><!-- cr-comment:v1:bbbbbbbbbbbb --></details>"
        )
        inline = thread("T1", resolved=True, body="<!-- cr-comment:v1:bbbbbbbbbbbb -->")
        github = FakeGitHub(
            reviews=[HEAD_REVIEW, review(2, body), review(3, body)], threads=[inline], check_runs=GREEN_CI
        )
        (finding,) = items(evaluate(github), "body_findings")
        assert (finding["id"], finding["kind"], finding["title"]) == ("aaaaaaaaaaaa", "nitpick", "Rename x.")

    def test_source_link_and_missing_location(self) -> None:
        body = (
            "<!-- coderabbit-review-publication v1 publication=p attempt=a batch=1/1 -->\n"
            "**Use the link.** [Open](https://github.com/o/r/blob/abcdef1/src/b.py#L9)\n"
            "<!-- cr-comment:v1:cccccccccccc -->\n"
            "**No location here at all.**\n<!-- cr-comment:v1:dddddddddddd -->"
        )
        findings = items(
            evaluate(FakeGitHub(reviews=[HEAD_REVIEW, review(2, body)], check_runs=GREEN_CI)), "body_findings"
        )
        assert [(f["path"], f["line"], f["kind"]) for f in findings] == [
            ("src/b.py", 9, "review_body"),
            ("", None, "review_body"),
        ]

    def test_findings_without_ids_are_tracked_per_review(self) -> None:
        body = "Actionable comments posted: 0\n\n**⚠️ Outside diff range comments (2)**\n- old style"
        github = FakeGitHub(
            reviews=[HEAD_REVIEW, review(7, body)], comments=[comment(1, "review-8")], check_runs=GREEN_CI
        )
        (finding,) = items(evaluate(github), "body_findings")
        assert (finding["id"], finding["kind"]) == ("review-7", "unparsed")


class TestCi:
    def test_failure_blocks(self) -> None:
        github = FakeGitHub(reviews=[HEAD_REVIEW], check_runs=[check("lint", conclusion="failure")])
        result = evaluate(github)
        assert result["verdict"] == gate.BLOCKED
        assert section(result, "ci")["state"] == "failure"

    def test_pending_waits(self) -> None:
        github = FakeGitHub(reviews=[HEAD_REVIEW], check_runs=[check("lint", status="in_progress", conclusion=None)])
        assert section(evaluate(github), "ci")["state"] == "pending"

    def test_gate_is_authoritative(self) -> None:
        # automerge-gate があれば、ignore-checks で除いたチェックの失敗は見ない。
        runs = [check(gate.GATE_CHECK, run_id=3), check("claude", conclusion="failure", run_id=4)]
        result = evaluate(FakeGitHub(reviews=[HEAD_REVIEW], check_runs=runs))
        assert result["verdict"] == gate.READY
        assert gate.as_dict(section(result, "ci")["gate"])["name"] == gate.GATE_CHECK

    def test_newest_run_of_a_name_wins(self) -> None:
        runs = [
            check("lint", conclusion="failure", run_id=5),
            check("lint", run_id=9),
            check("lint", conclusion="failure", run_id=6),
        ]
        assert evaluate(FakeGitHub(reviews=[HEAD_REVIEW], check_runs=runs))["verdict"] == gate.READY

    @pytest.mark.parametrize(
        ("state", "expected"),
        [("failure", "failure"), ("error", "failure"), ("pending", "pending"), ("success", "success")],
    )
    def test_legacy_commit_statuses(self, state: str, expected: str) -> None:
        github = FakeGitHub(reviews=[HEAD_REVIEW], check_runs=GREEN_CI, status={"state": state, "total_count": 1})
        assert section(evaluate(github), "ci")["state"] == expected

    def test_no_ci_waits_unless_allowed(self) -> None:
        github = FakeGitHub(reviews=[HEAD_REVIEW])
        assert evaluate(github)["verdict"] == gate.WAITING
        result = evaluate(github, allow_no_ci=True)
        assert result["verdict"] == gate.READY
        assert section(result, "ci")["state"] == "none"


class TestPullRequestState:
    def test_conflict_blocks(self) -> None:
        github = FakeGitHub(pull_request=pull(mergeable="CONFLICTING"), reviews=[HEAD_REVIEW], check_runs=GREEN_CI)
        assert "conflict" in reasons(evaluate(github))

    def test_unknown_mergeability_waits(self) -> None:
        github = FakeGitHub(pull_request=pull(mergeable="UNKNOWN"), reviews=[HEAD_REVIEW], check_runs=GREEN_CI)
        assert evaluate(github)["verdict"] == gate.WAITING

    @pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
    def test_closed_pull_request(self, state: str) -> None:
        github = FakeGitHub(pull_request=pull(state=state, mergeable="UNKNOWN"), check_runs=GREEN_CI)
        result = evaluate(github)
        assert (result["verdict"], result["analysis_verdict"], result["pr_state"]) == (gate.CLOSED, gate.WAITING, state)
        assert state in reasons(result)
        assert "計算" not in reasons(result)


class TestFetch:
    def test_paginates_every_connection_and_check_run_page(self) -> None:
        github = FakeGitHub(
            reviews=[review(i, "") for i in range(5)],
            comments=[comment(i, "") for i in range(3)],
            check_runs=[check(f"c{i}", run_id=i) for i in range(5)],
        )
        snapshot = gate.fetch(github, TARGET)
        assert (len(snapshot.reviews), len(snapshot.comments), len(snapshot.check_runs)) == (5, 3, 5)
        assert any("after=4" in call for call in github.calls)

    def test_variables_are_passed_as_fields(self) -> None:
        github = FakeGitHub()
        _ = gate.fetch(github, TARGET)
        first = github.calls[0]
        assert first[:2] == ["api", "graphql"]
        assert "owner=kanade0404" in first
        assert first[first.index("-F") : first.index("-F") + 2] == ["-F", "number=1"]
        assert not any("kanade0404" in arg for arg in first if arg.startswith("query="))

    def test_graphql_errors(self) -> None:
        with pytest.raises(gate.GhError, match="GraphQL"):
            _ = gate.graphql(lambda _: json.dumps({"errors": [{"message": "x"}]}), "q", TARGET)

    def test_missing_pull_request(self) -> None:
        with pytest.raises(gate.GhError, match="見つからない"):
            _ = gate.graphql(lambda _: json.dumps({"data": {"repository": {"pullRequest": None}}}), "q", TARGET)

    def test_invalid_json(self) -> None:
        with pytest.raises(gate.GhError):
            _ = gate.parse_json("<html>")
        with pytest.raises(gate.GhError):
            _ = gate.parse_json_stream('{"a": 1} nope')

    def test_json_stream(self) -> None:
        assert gate.parse_json_stream('{"a": 1}{"b": 2}\n {"c": 3}\n') == [{"a": 1}, {"b": 2}, {"c": 3}]
        assert gate.parse_json_stream("") == []


class TestGh:
    def test_env_drops_forced_color(self) -> None:
        env = gate.gh_env({"CLICOLOR_FORCE": "1", "GH_FORCE_TTY": "1", "PATH": "/bin"})
        assert env == {"PATH": "/bin", "NO_COLOR": "1", "GH_PROMPT_DISABLED": "1"}

    def test_run_gh(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        body = (
            "import os, sys\nprint(os.environ.get('NO_COLOR'), *sys.argv[1:])\n"
            "sys.exit(3 if 'fail' in sys.argv else 0)\n"
        )
        _ = write_executable(tmp_path, "gh", body)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("CLICOLOR_FORCE", "1")
        assert gate.run_gh(["api", "x; rm -rf /"]) == "1 api x; rm -rf /\n"
        with pytest.raises(gate.GhError, match="失敗"):
            _ = gate.run_gh(["api", "fail"])

    def test_run_gh_times_out(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _ = write_executable(tmp_path, "gh", "import time\ntime.sleep(5)\n")
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setattr(gate, "GH_TIMEOUT", 0.2)
        with pytest.raises(gate.GhError, match="終わらなかった"):
            _ = gate.run_gh(["api", "slow"])

    def test_gh_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", no_gh)
        with pytest.raises(gate.GhError, match="gh"):
            _ = gate.run_gh(["api"])


class Clock:
    def __init__(self) -> None:
        self.now: float = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Sequenced:
    def __init__(self, steps: Sequence[FakeGitHub | None]) -> None:
        self.steps: list[FakeGitHub | None] = list(steps)
        self.current: FakeGitHub | None = None

    def __call__(self, args: Sequence[str]) -> str:
        if list(args[:2]) == ["api", "graphql"] and gate.PR_QUERY in args[3]:
            self.current = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if self.current is None:
            msg = "gh down"
            raise gate.GhError(msg)
        return self.current(args)


WAIT = gate.Options(wait=True, timeout=100, interval=30, allow_no_ci=False)
ONCE = gate.Options(wait=False, timeout=100, interval=30, allow_no_ci=False)


class TestPoll:
    def test_waits_until_ready(self) -> None:
        clock = Clock()
        runner = Sequenced([FakeGitHub(check_runs=GREEN_CI), FakeGitHub(reviews=[HEAD_REVIEW], check_runs=GREEN_CI)])
        result = gate.poll(runner, TARGET, WAIT, clock.sleep, clock)
        assert (result["verdict"], result["polls"], result["waited_seconds"]) == (gate.READY, 2, 30)

    def test_times_out_waiting(self) -> None:
        clock = Clock()
        result = gate.poll(Sequenced([FakeGitHub(check_runs=GREEN_CI)]), TARGET, WAIT, clock.sleep, clock)
        assert result["verdict"] == gate.WAITING
        assert result["polls"] == 4
        assert "100 秒" in reasons(result)

    def test_single_shot_does_not_wait(self) -> None:
        clock = Clock()
        result = gate.poll(Sequenced([FakeGitHub(check_runs=GREEN_CI)]), TARGET, ONCE, clock.sleep, clock)
        assert (result["verdict"], clock.sleeps) == (gate.WAITING, [])

    def test_retries_transient_gh_failures(self) -> None:
        clock = Clock()
        runner = Sequenced([None, FakeGitHub(reviews=[HEAD_REVIEW], check_runs=GREEN_CI)])
        assert gate.poll(runner, TARGET, WAIT, clock.sleep, clock)["verdict"] == gate.READY

    def test_gives_up_after_repeated_failures(self) -> None:
        clock = Clock()
        options = gate.Options(wait=True, timeout=1000, interval=1, allow_no_ci=False)
        with pytest.raises(gate.GhError):
            _ = gate.poll(Sequenced([None]), TARGET, options, clock.sleep, clock)

    def test_timeout_without_any_success(self) -> None:
        clock = Clock()
        with pytest.raises(gate.GhError, match="一度も"):
            _ = gate.poll(
                Sequenced([None]),
                TARGET,
                gate.Options(wait=True, timeout=10, interval=30, allow_no_ci=False),
                clock.sleep,
                clock,
            )

    def test_single_shot_failure_is_raised(self) -> None:
        clock = Clock()
        with pytest.raises(gate.GhError):
            _ = gate.poll(Sequenced([None]), TARGET, ONCE, clock.sleep, clock)


class TestCli:
    def test_exit_code_and_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        runner = FakeGitHub(threads=[thread("T1")], reviews=[HEAD_REVIEW], check_runs=GREEN_CI)
        assert gate.run(["kanade0404/skills", "1"], runner) == gate.EXIT_CODES[gate.ACTION_NEEDED]
        output = cast("dict[str, object]", json.loads(capsys.readouterr().out))
        assert output["verdict"] == gate.ACTION_NEEDED
        assert output["polls"] == 1

    def test_wait_flags(self, capsys: pytest.CaptureFixture[str]) -> None:
        clock = Clock()
        args = ["kanade0404/skills", "1", "--wait", "--timeout", "5", "--interval", "1", "--allow-no-ci"]
        assert gate.run(args, FakeGitHub(reviews=[HEAD_REVIEW]), clock.sleep) == 0
        assert '"READY"' in capsys.readouterr().out

    @pytest.mark.parametrize("args", [["bad repo", "1"], ["o/r", "0"], ["a/b/c", "1"]])
    def test_invalid_target(self, args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
        assert gate.run(args, FakeGitHub()) == gate.EXIT_ERROR
        assert '"ERROR"' in capsys.readouterr().out

    def test_main_reports_missing_gh(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(shutil, "which", no_gh)
        monkeypatch.setattr(sys, "argv", ["coderabbit_gate.py", "kanade0404/skills", "1"])
        assert gate.main() == gate.EXIT_ERROR
        assert "gh" in capsys.readouterr().out
