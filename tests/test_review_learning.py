from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import coderabbit_gate as gate
import review_learning as rl

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "review_learning"
TODAY = "2026-11-03"
TARGET = gate.Target(owner="kanade0404", name="skills", number=190)
# #190 の 4d7fe4c(CodeRabbit がレビューしていない)と、CodeRabbit がレビューした 050c3e7。
UNREVIEWED = "4d7fe4c1489eee2067988e2289eb16ff721a7cb0"
REVIEWED = "050c3e7c25774f7baa17bbd99275bb2940bd95c7"
GATE_FILE = "plugins/productivity/scripts/coderabbit_gate.py"


def real(name: str) -> dict[str, object]:
    # kanade0404/skills の #190・#191 を review_learning.fetch で取った実データ(URL の scope は伏せた)。
    return gate.as_dict(rl.load_json((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))


def snapshot(name: str) -> gate.Snapshot:
    data = real(name)
    return gate.Snapshot(
        pull=gate.as_dict(data["pull"]),
        threads=gate.as_list(data["threads"]),
        reviews=gate.as_list(data["reviews"]),
        comments=gate.as_list(data["comments"]),
        check_runs=[],
        combined_status={},
    )


class Replay:
    # 実データを GraphQL の応答として返す、偽の gh。
    def __init__(self, data: dict[str, object]) -> None:
        self.data: dict[str, object] = data

    def __call__(self, args: Sequence[str]) -> str:
        query = next(arg.partition("=")[2] for arg in args if arg.startswith("query="))
        if query == gate.PR_QUERY:
            pull: object = self.data["pull"]
        else:
            key = {rl.THREADS_QUERY: "threads", gate.REVIEWS_QUERY: "reviews", gate.COMMENTS_QUERY: "comments"}[query]
            pull = {"page": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": self.data[key]}}
        return json.dumps({"data": {"repository": {"pullRequest": pull}}})


def context(pr: int = 190, catalog: rl.Catalog | None = None) -> rl.Context:
    return rl.Context(repo="kanade0404/skills", pr=pr, today=TODAY, catalog=catalog or rl.Catalog())


def record(
    identifier: str = "coderabbit:a", cls: str | None = "unverified-comment-author", **changes: object
) -> rl.Record:
    base = rl.Record(
        id=identifier,
        source="coderabbit",
        repo="kanade0404/skills",
        pr=190,
        head_sha=REVIEWED,
        path=GATE_FILE,
        line=100,
        claim="claim",
        recorded_at=TODAY,
        cls="unverified-comment-author",
        outcome="fixed",
        fix_commit=None,
        missed_by_coderabbit=False,
    )
    return rl.record_from({**base.to_json(), "class": cls, **changes})


def local(cls: str | None = "unverified-comment-author", **changes: object) -> rl.Record:
    base = {"id": "local:security-guidance:x", "source": "local:security-guidance", "coderabbit_reviewed_head": True}
    return rl.record_from({**record(cls=cls).to_json(), **base, **changes})


def finding(**changes: object) -> dict[str, object]:
    return {
        "path": GATE_FILE,
        "line": 168,
        "class": "prefix-match-identity",
        "claim": "is_coderabbit が前方一致で CodeRabbit を見分ける",
        "outcome": "fixed",
        "fix_commit": REVIEWED,
        **changes,
    }


def local_input(head: str = UNREVIEWED, tool: str = "security-guidance", **changes: object) -> dict[str, object]:
    return {"version": 1, "tool": tool, "head_sha": head, "findings": [finding()], **changes}


def rule(**changes: object) -> dict[str, object]:
    return {
        "id": "prefix-match-identity",
        "class": "prefix-match-identity",
        "level": "ast-grep",
        "enforcer": "harness/ast-grep/review-rules/prefix-match-identity.yml",
        "scope": "**/*.py",
        "evidence": {"records": ["coderabbit:a"], "prs": [190], "commits": [UNREVIEWED], "fixtures": ["t.yml"]},
        "reason": "重大度",
        **changes,
    }


PATH_RULE = rule(
    id="author",
    **{"class": "unverified-comment-author"},
    level="path_instructions",
    enforcer=".coderabbit.yaml",
    scope="plugins/**/scripts/*.py",
    instructions="著者を確かめる",
)
CHECK_RULE = rule(
    id="check",
    level="custom_check",
    enforcer=".coderabbit.yaml",
    name="Author check",
    mode="warning",
    instructions="著者を確かめる | 本文",
)


def write_repo(root: Path, rules: Sequence[dict[str, object]] = (), records: Sequence[rl.Record] = ()) -> Path:
    # 規則、記録、.coderabbit.yaml、表、ast-grep の規則を持つ小さなリポジトリを作る。
    (root / "harness" / "ast-grep" / "review-rules").mkdir(parents=True)
    (root / "plugins" / "p" / "scripts").mkdir(parents=True)
    _ = (root / "plugins" / "p" / "scripts" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _ = (root / "harness" / "ast-grep" / "review-rules" / "prefix-match-identity.yml").write_text("id: x\n")
    _ = (root / "t.yml").write_text("id: x\n", encoding="utf-8")
    catalog = {"version": 1, "classes": {"prefix-match-identity": {"description": "d", "patterns": ["前方一致"]}}}
    _ = (root / rl.RULES).write_text(json.dumps({**catalog, "rules": list(rules)}, ensure_ascii=False))
    _ = (root / rl.CONFIG).write_text(f"reviews:\n  {rl.BEGIN}\n  {rl.END}\n  tools: {{}}\n", encoding="utf-8")
    _ = (root / rl.TABLE).write_text(f"# t\n\n{rl.TABLE_BEGIN}\n{rl.TABLE_END}\n", encoding="utf-8")
    _ = rl.append(root / rl.STORE, {}, records)
    return root


def output(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return gate.as_dict(rl.load_json(capsys.readouterr().out))


def test_repository_is_in_sync() -> None:
    # .coderabbit.yaml と harness/review-rules.md の印の間が harness/review-rules.json と一致する。
    # 規則の証拠の id が記録にあることも確かめる。
    store = rl.load_store(ROOT / rl.STORE)
    assert rl.sync(ROOT, rl.load_catalog(ROOT / rl.RULES), store, write=False) == []


def test_repository_rules_are_not_stale() -> None:
    store = rl.load_store(ROOT / rl.STORE)
    assert rl.stale(ROOT, rl.load_catalog(ROOT / rl.RULES), store)["stale"] == []


class TestStore:
    def test_round_trip_and_last_line_wins(self, tmp_path: Path) -> None:
        path = tmp_path / "s.jsonl"
        store: dict[str, rl.Record] = {}
        first = record()
        assert rl.append(path, store, [first]) == [first]
        # 中身が同じなら、日付が違っても行を増やさない。
        assert rl.append(path, store, [replace(first, recorded_at="2027-01-01")]) == []
        second = replace(first, cls="other")
        assert rl.append(path, store, [second]) == [second]
        _ = path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        assert rl.load_store(path) == {first.id: second}
        assert len(path.read_text(encoding="utf-8").split()) >= 2

    def test_missing_store(self, tmp_path: Path) -> None:
        assert rl.load_store(tmp_path / "none.jsonl") == {}

    def test_broken_line(self, tmp_path: Path) -> None:
        path = tmp_path / "s.jsonl"
        _ = path.write_text("{\n", encoding="utf-8")
        with pytest.raises(rl.LearningError, match=r"s\.jsonl:1"):
            _ = rl.load_store(path)

    def test_to_json_uses_class(self) -> None:
        data = record().to_json()
        assert data["class"] == "unverified-comment-author"
        assert "cls" not in data
        assert rl.record_from({"line": True, "class": "", "missed_by_coderabbit": "yes"}).line is None


class TestCatalog:
    def test_missing(self, tmp_path: Path) -> None:
        assert rl.load_catalog(tmp_path / "none.json") == rl.Catalog()

    def test_repository_catalog_classifies_real_titles(self) -> None:
        catalog = rl.load_catalog(ROOT / rl.RULES)
        assert catalog.classify("Count only CodeRabbit-authored thread comments as inline ids.") == (
            "unverified-comment-author"
        )
        assert catalog.classify("Add a timeout to the `gh` subprocess call.") == "subprocess-no-timeout"
        assert catalog.classify("Split `TestCodeRabbitState` to restore passing lint.") is None
        assert [rule.id for rule in catalog.rules] == ["prefix-match-identity", "unverified-comment-author"]


class TestOutcome:
    @pytest.mark.parametrize(
        ("texts", "expected"),
        [
            # #186 で見送った返信と、#188・#191 の返信の書き方。
            (["この指摘は取り込まない。拒否の規則は文字列の一致なので"], ("declined", None)),
            (
                ["対応済み。9d454a3 で決定 1 の要件ごとの受け入れテストを完了条件に追加している。"],
                ("already-fixed", "9d454a3"),
            ),
            (["49fcc8f で修正した。master、ruleset などの具体名を外した。"], ("fixed", "49fcc8f")),
            (["cr-comment:v1:58f8fb1ea0cb819ca6c0b395 直した。facade の decade"], (None, None)),
            ([], (None, None)),
        ],
    )
    def test_outcome_of(self, texts: list[str], expected: tuple[str | None, str | None]) -> None:
        assert rl.outcome_of(texts) == expected

    def test_segment_for(self) -> None:
        body = "cr-comment:v1:aaaaaaaa 対応済み 1234567\ncr-comment:v1:bbbbbbbb 見送る"
        assert "対応済み" in rl.segment_for(body, "aaaaaaaa")
        assert "見送る" not in rl.segment_for(body, "aaaaaaaa")
        assert "見送る" in rl.segment_for(body, "bbbbbbbb")
        assert not rl.segment_for(body, "cccccccc")


class TestCodeRabbitRecords:
    def test_real_threads_of_190(self) -> None:
        catalog = rl.load_catalog(ROOT / rl.RULES)
        records = {r.id: r for r in rl.thread_records(snapshot("pr190"), context(catalog=catalog))}
        author = records["coderabbit:33569afdf8224158cd71ff17"]
        assert (author.cls, author.outcome, author.fix_commit) == ("unverified-comment-author", "fixed", "c570b18")
        assert (author.path, author.head_sha) == (GATE_FILE, REVIEWED)
        assert len(records) == 4
        assert all(r.missed_by_coderabbit is False for r in records.values())

    def test_real_body_finding_of_191(self) -> None:
        records = rl.body_records(snapshot("pr191"), context(191))
        assert [(r.id, r.outcome, r.fix_commit, r.line) for r in records] == [
            ("coderabbit:58f8fb1ea0cb819ca6c0b395", "fixed", "49fcc8f", 72)
        ]

    def test_thread_variants(self) -> None:
        def node(login: str, body: str, association: str = "OWNER") -> dict[str, object]:
            typename = "Bot" if login == "coderabbitai" else "User"
            return {"author": {"login": login, "__typename": typename}, "authorAssociation": association, "body": body}

        coderabbit = {**node("coderabbitai", "**Fix it.**", "NONE"), "databaseId": 5}
        threads: list[object] = [
            # id の無いスレッドは comment の id で追う。見送りは書き込める人の返信からだけ読む。
            {
                "path": "a.py",
                "line": None,
                "originalLine": 3,
                "first": {"nodes": [coderabbit]},
                "all": {"nodes": [coderabbit, node("evil", "1234567 で直した", "NONE"), node("kanade0404", "見送る")]},
            },
            # CodeRabbit 以外が始めたスレッドは取り込まない。
            {"path": "a.py", "first": {"nodes": [node("evil", "cr-comment:v1:abcdef12")]}, "all": {"nodes": []}},
        ]
        snap = replace(snapshot("pr190"), threads=threads)
        records = rl.thread_records(snap, context())
        assert [(r.id, r.line, r.outcome) for r in records] == [("coderabbit:comment-5", 3, "declined")]
        # CodeRabbit 以外のスレッドに書いた id では、本文の指摘を行の指摘に見せかけられない。
        assert len(rl.body_records(snap, context())) == len(rl.body_records(snapshot("pr190"), context()))

    def test_duplicate_body_finding(self) -> None:
        snap = snapshot("pr191")
        review = next(r for r in gate.coderabbit_reviews(snap) if "58f8fb1e" in gate.text(r.get("body")))
        doubled = replace(snap, reviews=[*snap.reviews, {**review, "databaseId": 1}])
        assert len(rl.body_records(doubled, context(191))) == 1

    def test_keep_class(self) -> None:
        old = record(outcome="declined", fix_commit=None)
        new = record(cls=None, outcome="fixed", fix_commit="abc1234")
        kept = rl.keep_class(new, {old.id: old})
        assert (kept.cls, kept.outcome, kept.fix_commit) == ("unverified-comment-author", "declined", "abc1234")
        assert rl.keep_class(new, {}) is new


class TestMiss:
    @pytest.mark.parametrize(
        ("left", "right", "expected"), [(None, 3, True), (3, None, True), (1, 11, True), (1, 12, False)]
    )
    def test_near(self, left: int | None, right: int | None, *, expected: bool) -> None:
        assert rl.near(left, right) is expected

    def test_missed(self) -> None:
        coderabbit = [record(line=105)]
        assert rl.missed(local(), coderabbit) is False
        assert rl.missed(local(line=200), coderabbit) is True
        assert rl.missed(local(path="b.py"), coderabbit) is True
        assert rl.missed(local(cls="other"), coderabbit) is True
        assert rl.missed(local(), [record(pr=1)]) is True
        assert rl.missed(local(), [local()]) is True
        # head をレビューしていない、または種類が無いなら、見逃しかは分からない。
        assert rl.missed(local(coderabbit_reviewed_head=False), []) is None
        assert rl.missed(local(cls=None), []) is None

    def test_reviewed_uses_commit_evidence(self) -> None:
        snap = snapshot("pr190")
        assert rl.reviewed(snap, REVIEWED) is True
        assert rl.reviewed(snap, UNREVIEWED) is False
        assert rl.reviewed(snap, snap.head) is True

    def test_refresh_misses(self) -> None:
        far = replace(local(line=500), id="local:security-guidance:y")
        store = {r.id: r for r in [record(line=105), local(line=100), far]}
        changed = rl.refresh_misses(store, "2027-01-01")
        assert [(r.id, r.missed_by_coderabbit, r.recorded_at) for r in changed] == [(far.id, True, "2027-01-01")]
        store.update({r.id: r for r in changed})
        assert rl.refresh_misses(store, TODAY) == []


class TestLocal:
    def test_real_security_guidance_finding_of_190(self) -> None:
        # security-guidance が 4d7fe4c で見つけた前方一致。CodeRabbit は 4d7fe4c をレビューしていないので見逃しは null。
        records = rl.local_records(local_input(), snapshot("pr190"), context())
        assert len(records) == 1
        found = records[0]
        assert found.id.startswith("local:security-guidance:")
        assert (found.coderabbit_reviewed_head, found.missed_by_coderabbit) == (False, None)
        assert (found.cls, found.outcome, found.fix_commit) == ("prefix-match-identity", "fixed", REVIEWED)

    def test_human_and_catalog_class(self) -> None:
        catalog = rl.load_catalog(ROOT / rl.RULES)
        raw = local_input(REVIEWED, "human", findings=[finding(**{"class": None}, line=None, note="n")])
        found = rl.local_records(raw, snapshot("pr190"), context(catalog=catalog))[0]
        assert (found.source, found.cls, found.coderabbit_reviewed_head, found.note) == (
            "human",
            "prefix-match-identity",
            True,
            "n",
        )

    @pytest.mark.parametrize(
        ("changes", "message"),
        [
            ({"version": 2}, "version"),
            ({"tool": "Bad Tool"}, "tool"),
            ({"head_sha": "4d7fe4c"}, "head_sha"),
            ({"findings": [finding(path="")]}, "path"),
            ({"findings": [finding(claim="")]}, "claim"),
            ({"findings": [finding(line=0)]}, "line"),
            ({"findings": [finding(**{"class": "Bad Class"})]}, "class"),
            ({"findings": [finding(outcome="maybe")]}, "outcome"),
            ({"findings": [finding(fix_commit="main")]}, "fix_commit"),
        ],
    )
    def test_rejects_bad_input(self, changes: dict[str, object], message: str) -> None:
        with pytest.raises(rl.LearningError, match=message):
            _ = rl.parse_local({**local_input(), **changes})


class TestReport:
    def test_counts_candidates_misses_and_noise(self) -> None:
        catalog = rl.Catalog(rules=[rl.Rule("r", "noisy", "docs", "", "*", {}, "x")])
        store = {
            r.id: r
            for r in [
                record("coderabbit:1", cls="twice"),
                record("coderabbit:2", cls="twice", outcome=None),
                record("coderabbit:3", cls="noisy", outcome="declined"),
                record("coderabbit:4", cls="noisy"),
                local(cls="missed", missed_by_coderabbit=True),
                record("coderabbit:5", cls=None),
            ]
        }
        result = rl.report(store, catalog)
        assert result["promote"] == ["twice"]
        assert result["missed"] == ["missed"]
        assert result["noisy"] == ["noisy"]
        assert result["unclassified"] == ["coderabbit:5"]
        rows = {gate.text(row["class"]): row for row in map(gate.as_dict, gate.as_list(result["classes"]))}
        assert rows["twice"]["noise_rate"] == pytest.approx(0.0)
        assert result["by_source"] == {"coderabbit": 5, "local:security-guidance": 1}

    def test_seeded_store(self) -> None:
        # #186〜#191 から取り込んだ記録。2 件以上の種類と、昇格させた規則を確かめる。
        result = rl.report(rl.load_store(ROOT / rl.STORE), rl.load_catalog(ROOT / rl.RULES))
        assert "unverified-comment-author" not in gate.as_list(result["promote"])
        assert "adr-unspecified-contract" in gate.as_list(result["promote"])


class TestStale:
    def test_flags_each_reason(self, tmp_path: Path) -> None:
        root = write_repo(tmp_path)
        rules = [
            rl.Rule("gone", "c", "ast-grep", "missing.yml", "**/*.go", {}, "x", superseded_by="型"),
            rl.Rule("noisy", "noisy", "path_instructions", ".coderabbit.yaml", "plugins/**/scripts/*.py", {}, "x"),
            rl.Rule("ok", "c", "path_instructions", ".coderabbit.yaml", "**/*.py", {}, "x"),
        ]
        store = {r.id: r for r in [record(cls="noisy", outcome="declined")]}
        result = rl.stale(root, rl.Catalog(rules=rules), store)
        reasons = {
            gate.text(gate.as_dict(item)["rule"]): gate.as_list(gate.as_dict(item)["reasons"])
            for item in gate.as_list(result["stale"])
        }
        assert len(reasons["gone"]) == 3
        assert len(reasons["noisy"]) == 1
        assert "ok" not in reasons

    def test_repo_files_skips_dependencies(self, tmp_path: Path) -> None:
        (tmp_path / "node_modules" / "x").mkdir(parents=True)
        _ = (tmp_path / "node_modules" / "x" / "a.py").write_text("")
        _ = (tmp_path / "b.py").write_text("")
        assert rl.repo_files(tmp_path) == ["b.py"]
        assert rl.matches_scope("**/*.py", ["b.py"])
        assert not rl.matches_scope("plugins/**/scripts/*.py", ["b.py"])


class TestSync:
    def test_writes_blocks(self, tmp_path: Path) -> None:
        root = write_repo(tmp_path, [rule(), PATH_RULE, CHECK_RULE], [record()])
        catalog = rl.load_catalog(root / rl.RULES)
        store = rl.load_store(root / rl.STORE)
        assert rl.sync(root, catalog, store, write=False) == [".coderabbit.yaml", "harness/review-rules.md"]
        assert rl.sync(root, catalog, store, write=True) == [".coderabbit.yaml", "harness/review-rules.md"]
        assert rl.sync(root, catalog, store, write=False) == []
        config = (root / rl.CONFIG).read_text(encoding="utf-8")
        assert '    - path: "plugins/**/scripts/*.py"' in config
        assert '      - name: "Author check"' in config
        assert "#190, `4d7fe4c`, `coderabbit:a`" in (root / rl.TABLE).read_text(encoding="utf-8")
        assert "著者を確かめる \\| 本文" not in (root / rl.TABLE).read_text(encoding="utf-8")

    def test_empty_blocks(self) -> None:
        assert rl.yaml_block(rl.Catalog()) == ["path_instructions: []", "pre_merge_checks:", "  custom_checks: []"]
        assert rl.cell("a|b\nc") == "a\\|b c"

    def test_missing_markers(self) -> None:
        with pytest.raises(rl.LearningError, match="印が無い"):
            _ = rl.render("x", [], rl.BEGIN, rl.END, "f")

    @pytest.mark.parametrize(
        ("rules", "message"),
        [
            ([rule(), rule()], "重複"),
            ([rule(id=f"p{i}", level="path_instructions", instructions="x") for i in range(6)], "path_instructions は"),
            (
                [rule(id=f"c{i}", level="custom_check", name="n", mode="off", instructions="x") for i in range(4)],
                "custom_checks は",
            ),
            ([rule(level="lint")], "level"),
            ([rule(reason="")], "reason"),
            ([rule(evidence={})], "evidence.records が空"),
            ([rule(evidence={"records": ["coderabbit:none"]})], "記録に無い id"),
            ([rule(evidence={"records": ["coderabbit:a"], "fixtures": ["none.yml"]})], "fixture"),
            ([rule(level="path_instructions", instructions="x" * 1501)], "instructions の長さ"),
            ([rule(level="custom_check", name="x" * 51, mode="off", instructions="x")], "name"),
            ([rule(level="custom_check", name="n", mode="block", instructions="x")], "mode"),
            ([rule(level="custom_check", name="n", mode="off", instructions="")], "instructions の長さ"),
            ([rule(enforcer="elsewhere/x.yml")], "に置く"),
            ([rule(enforcer="harness/ast-grep/review-rules/none.yml")], "が無い"),
            ([], "載っていない"),
        ],
    )
    def test_validate_rejects(self, tmp_path: Path, rules: list[dict[str, object]], message: str) -> None:
        root = write_repo(tmp_path, rules, [record()])
        with pytest.raises(rl.LearningError, match=message):
            rl.validate(rl.load_catalog(root / rl.RULES), root, rl.load_store(root / rl.STORE))


class TestRun:
    def test_capture_coderabbit(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = write_repo(tmp_path)
        assert (
            rl.run(
                ["--root", str(root), "capture-coderabbit", "kanade0404/skills", "190"],
                Replay(real("pr190")),
                lambda: TODAY,
            )
            == 0
        )
        result = output(capsys)
        assert result["found"] == 4
        assert len(gate.as_list(result["appended"])) == 4
        assert result["coderabbit_reviewed_head"] is True
        # 同じ PR をもう一度取り込んでも、行は増えない。
        assert (
            rl.run(
                ["--root", str(root), "capture-coderabbit", "kanade0404/skills", "190"],
                Replay(real("pr190")),
                lambda: TODAY,
            )
            == 0
        )
        assert output(capsys)["appended"] == []

    def test_capture_local_and_amend(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = write_repo(tmp_path)
        # このリポジトリの種類の定義を使い、CodeRabbit の指摘に種類を付ける。
        classes = rl.load_json((ROOT / rl.RULES).read_text(encoding="utf-8"))
        _ = (root / rl.RULES).write_text(json.dumps({**gate.as_dict(classes), "rules": []}, ensure_ascii=False))
        source = tmp_path / "findings.json"
        _ = source.write_text(
            json.dumps(
                local_input(
                    REVIEWED, findings=[finding(path=GATE_FILE, line=640, **{"class": "unverified-comment-author"})]
                )
            )
        )
        args = ["--root", str(root), "capture-local", "kanade0404/skills", "190", "--input", str(source)]
        assert rl.run(args, Replay(real("pr190")), lambda: TODAY) == 0
        result = output(capsys)
        local_id = next(i for i in map(gate.text, gate.as_list(result["appended"])) if i.startswith("local:"))
        # CodeRabbit は 050c3e7 をレビューし、649 行目に同じ種類の指摘を出していたので、見逃しではない。
        assert result["missed_by_coderabbit"] == []
        assert rl.load_store(root / rl.STORE)[local_id].missed_by_coderabbit is False
        # 種類を直すと見逃しになり、判定し直した行が追記される。
        amendments = tmp_path / "amend.json"
        _ = amendments.write_text(json.dumps({local_id: {"class": "other", "outcome": "declined"}}))
        assert (
            rl.run(
                ["--root", str(root), "amend", "--input", str(amendments)], Replay(real("pr190")), lambda: "2027-01-01"
            )
            == 0
        )
        assert output(capsys)["appended"] == [local_id, local_id]
        amended = rl.load_store(root / rl.STORE)[local_id]
        assert (amended.cls, amended.outcome, amended.missed_by_coderabbit) == ("other", "declined", True)

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ({"cls": "x"}, "直せない"),
            ("Bad", "class"),
            ({"outcome": "maybe"}, "outcome"),
            ({"fix_commit": "main"}, "fix_commit"),
        ],
    )
    def test_amend_rejects(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], value: object, message: str
    ) -> None:
        root = write_repo(tmp_path, records=[record()])
        amendments = tmp_path / "amend.json"
        _ = amendments.write_text(json.dumps({"coderabbit:a": value, "coderabbit:none": "x"}))
        assert rl.run(["--root", str(root), "amend", "--input", str(amendments)]) == 1
        assert message in gate.text(output(capsys)["error"])

    def test_amend_unknown_id(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = write_repo(tmp_path)
        amendments = tmp_path / "amend.json"
        _ = amendments.write_text(json.dumps({"coderabbit:none": "x"}))
        assert rl.run(["--root", str(root), "amend", "--input", str(amendments)]) == 1
        assert "記録に無い id" in gate.text(output(capsys)["error"])

    def test_capture_local_rejects_before_fetch(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        def no_gh(_: Sequence[str]) -> str:
            raise AssertionError

        source = tmp_path / "findings.json"
        _ = source.write_text(json.dumps({"version": 1}))
        args = [
            "--root",
            str(write_repo(tmp_path)),
            "capture-local",
            "kanade0404/skills",
            "190",
            "--input",
            str(source),
        ]
        assert rl.run(args, no_gh) == 1
        assert "tool" in gate.text(output(capsys)["error"])

    def test_report_stale_sync(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = write_repo(tmp_path, [rule()], [record()])
        assert rl.run(["--root", str(root), "report"]) == 0
        assert output(capsys)["records"] == 1
        assert rl.run(["--root", str(root), "sync", "--check"]) == 1
        assert output(capsys)["stale_files"] == [".coderabbit.yaml", "harness/review-rules.md"]
        assert rl.run(["--root", str(root), "sync"]) == 0
        _ = output(capsys)
        assert rl.run(["--root", str(root), "sync", "--check"]) == 0
        _ = output(capsys)
        assert rl.run(["--root", str(root), "stale"]) == 0
        _ = output(capsys)
        (root / "t.yml").unlink()
        (root / "harness" / "ast-grep" / "review-rules" / "prefix-match-identity.yml").unlink()
        assert rl.run(["--root", str(root), "stale"]) == 1
        assert gate.as_list(output(capsys)["stale"])

    def test_bad_repo(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert rl.run(["--root", str(tmp_path), "capture-coderabbit", "not a repo", "1"]) == 1
        assert "owner/repo" in gate.text(output(capsys)["error"])


def test_today() -> None:
    assert len(rl.today()) == len(TODAY)


def test_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["review_learning.py", "--root", str(tmp_path), "report"])
    assert rl.main() == 0
    assert output(capsys)["records"] == 0
