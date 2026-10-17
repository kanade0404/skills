"""yomiyasu(https://github.com/nanaism/yomiyasu, MIT)の skills/yomiyasu を写す.

plugins/ja-writing/skills/yomiyasu へ写し、各ファイルの先頭に参照元のコメントを入れる。
依存として取り込まずに写すのは、上流の変更が自動で入らないようにするため。

使い方(更新するときも同じ手順):

    git clone https://github.com/nanaism/yomiyasu <dir>
    git -C <dir> checkout <見直したコミット>
    uv run python tools/vendor_yomiyasu.py <dir>
"""

from __future__ import annotations

import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - git を固定の引数で起動するために使う
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

REPO = "https://github.com/nanaism/yomiyasu"
DESTINATION = Path(__file__).resolve().parent.parent / "plugins" / "ja-writing" / "skills" / "yomiyasu"
# 写さないもの: 上流のプラグイン設定(入れ子のプラグインとして読まれるのを避ける)と、SKILL.md から参照されない画像。
_EXCLUDE = frozenset({".claude-plugin", "assets"})
_ANNOTATED_SUFFIXES = frozenset({".py", ".txt", ".md"})
_SHA = re.compile(r"[0-9a-f]{40}")
_FRONTMATTER_END = "\n---\n"


class VendorError(Exception):
    """写せないときの誤り."""


def _git(source: Path, *args: str) -> str:
    git = shutil.which("git")
    if git is None:
        msg = "git が見つかりません"
        raise VendorError(msg)
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - git は which で解決した絶対パス、引数は固定
        [git, "-C", str(source), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = f"git {' '.join(args)} が失敗しました: {source}\n{result.stderr}"
        raise VendorError(msg)
    return result.stdout


def header(relative: str, sha: str) -> str | None:
    """ファイルの種類に合わせた参照元のコメントを作る.

    Returns:
        コメントの行。コメントを書けない種類なら None。
    """
    text = (
        f"参照元: {REPO}/blob/{sha}/skills/yomiyasu/{relative} "
        "(MIT License。著作権表示は skills/yomiyasu/LICENSE を参照)"
    )
    suffix = Path(relative).suffix
    if suffix in {".py", ".txt"}:
        return f"# {text}\n"
    if suffix == ".md":
        return f"<!-- {text} -->\n"
    return None


def annotate(body: str, relative: str, sha: str) -> str:
    """本文の先頭に参照元のコメントを入れる. shebang と SKILL.md の frontmatter は先頭に残す.

    Returns:
        コメントを入れた本文。ライセンスとコメントを書けない種類はそのまま。
    """
    comment = header(relative, sha)
    if comment is None or "LICENSE" in relative:
        return body
    if body.startswith("#!"):
        end = body.index("\n") + 1
        return body[:end] + comment + body[end:]
    if relative == "SKILL.md" and body.startswith("---\n"):
        end = body.index(_FRONTMATTER_END, len("---")) + len(_FRONTMATTER_END)
        return body[:end] + "\n" + comment + body[end:]
    return comment + body


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _EXCLUDE}


def vendor(source: Path, destination: Path = DESTINATION) -> str:
    """Source の skills/yomiyasu を destination へ写す.

    Returns:
        写したコミットの SHA。

    Raises:
        VendorError: git が失敗したとき、作業ツリーに変更があるとき、写す元がないとき。
    """
    origin = source / "skills" / "yomiyasu"
    sha = _git(source, "rev-parse", "HEAD").strip()
    if _SHA.fullmatch(sha) is None:
        msg = f"コミットを取得できません: {source}"
        raise VendorError(msg)
    if _git(source, "status", "--porcelain").strip():
        msg = f"作業ツリーに変更があります: {source}"
        raise VendorError(msg)
    # 既存の写しを消す前に、写す元があることを確かめる。
    if not (origin / "SKILL.md").is_file():
        msg = f"{origin} に SKILL.md がありません"
        raise VendorError(msg)

    shutil.rmtree(destination, ignore_errors=True)
    _ = shutil.copytree(origin, destination, ignore=_ignore)
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.suffix in _ANNOTATED_SUFFIXES:
            relative = path.relative_to(destination).as_posix()
            _ = path.write_text(annotate(path.read_text(encoding="utf-8"), relative, sha), encoding="utf-8")
    return sha


def run(args: Sequence[str]) -> int:
    """コマンドライン引数で実行する.

    Returns:
        成功なら 0、写せなければ 1、引数の誤りなら 2。
    """
    if len(args) != 1:
        _ = sys.stderr.write("使い方: python tools/vendor_yomiyasu.py <yomiyasu を clone したディレクトリ>\n")
        return 2
    try:
        sha = vendor(Path(args[0]))
    except VendorError as error:
        _ = sys.stderr.write(f"{error}\n")
        return 1
    _ = sys.stdout.write(f"{REPO}@{sha} を {DESTINATION} へ写しました\n")
    return 0


def main() -> int:
    """コマンドライン引数で実行する.

    Returns:
        終了コード。
    """
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
