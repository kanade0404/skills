#!/usr/bin/env python3
# ruff: noqa: T201, INP001
"""skill-improver の改善台帳 (improvements/ledger.jsonl) を読み書きする。

stdlib のみ。`uv run python3 skills/skill-improver/scripts/ledger.py <subcommand>` で動く
(追加の依存も仮想環境も要らない)。

台帳は 1 行 1 JSON オブジェクトの JSON Lines。スキーマは
skills/skill-improver/references/ledger.md が canonical。

exit code 契約 (相互排他):
  0 = 成功 / check-target が改善対象と判定
  1 = 検査で不合格 (check-target が未知の skill / skill ディレクトリを解決できない、
      report で revert candidate を検出し --fail-on-revert を付けた場合)
  2 = 対象が **メタスキル** (改善対象外)。check-target の判定に加え、add の記録時と
      set-status / link-pr / record-metrics の書き込みガードでも返す
注意: argparse は usage エラーでも 2 を返す。呼出側は stdout 1 行目の機械可読な
`classification: <ok|unknown|excluded_meta|unresolved>` で曖昧さを解消すること
(usage エラー時はこの行が出ない)。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Sequence

# --- 定数 (schema) -------------------------------------------------------

#: 改善対象にしないメタスキル。second-order な効果指標が取れず、
#: 自己改変の失敗が下流の全改善に伝播するため (SKILL.md「メタスキル除外」節)。
META_SKILLS: frozenset[str] = frozenset(
    {
        "retro",
        "session-retro",
        "skill-builder",
        "empirical-prompt-tuning",
        "skill-improver",
        "model-policy",
        "harness-distribution",
        "rulesync-sync",
    }
)

SOURCES: tuple[str, ...] = ("retro", "session-retro", "agent-feedback", "trigger-eval")
LEVERS: tuple[str, ...] = ("skill-edit", "ept", "trigger")
STATUSES: tuple[str, ...] = (
    "proposed",
    "pr_open",
    "merged",
    "rejected",
    "excluded_meta",
    "reverted",
)
METRIC_KEYS: tuple[str, ...] = (
    "trigger_f1",
    "ci_fix_iterations",
    "review_cycles",
    "escalations",
)
#: 値が大きいほど良い指標。これ以外は小さいほど良い。
HIGHER_IS_BETTER: frozenset[str] = frozenset({"trigger_f1"})

SKILL_DIR_CANDIDATES: tuple[str, ...] = ("skills", ".claude/skills", ".agents/skills")
LEDGER_RELPATH = Path("improvements") / "ledger.jsonl"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_META = 2

ID_RE = re.compile(r"^IMP-(\d{4,})$")


# --- pure functions (単体テスト対象) --------------------------------------


def normalize_skill_name(skill: str) -> str:
    """skill 指定を比較用の名前に正規化する (純関数)。

    `skills/retro` や `Retro` のような書き方でメタスキル判定をすり抜けられると
    除外リストが飾りになるため、パス末尾の要素だけを取り出して casefold する。
    """
    return Path(skill.strip()).name.strip().casefold()


def classify_target(skill: str, known_skills: Iterable[str] | None) -> str:
    """改善対象 skill を分類する。

    戻り値: "excluded_meta" | "unknown" | "unresolved" | "ok"
    known_skills が None のときは skill ディレクトリを解決できなかった状態を表し
    "unresolved" を返す (存在しない skill を「あった」と誤認しない fail-closed)。
    メタスキル判定は known_skills に依存せず、表記揺れ (`Retro` / `skills/retro`) も
    同じメタスキルとして弾く (ハードコードされた不変条件)。
    """
    if normalize_skill_name(skill) in META_SKILLS:
        return "excluded_meta"
    if known_skills is None:
        return "unresolved"
    return "ok" if skill in set(known_skills) else "unknown"


def _is_number(value: Any) -> bool:
    """bool を数値として扱わない (True/False が指標値として通ると delta が無意味になる)。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def metric_deltas(
    before: dict[str, Any] | None, after: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """before/after 双方に値がある指標だけ delta と verdict を返す (純関数)。"""
    before = before or {}
    after = after or {}
    rows: list[dict[str, Any]] = []
    for key in METRIC_KEYS:
        b = before.get(key)
        a = after.get(key)
        if not _is_number(b) or not _is_number(a):
            continue
        delta = a - b
        if delta == 0:
            verdict = "unchanged"
        elif (delta > 0) == (key in HIGHER_IS_BETTER):
            verdict = "improved"
        else:
            verdict = "worse"
        rows.append(
            {"metric": key, "before": b, "after": a, "delta": delta, "verdict": verdict}
        )
    return rows


def fmt_delta(value: float | int) -> str:
    """delta を表示用に整形する (float の丸め誤差をレポートに漏らさない)。"""
    if isinstance(value, int):
        return f"{value:+d}"
    return f"{round(value, 6):+g}"


def worse_metrics(entry: dict[str, Any]) -> list[str]:
    """after が before より悪化した指標名を返す (純関数)。"""
    return [
        row["metric"]
        for row in metric_deltas(entry.get("before"), entry.get("after"))
        if row["verdict"] == "worse"
    ]


def is_revert_candidate(entry: dict[str, Any]) -> bool:
    """1 つでも指標が悪化していれば revert candidate (純関数)。"""
    return bool(worse_metrics(entry))


def finding_class(finding: str) -> str:
    """finding を再発判定用のクラスキーに正規化する (純関数)。

    NFKC 正規化 + casefold の上で、空白と記号をすべて落とす。吸収できるのは
    **表記揺れだけ** — `3 連続失敗` と `3連続失敗`、大文字小文字、句読点の有無まで。
    語順や言い回しが変われば別クラスになる: 日本語には語境界が無く、文字列一致で
    「意味として同じ finding か」を決めることはできないため、ここで無理に寄せると
    無関係な finding を同じクラスに畳んで recurrence を水増しする方が害が大きい。
    再発かどうかの判断は agent が `report --skill <skill>` の出力を読んで下し、
    `add --class <key>` で明示する (SKILL.md Step 2 ゲート 3)。
    """
    normalized = unicodedata.normalize("NFKC", finding).casefold()
    return "".join(ch for ch in normalized if ch.isalnum())


def entry_class_key(entry: dict[str, Any]) -> str:
    """台帳エントリの再発クラスキーを返す (純関数)。

    agent が明示した `finding_class` を最優先し、無ければ finding 本文の正規化に落とす。
    """
    explicit = entry.get("finding_class")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return finding_class(str(entry.get("finding", "")))


def recurrence_for(
    entries: Sequence[dict[str, Any]],
    target_skill: str,
    finding: str,
    class_key: str | None = None,
) -> int:
    """同一 target_skill × 同一 finding クラスの通算回数 (今回分を含む、純関数)。

    class_key を渡すと、テキストの正規化ではなく agent が明示したクラスで数える。
    """
    key = (class_key or "").strip() or finding_class(finding)
    prior = sum(
        1
        for entry in entries
        if entry.get("target_skill") == target_skill and entry_class_key(entry) == key
    )
    return prior + 1


def next_id(entries: Sequence[dict[str, Any]]) -> str:
    """既存 id の最大値 + 1 を IMP-NNNN 形式で返す (純関数)。"""
    highest = 0
    for entry in entries:
        match = ID_RE.match(str(entry.get("id", "")))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"IMP-{highest + 1:04d}"


def recurrence_summary(entries: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """skill 別の件数・status 内訳・finding クラス別再発回数を返す (純関数)。"""
    summary: dict[str, dict[str, Any]] = {}
    for entry in entries:
        skill = str(entry.get("target_skill", "?"))
        bucket = summary.setdefault(
            skill, {"entries": 0, "statuses": {}, "classes": {}, "max_recurrence": 0}
        )
        bucket["entries"] += 1
        status = str(entry.get("status", "?"))
        bucket["statuses"][status] = bucket["statuses"].get(status, 0) + 1
        key = entry_class_key(entry)
        bucket["classes"][key] = bucket["classes"].get(key, 0) + 1
        counted = bucket["classes"][key]
        # 台帳から数え直した件数を正とする (手書き・古い recurrence を信用しない)。
        # 保存値は数え直しより大きいときだけ採る tiebreak — 過去分を剪定した台帳でも
        # 「通算何回目か」を過小評価しないため。
        stored = entry.get("recurrence")
        observed = stored if _is_number(stored) and stored > counted else counted
        bucket["max_recurrence"] = max(bucket["max_recurrence"], observed)
    return summary


def new_entry(
    *,
    entry_id: str,
    created: str,
    source: str,
    evidence: Sequence[str],
    target_skill: str,
    finding: str,
    lever: str,
    status: str,
    recurrence: int,
    class_key: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """台帳 1 行分のオブジェクトを組み立てる (純関数。キー順も固定する)。"""
    return {
        "id": entry_id,
        "created": created,
        "source": source,
        "evidence": list(evidence),
        "target_skill": target_skill,
        "finding": finding,
        "finding_class": class_key.strip(),
        "lever": lever,
        "status": status,
        "pr": None,
        "before": {},
        "after": {},
        "recurrence": recurrence,
        "notes": notes,
    }


def parse_metric(pair: str) -> tuple[str, float | int]:
    """`key=value` を検証して (key, 数値) に変換する (純関数)。"""
    key, sep, raw = pair.partition("=")
    key = key.strip()
    raw = raw.strip()
    if not sep or not raw:
        raise ValueError(f"metric は key=value 形式で指定する: {pair!r}")
    if key not in METRIC_KEYS:
        raise ValueError(f"未知の metric key {key!r} (使えるのは {', '.join(METRIC_KEYS)})")
    if re.fullmatch(r"[+-]?\d+", raw):
        return key, int(raw)
    try:
        return key, float(raw)
    except ValueError as exc:
        raise ValueError(f"metric {key} の値が数値でない: {raw!r}") from exc


def parse_metrics(pairs: Iterable[str]) -> dict[str, float | int]:
    """`key=value` の並びを metric オブジェクトへ (純関数)。"""
    return dict(parse_metric(pair) for pair in pairs)


# --- I/O -----------------------------------------------------------------


def find_repo_root(start: Path | None = None) -> Path:
    """cwd から上に向かって .git を探す。無ければスクリプト位置から推定する。"""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return Path(__file__).resolve().parents[3]


def discover_skills(root: Path) -> set[str] | None:
    """既知 skill 名を収集する。ディレクトリが 1 つも無ければ None (unresolved)。"""
    names: set[str] = set()
    found_dir = False
    for rel in SKILL_DIR_CANDIDATES:
        skills_dir = root / rel
        if not skills_dir.is_dir():
            continue
        found_dir = True
        for child in sorted(skills_dir.iterdir()):
            if (child / "SKILL.md").is_file():
                names.add(child.name)
    return names if found_dir else None


def load_entries(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    # splitlines() は U+2028 / U+2029 / U+0085 でも行を割る。finding 本文にそれらが
    # 混ざると 1 エントリが 2 行に割れて台帳全体が読めなくなるため "\n" だけで割る。
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} が JSON として読めない: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"{path}:{lineno} はオブジェクトでなければならない")
        entries.append(obj)
    return entries


def save_entries(path: Path, entries: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        json.dumps(entry, ensure_ascii=False, sort_keys=False) + "\n" for entry in entries
    )
    # 同一ディレクトリの一時ファイルに書いてから os.replace で差し替える。
    # 途中で落ちても、書きかけの台帳が残って以後の読み込みを壊すことがない。
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def find_entry(entries: Sequence[dict[str, Any]], entry_id: str) -> dict[str, Any]:
    for entry in entries:
        if entry.get("id") == entry_id:
            return entry
    raise ValueError(f"id {entry_id!r} が台帳に無い")


def ledger_path(args: argparse.Namespace) -> Path:
    if args.ledger:
        return Path(args.ledger)
    return find_repo_root() / LEDGER_RELPATH


def today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


# --- subcommands ---------------------------------------------------------


#: メタスキルのエントリに対して許可する status 遷移。
#: 「記録として残す」「却下する」以外に台帳を進める操作は、除外を骨抜きにする。
META_ALLOWED_STATUSES: frozenset[str] = frozenset({"excluded_meta", "rejected"})


def guard_meta(entry: dict[str, Any], new_status: str | None = None) -> int | None:
    """メタスキル宛エントリへの書き込みを止める (書き込み系サブコマンド共通)。

    除外を `add` だけで守ると、後から set-status / link-pr / record-metrics 経由で
    メタスキルの改善 PR を台帳に載せられてしまい、Iron Law が入口ひとつ分しか
    効かない。ガードは書き込み経路すべてに置く。
    excluded_meta / rejected への更新だけは通す — 記録と却下は除外と矛盾しない。

    戻り値: 止めたときは EXIT_META、通してよいときは None。
    """
    target = str(entry.get("target_skill", ""))
    # メタ判定は known_skills に依存しない (classify_target の不変条件)
    if classify_target(target, None) != "excluded_meta":
        return None
    if new_status in META_ALLOWED_STATUSES:
        return None
    print("classification: excluded_meta")
    print(
        f"error: {target} はメタスキル (改善対象外) のため {entry.get('id')} は更新しない。"
        f"許可されるのは status を {' / '.join(sorted(META_ALLOWED_STATUSES))} に"
        "する更新だけ — それ以外は人間に判断を上げること",
        file=sys.stderr,
    )
    return EXIT_META


def cmd_add(args: argparse.Namespace) -> int:
    path = ledger_path(args)
    entries = load_entries(path)
    root = find_repo_root()
    classification = classify_target(args.target, discover_skills(root))

    status = args.status
    # メタ判定を先に見るため、--allow-unknown-target では除外をすり抜けられない
    if classification == "excluded_meta":
        status = "excluded_meta"
    elif classification in ("unknown", "unresolved") and not args.allow_unknown_target:
        print(f"classification: {classification}")
        print(
            f"error: target skill {args.target!r} を {root} 配下で解決できない。"
            "名前を確認するか --allow-unknown-target を明示すること",
            file=sys.stderr,
        )
        return EXIT_FAIL

    entry = new_entry(
        entry_id=args.id or next_id(entries),
        created=args.created or today(),
        source=args.source,
        evidence=args.evidence,
        target_skill=args.target,
        finding=args.finding,
        lever=args.lever,
        status=status,
        recurrence=recurrence_for(
            entries, args.target, args.finding, args.finding_class
        ),
        class_key=args.finding_class,
        notes=args.notes,
    )
    entries.append(entry)
    save_entries(path, entries)
    print(f"classification: {classification}")
    print(json.dumps(entry, ensure_ascii=False))
    if classification == "excluded_meta":
        print(
            f"note: {args.target} はメタスキルのため編集しない。"
            "status=excluded_meta で記録した — 人間に上げること",
        )
        # 記録は残すが、呼出側が「改善対象として通った」と誤読しないよう exit 2
        return EXIT_META
    return EXIT_OK


def cmd_set_status(args: argparse.Namespace) -> int:
    path = ledger_path(args)
    entries = load_entries(path)
    entry = find_entry(entries, args.id)
    blocked = guard_meta(entry, args.status)
    if blocked is not None:
        return blocked
    entry["status"] = args.status
    if args.notes:
        entry["notes"] = args.notes
    save_entries(path, entries)
    print(json.dumps(entry, ensure_ascii=False))
    return EXIT_OK


def cmd_link_pr(args: argparse.Namespace) -> int:
    path = ledger_path(args)
    entries = load_entries(path)
    entry = find_entry(entries, args.id)
    blocked = guard_meta(entry)
    if blocked is not None:
        return blocked
    entry["pr"] = args.pr
    if not args.keep_status:
        entry["status"] = "pr_open"
    save_entries(path, entries)
    print(json.dumps(entry, ensure_ascii=False))
    return EXIT_OK


def cmd_record_metrics(args: argparse.Namespace) -> int:
    path = ledger_path(args)
    entries = load_entries(path)
    entry = find_entry(entries, args.id)
    blocked = guard_meta(entry)
    if blocked is not None:
        return blocked
    metrics = parse_metrics(args.metric)
    bucket = entry.get(args.phase)
    if not isinstance(bucket, dict):
        bucket = {}
    bucket.update(metrics)
    entry[args.phase] = bucket
    save_entries(path, entries)
    print(json.dumps(entry, ensure_ascii=False))
    for row in metric_deltas(entry.get("before"), entry.get("after")):
        print(
            f"  {row['metric']}: {row['before']} -> {row['after']}"
            f" ({fmt_delta(row['delta'])}) {row['verdict']}"
        )
    return EXIT_OK


def build_report(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """report の素データを組み立てる (純関数)。"""
    return {
        "entries": len(entries),
        "per_skill": recurrence_summary(entries),
        "deltas": [
            {
                "id": entry.get("id"),
                "target_skill": entry.get("target_skill"),
                "rows": metric_deltas(entry.get("before"), entry.get("after")),
            }
            for entry in entries
            if metric_deltas(entry.get("before"), entry.get("after"))
        ],
        "revert_candidates": [
            {
                "id": entry.get("id"),
                "target_skill": entry.get("target_skill"),
                "status": entry.get("status"),
                "pr": entry.get("pr"),
                "worse": worse_metrics(entry),
            }
            for entry in entries
            if is_revert_candidate(entry)
        ],
    }


def cmd_report(args: argparse.Namespace) -> int:
    path = ledger_path(args)
    entries = load_entries(path)
    if args.skill:
        entries = [e for e in entries if e.get("target_skill") == args.skill]
    report = build_report(entries)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"# Improvement ledger report ({path})")
        print(f"entries: {report['entries']}")
        print()
        print("## Per-skill")
        if not report["per_skill"]:
            print("  (なし)")
        for skill, bucket in sorted(report["per_skill"].items()):
            statuses = ", ".join(
                f"{k}={v}" for k, v in sorted(bucket["statuses"].items())
            )
            print(
                f"  {skill}: entries={bucket['entries']}"
                f" max_recurrence={bucket['max_recurrence']} [{statuses}]"
            )
        print()
        print("## Metric deltas (before -> after)")
        if not report["deltas"]:
            print("  (after 未記録)")
        for item in report["deltas"]:
            for row in item["rows"]:
                print(
                    f"  {item['id']} {item['target_skill']} {row['metric']}:"
                    f" {row['before']} -> {row['after']} ({fmt_delta(row['delta'])})"
                    f" {row['verdict']}"
                )
        print()
        print("## Revert candidates")
        if not report["revert_candidates"]:
            print("  (なし)")
        for item in report["revert_candidates"]:
            print(
                f"  {item['id']} {item['target_skill']} 悪化: "
                f"{', '.join(item['worse'])} (status={item['status']}, pr={item['pr']})"
            )

    if args.fail_on_revert and report["revert_candidates"]:
        return EXIT_FAIL
    return EXIT_OK


def cmd_check_target(args: argparse.Namespace) -> int:
    root = find_repo_root()
    classification = classify_target(args.skill, discover_skills(root))
    print(f"classification: {classification}")
    if classification == "excluded_meta":
        print(
            f"{args.skill} はメタスキル (改善対象外)。ledger に status=excluded_meta で"
            "記録し、人間に判断を上げること — 編集も PR 起票もしない"
        )
        return EXIT_META
    if classification == "unknown":
        print(f"{args.skill} は既知の skill ディレクトリに無い", file=sys.stderr)
        return EXIT_FAIL
    if classification == "unresolved":
        print(
            f"skill ディレクトリを {root} 配下で解決できない"
            f" (探した場所: {', '.join(SKILL_DIR_CANDIDATES)})",
            file=sys.stderr,
        )
        return EXIT_FAIL
    print(f"{args.skill} は改善対象にできる")
    return EXIT_OK


# --- CLI -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledger.py",
        description="skill-improver の改善台帳 (improvements/ledger.jsonl) を操作する",
    )
    parser.add_argument(
        "--ledger",
        help="台帳のパス (既定: <repo root>/improvements/ledger.jsonl)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="finding を 1 件記録する")
    add.add_argument("--source", required=True, choices=SOURCES)
    add.add_argument("--target", required=True, help="対象 skill 名")
    add.add_argument("--finding", required=True, help="finding を 1 文で")
    add.add_argument("--lever", required=True, choices=LEVERS)
    add.add_argument(
        "--class",
        dest="finding_class",
        default="",
        metavar="KEY",
        help=(
            "再発クラスを明示するキー (例: ci-self-heal/stop-condition)。"
            "省略時は finding 本文の正規化で数えるが、それが吸収できるのは表記揺れだけ。"
            "`report --skill` を読んで同じクラスと判断したら、同じキーを渡して束ねる"
        ),
    )
    add.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="証跡 URL / session id / ファイルパス (繰り返し可)",
    )
    add.add_argument("--status", default="proposed", choices=STATUSES)
    add.add_argument("--notes", default="")
    add.add_argument("--id", help="id を明示する (既定: 連番)")
    add.add_argument("--created", help="作成日 YYYY-MM-DD (既定: 今日 UTC)")
    add.add_argument(
        "--allow-unknown-target",
        action="store_true",
        help="skill ディレクトリで解決できない target でも記録する",
    )
    add.set_defaults(func=cmd_add)

    set_status = sub.add_parser("set-status", help="status を更新する")
    set_status.add_argument("--id", required=True)
    set_status.add_argument("--status", required=True, choices=STATUSES)
    set_status.add_argument("--notes", default="")
    set_status.set_defaults(func=cmd_set_status)

    link = sub.add_parser("link-pr", help="PR URL を紐付ける (既定で status=pr_open)")
    link.add_argument("--id", required=True)
    link.add_argument("--pr", required=True)
    link.add_argument("--keep-status", action="store_true", help="status を変えない")
    link.set_defaults(func=cmd_link_pr)

    metrics = sub.add_parser("record-metrics", help="before / after の指標を記録する")
    metrics.add_argument("--id", required=True)
    metrics.add_argument("--phase", required=True, choices=("before", "after"))
    metrics.add_argument(
        "--metric",
        action="append",
        default=[],
        required=True,
        metavar="KEY=VALUE",
        help=f"記録する指標 (繰り返し可)。KEY: {', '.join(METRIC_KEYS)}",
    )
    metrics.set_defaults(func=cmd_record_metrics)

    report = sub.add_parser("report", help="再発回数と before->after の delta を集計する")
    report.add_argument("--skill", help="対象 skill で絞る")
    report.add_argument("--json", action="store_true")
    report.add_argument(
        "--fail-on-revert",
        action="store_true",
        help="revert candidate があれば exit 1",
    )
    report.set_defaults(func=cmd_report)

    check = sub.add_parser("check-target", help="改善対象にしてよい skill かを判定する")
    check.add_argument("skill")
    check.set_defaults(func=cmd_check_target)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
