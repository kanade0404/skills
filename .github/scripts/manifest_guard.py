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
3. `scan-diff` / `scan-files` — **候補ブランチの差分そのもの**と PR 本文ファイルを、
   この job が見えるシークレットの実値で走査する。manifest と本文だけを見ていても、
   agent は資格情報を `skills/<x>/**` や台帳の中に書いて push させられるため
   (allow-list はパスしか見ない)。値は環境変数名で受け取り、**値も一致箇所も一切
   出力しない** — 出るのはパスと理由だけ。

同じ走査を 2 か所に書き写すと片方だけ古くなるので、workflow の improve
(staging) と publish (起票直前) の双方がこのファイルを呼ぶ。候補ブランチは
`.github/` を触れない (allow-list の外) が、**その allow-list が効くのは push の後**
なので、improve 側の呼び出しは working tree のコピーではなく run を起動した commit
の blob (`git show $GITHUB_SHA:...`) を取り出して実行する。

exit code: 0 = 合格 / 1 = 違反 (理由を stderr に出す)
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import subprocess  # noqa: S404
import sys
from collections.abc import Mapping, Sequence
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


# ---------------------------------------------------------------------------
# シークレット走査 (候補ブランチの差分 / PR 本文)
#
# **なぜ要るか**: `claude-code-action` は API を呼ぶために資格情報を agent の
# プロセスに渡す。取り除けない以上、資格情報が読めること自体は前提として、
# **公開される場所へ出て行く手前**を関門にする。候補ブランチの内容は
# パスの allow-list (`skills/<x>/**` と台帳) しか見られていないので、値の側は
# ここで見る。走査は push の前 = 公開の前に、trusted step で行う。
#
# **出力規律**: 一致した値も、その周辺の文字列も、絶対に出さない (出したら
# それ自体が漏洩になる)。出すのはパスと「どの名前のシークレットか」だけ。
# ---------------------------------------------------------------------------

#: 値そのものを探すときの最小長。短い値 (App ID のような数字列) は誤検知に
#: しかならないので走査対象にしない。
MIN_NEEDLE_BYTES = 12

#: 値を知らなくても拾える接頭辞の網。実値の走査と違い、**この job が見ていない**
#: 資格情報 (別経路で混入したもの) も引っ掛かる。
PREFIX_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("Anthropic API/OAuth key", re.compile(rb"sk-ant-[A-Za-z0-9_-]{16,}")),
    ("GitHub token", re.compile(rb"(?:ghs|ghp|ghu|gho)_[A-Za-z0-9]{16,}")),
    ("GitHub fine-grained PAT", re.compile(rb"github_pat_[A-Za-z0-9_]{16,}")),
    ("PEM private key", re.compile(rb"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----")),
)

#: 走査前に落とす「見た目だけの区切り」。改行で折り返したり JSON の `\n` に
#: して埋め込んだりするだけで実値の一致を外せてしまうため。
_WHITESPACE_RE = re.compile(rb"\s+")
_ESCAPE_RE = re.compile(rb"\\[nrt]")


class GitError(RuntimeError):
    """git の呼び出しが失敗した (読めない候補は不合格として扱う)。"""


def compact(data: bytes) -> bytes:
    """空白と `\n` `\r` `\t` エスケープを落とした比較用の姿を返す (純関数)。"""
    return _WHITESPACE_RE.sub(b"", _ESCAPE_RE.sub(b"", data))


def _encodings(raw: bytes) -> list[tuple[str, bytes]]:
    """1 つのバイト列について、探すべき符号化の姿を並べる (純関数)。"""
    hexed = binascii.hexlify(raw)
    return [
        ("literal", raw),
        # 末尾の `=` を落とす: パディング付きの姿はこの部分列を含むので、
        # 落とした側だけを持てば両方に当たる。
        ("base64", base64.b64encode(raw).rstrip(b"=")),
        ("base64url", base64.urlsafe_b64encode(raw).rstrip(b"=")),
        ("hex", hexed),
        ("hex-upper", hexed.upper()),
    ]


