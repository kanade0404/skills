#!/usr/bin/env python3
# ruff: noqa: T201, INP001
"""skill-improver workflow の manifest と、PR に載る文字列を検査する。

agent が書いた manifest は **データ**であって、そのまま特権的な操作に渡してよい
値ではない。ここが唯一の関門なので、次の 2 つを 1 か所で持つ:

1. `sanitize` — manifest を **許可したキーだけで組み直す**。未知のキーは落とし、
   各フィールドは形式・長さ・制御文字・トークン様文字列で検査する。さらに manifest
   **全体**で `branch` と (空でない) `ledger_id` の一意性を見る — 行ごとの検査だけでは
   同じ finding を 2 回起票する形が通ってしまう。
2. `check-text` — `gh pr create` に渡す直前の title / body を同じ規則で再検査する。

同じ走査を 2 か所に書き写すと片方だけ古くなるので、workflow の improve
(staging) と publish (起票直前) の双方がこのファイルを呼ぶ。候補ブランチは
`.github/` を触れない (allow-list の外) ため、このスクリプト自体は候補の影響を
受けない。

exit code: 0 = 合格 / 1 = 違反 (理由を stderr に出す)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

#: PR タイトルや本文に載ってはいけないトークンの接頭辞。
#: agent は自分の GH_TOKEN を読めるので、manifest の任意の文字列フィールドに
#: 書き込める。body だけでなく **全ての文字列**を走査する。
TOKEN_RE = re.compile(r"(ghs_|ghp_|github_pat_|ghu_)[A-Za-z0-9_]{10,}")

#: 制御文字 (改行・タブ・エスケープ等)。title に混ざると表示や引用が壊れるうえ、
#: ログ上で別の行に見せかける細工にも使える。
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

BRANCH_RE = re.compile(r"^improve/[A-Za-z0-9._-]+$")
HEAD_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LEDGER_ID_RE = re.compile(r"^IMP-[0-9]{8}-[0-9a-f]{10}$")
BODY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: manifest に残してよいキーと、その最大長。これ以外は黙って落とす
#: (未知のキーを運ぶと、後から増えた読み手がそれを信用してしまう)。
ALLOWED_KEYS: dict[str, int] = {
    "branch": 200,
    "head_sha": 40,
    "body_file": 200,
    "ledger_id": 32,
    "title": 200,
}

MAX_BODY_BYTES = 65536


def text_problems(label: str, value: str, limit: int) -> list[str]:
    """1 つの文字列フィールドの違反を並べる (純関数)。"""
    problems: list[str] = []
    if len(value) > limit:
        problems.append(f"{label}: 長すぎる ({len(value)} > {limit})")
    if CONTROL_RE.search(value):
        problems.append(f"{label}: 制御文字または改行を含む")
    if TOKEN_RE.search(value):
        problems.append(f"{label}: トークンらしき文字列を含む")
    return problems


def entry_problems(entry: dict[str, Any]) -> list[str]:
    """manifest 1 行分の違反を並べる (純関数)。"""
    problems: list[str] = []
    for key, limit in ALLOWED_KEYS.items():
        value = entry.get(key, "")
        if not isinstance(value, str):
            problems.append(f"{key}: 文字列でない")
            continue
        problems.extend(text_problems(key, value, limit))

    branch = entry.get("branch", "")
    if not isinstance(branch, str) or not BRANCH_RE.match(branch):
        problems.append("branch: 形式が不正")
    head_sha = entry.get("head_sha", "")
    if not isinstance(head_sha, str) or not HEAD_SHA_RE.match(head_sha):
        problems.append("head_sha: 形式が不正")
    title = entry.get("title", "")
    if not isinstance(title, str) or not title.strip():
        problems.append("title: 空")
    body_file = entry.get("body_file", "")
    if not isinstance(body_file, str) or not BODY_NAME_RE.match(Path(body_file).name):
        problems.append("body_file: 名前が不正")
    ledger_id = entry.get("ledger_id", "")
    if ledger_id and (
        not isinstance(ledger_id, str) or not LEDGER_ID_RE.match(ledger_id)
    ):
        problems.append("ledger_id: 形式が不正")
    return problems


def rebuild_entry(entry: dict[str, Any]) -> dict[str, str]:
    """許可したキーだけで組み直す (純関数)。body_file は basename に落とす。"""
    rebuilt = {key: str(entry.get(key, "")) for key in ALLOWED_KEYS}
    rebuilt["body_file"] = Path(rebuilt["body_file"]).name
    return rebuilt


def duplicate_problems(entries: Sequence[dict[str, str]]) -> list[str]:
    """manifest 全体で重複してはいけない値の違反を並べる (純関数)。

    1 行ずつの検査は「同じ finding を 2 回起票する」形を通してしまう。publish は
    行ごとに `gh pr create` と `link-pr` を回すため、`branch` が重なれば同じ
    ブランチから 2 本の PR が立ち、`ledger_id` が重なれば 1 つの台帳行に 2 度
    `link-pr` が走って先に書いた PR URL が消える。

    `ledger_id` が空の行 (突き合わせ用の reconcile 行) は台帳の特定の行を指さない
    ので、この一意性検査の対象外にする。
    """
    problems: list[str] = []
    for key in ("branch", "ledger_id"):
        seen: dict[str, int] = {}
        for lineno, entry in enumerate(entries, 1):
            value = entry.get(key, "")
            if key == "ledger_id" and not value:
                continue
            if value in seen:
                problems.append(
                    f"manifest:{lineno}: {key} {value!r} が {seen[value]} 行目と重複"
                )
            else:
                seen[value] = lineno
    return problems


def cmd_sanitize(args: argparse.Namespace) -> int:
    """manifest を検査し、許可キーだけで組み直して書き出す。"""
    src = Path(args.src)
    lines = [ln for ln in src.read_text(encoding="utf-8").split("\n") if ln.strip()]
    rebuilt: list[dict[str, str]] = []
    failed = False
    for lineno, line in enumerate(lines, 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"manifest:{lineno}: JSON として読めない ({exc})", file=sys.stderr)
            failed = True
            continue
        if not isinstance(entry, dict):
            print(f"manifest:{lineno}: オブジェクトでない", file=sys.stderr)
            failed = True
            continue
        problems = entry_problems(entry)
        if problems:
            failed = True
            for problem in problems:
                print(f"manifest:{lineno}: {problem}", file=sys.stderr)
            continue
        rebuilt.append(rebuild_entry(entry))
    # 重複は行単位では見えないので、全行が個別検査を通った後にまとめて見る。
    # 1 件でもあれば manifest 全体を落とす — どちらの行が正しいかは判断できない。
    for problem in duplicate_problems(rebuilt):
        print(problem, file=sys.stderr)
        failed = True
    if failed:
        return 1
    Path(args.dest).write_text(
        "".join(f"{json.dumps(e, ensure_ascii=False)}\n" for e in rebuilt),
        encoding="utf-8",
    )
    print(f"manifest_guard: {len(rebuilt)} 行を検査して書き出した")
    return 0


def cmd_check_text(args: argparse.Namespace) -> int:
    """gh pr create に渡す直前の title / body を再検査する。"""
    problems: list[str] = []
    if args.title is not None:
        problems.extend(text_problems("title", args.title, ALLOWED_KEYS["title"]))
        if not args.title.strip():
            problems.append("title: 空")
    if args.body_file is not None:
        body_path = Path(args.body_file)
        raw = body_path.read_bytes()
        if len(raw) > MAX_BODY_BYTES:
            problems.append(f"body: 大きすぎる ({len(raw)} > {MAX_BODY_BYTES})")
        text = raw.decode("utf-8", errors="replace")
        if TOKEN_RE.search(text):
            problems.append("body: トークンらしき文字列を含む")
    if problems:
        for problem in problems:
            print(f"manifest_guard: {problem}", file=sys.stderr)
        return 1
    print("manifest_guard: ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """サブコマンドを持つ CLI パーサを組み立てる。"""
    parser = argparse.ArgumentParser(prog="manifest_guard.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sanitize = sub.add_parser("sanitize", help="manifest を検査して組み直す")
    sanitize.add_argument("--src", required=True)
    sanitize.add_argument("--dest", required=True)
    sanitize.set_defaults(func=cmd_sanitize)

    check = sub.add_parser("check-text", help="title / body を再検査する")
    check.add_argument("--title")
    check.add_argument("--body-file", dest="body_file")
    check.set_defaults(func=cmd_check_text)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI エントリポイント。"""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