def secret_needles(name: str, value: str) -> list[tuple[str, bytes]]:
    """シークレット 1 件から、探す文字列とそのラベルを並べる (純関数)。

    ラベルは**名前と符号化の種類だけ**で、値は含めない (ログに出す側で使う)。
    """
    needles: list[tuple[str, bytes]] = []
    seen: set[bytes] = set()

    def add(label: str, data: bytes) -> None:
        if len(data) < MIN_NEEDLE_BYTES or data in seen:
            return
        seen.add(data)
        needles.append((f"{name} ({label})", data))

    raw = value.encode("utf-8", errors="surrogateescape")
    for label, encoded in _encodings(raw):
        add(label, encoded)
    # PEM 秘密鍵のような複数行の値は、改行の扱いだけで姿が変わる。
    squeezed = compact(raw)
    if squeezed != raw:
        for label, encoded in _encodings(squeezed):
            add(f"whitespace-stripped {label}", encoded)
    lines = [line.strip() for line in raw.splitlines()]
    if len(lines) > 1:
        for lineno, line in enumerate(lines, 1):
            # `-----BEGIN ...-----` の行は値ごとに同じで、接頭辞の網が拾う。
            if line.startswith(b"-----"):
                continue
            add(f"line {lineno}", line)
    return needles


def needles_from_env(
    names: Sequence[str], environ: Mapping[str, str]
) -> tuple[list[tuple[str, bytes]], list[str]]:
    """環境変数名の列から探索対象を組み立て、(needles, 注記) を返す (純関数)。

    値を引数で渡さないのは、コマンドラインが他プロセスから見えるため。
    空や短すぎる値は「走査しなかった」ことを注記に残す — 黙って素通りさせない。
    """
    needles: list[tuple[str, bytes]] = []
    notes: list[str] = []
    for name in names:
        value = environ.get(name, "")
        if not value:
            notes.append(f"{name}: 環境変数が空 — この値は走査しない")
            continue
        built = secret_needles(name, value)
        if not built:
            notes.append(f"{name}: 値が短すぎる — この値は走査しない")
            continue
        needles.extend(built)
    return needles, notes


def scan_blob(data: bytes, needles: Sequence[tuple[str, bytes]]) -> list[str]:
    """1 つのバイト列の違反理由を並べる (純関数)。値は返さない。"""
    reasons: list[str] = []
    squeezed = compact(data)
    for label, needle in needles:
        squeezed_needle = compact(needle)
        if needle in data or (
            len(squeezed_needle) >= MIN_NEEDLE_BYTES and squeezed_needle in squeezed
        ):
            reason = f"シークレット {label} の値を含む"
            if reason not in reasons:
                reasons.append(reason)
    for label, pattern in PREFIX_PATTERNS:
        if pattern.search(data):
            reason = f"{label} らしき文字列を含む"
            if reason not in reasons:
                reasons.append(reason)
    return reasons


def _git(repo: str, *args: str) -> bytes:
    """git を呼んで stdout を返す。失敗は GitError にする。"""
    proc = subprocess.run(  # noqa: S603
        ["git", "-C", repo, *args],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"git {' '.join(args)}: {detail}")
    return proc.stdout


def changed_paths(repo: str, base: str, head: str) -> list[str]:
    """base...head で追加・変更されたパスを返す (削除は対象外)。"""
    out = _git(
        repo, "diff", "--name-only", "-z", "--diff-filter=ACMRT", f"{base}...{head}"
    )
    return [
        chunk.decode("utf-8", errors="surrogateescape")
        for chunk in out.split(b"\0")
        if chunk
    ]


def scan_repo_diff(
    repo: str, base: str, head: str, needles: Sequence[tuple[str, bytes]]
) -> list[str]:
    """候補ブランチの差分と、変更後ファイルの中身を丸ごと走査する。

    差分テキストだけでなく **head 側の全内容**を見るのは、バイナリ扱いの
    ファイルだと差分に中身が出ないため。中身は `git show <sha>:<path>` の
    生 blob から取る (textconv / smudge フィルタで隠せない経路)。
    """
    hits: list[str] = []
    diff = _git(
        repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", f"{base}...{head}"
    )
    label = f"<diff {base}...{head}>"
    hits.extend(f"{label}: {reason}" for reason in scan_blob(diff, needles))
    for path in changed_paths(repo, base, head):
        blob = _git(repo, "show", f"{head}:{path}")
        hits.extend(f"{path}: {reason}" for reason in scan_blob(blob, needles))
    return hits


def _report(hits: Sequence[str], notes: Sequence[str], label: str) -> int:
    """走査結果を出力する。**一致した値は決して出さない**。"""
    for note in notes:
        print(f"scan: {note}", file=sys.stderr)
    if hits:
        for hit in hits:
            print(f"scan: {hit}", file=sys.stderr)
        print(f"scan: {label} にシークレットらしき値がある", file=sys.stderr)
        return 1
    print(f"scan: {label} は clean")
    return 0


def _prepare(names: Sequence[str]) -> tuple[list[tuple[str, bytes]], list[str], bool]:
    """走査対象を組み立てる。1 つも作れなければ「走査できなかった」とする。"""
    needles, notes = needles_from_env(names, os.environ)
    return needles, notes, bool(needles)


def cmd_scan_diff(args: argparse.Namespace) -> int:
    """候補ブランチの差分をシークレットで走査する (push の前に呼ぶ)。"""
    needles, notes, usable = _prepare(args.secret_env)
    if not usable:
        for note in notes:
            print(f"scan: {note}", file=sys.stderr)
        print(
            "scan: 走査できる値が 1 つも無い — 走査したことにはできない",
            file=sys.stderr,
        )
        return 1
    try:
        hits = scan_repo_diff(args.repo, args.base, args.head, needles)
    except GitError as exc:
        print(f"scan: 差分を読めなかった ({exc})", file=sys.stderr)
        return 1
    return _report(hits, notes, f"{args.head} の差分")


def cmd_scan_files(args: argparse.Namespace) -> int:
    """ファイルの中身をシークレットで走査する (PR 本文など)。"""
    needles, notes, usable = _prepare(args.secret_env)
    if not usable:
        for note in notes:
            print(f"scan: {note}", file=sys.stderr)
        print(
            "scan: 走査できる値が 1 つも無い — 走査したことにはできない",
            file=sys.stderr,
        )
        return 1
    hits: list[str] = []
    for name in args.paths:
        path = Path(name)
        try:
            data = path.read_bytes()
        except OSError as exc:
            print(f"scan: {name} を読めなかった ({exc.strerror})", file=sys.stderr)
            return 1
        hits.extend(f"{name}: {reason}" for reason in scan_blob(data, needles))
    return _report(hits, notes, "対象ファイル")


def add_secret_env_argument(parser: argparse.ArgumentParser) -> None:
    """シークレットを **値ではなく環境変数名**で受け取る引数を足す。"""
    parser.add_argument(
        "--secret-env",
        action="append",
        default=[],
        metavar="NAME",
        help="走査する値を持つ環境変数の名前 (値は渡さない)。複数指定可",
    )


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

    scan_diff = sub.add_parser(
        "scan-diff", help="候補ブランチの差分をシークレットで走査する"
    )
    scan_diff.add_argument("--repo", default=".")
    scan_diff.add_argument("--base", required=True)
    scan_diff.add_argument("--head", required=True)
    add_secret_env_argument(scan_diff)
    scan_diff.set_defaults(func=cmd_scan_diff)

    scan_files = sub.add_parser(
        "scan-files", help="ファイルの中身をシークレットで走査する"
    )
    add_secret_env_argument(scan_files)
    scan_files.add_argument("paths", nargs="+")
    scan_files.set_defaults(func=cmd_scan_files)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI エントリポイント。"""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
