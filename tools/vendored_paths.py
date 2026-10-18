"""tools/vendored.json の写す先を、検査とレビューの除外に反映する.

使い方:

    python3 tools/vendored_paths.py sync     # pyproject.toml と .coderabbit.yaml の印の間を書き直す
    python3 tools/vendored_paths.py semgrep  # semgrep の --exclude の引数を1行ずつ出す
    python3 tools/vendored_paths.py review   # PR レビューで除くディレクトリを、読点で区切って出す
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from vendor_skill import CONFIG, ROOT, load_specs

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

BEGIN = "# BEGIN tools/vendored.json (tools/vendored_paths.py sync が書く)"
END = "# END tools/vendored.json"
TARGETS: dict[str, Callable[[str], str]] = {
    "pyproject.toml": lambda destination: f'"{destination}",',
    ".coderabbit.yaml": lambda destination: f'- "!{destination}/**"',
}


class SyncError(Exception):
    pass


def destinations(config: Path = CONFIG) -> list[str]:
    return sorted(load_specs(config))


def render(text: str, lines: Sequence[str], name: str) -> str:
    rows = text.split("\n")
    begin = next((index for index, row in enumerate(rows) if row.strip() == BEGIN), None)
    end = next((index for index, row in enumerate(rows) if row.strip() == END), None)
    if begin is None or end is None or end < begin:
        msg = f"{name} に「{BEGIN}」と「{END}」の印がありません"
        raise SyncError(msg)
    indent = rows[begin][: len(rows[begin]) - len(rows[begin].lstrip())]
    return "\n".join([*rows[: begin + 1], *(indent + line for line in lines), *rows[end:]])


def sync(root: Path = ROOT, config: Path = CONFIG, *, write: bool = True) -> list[str]:
    names = destinations(config)
    stale: list[str] = []
    for name, line in TARGETS.items():
        path = root / name
        text = path.read_text(encoding="utf-8")
        expected = render(text, [line(destination) for destination in names], name)
        if expected != text:
            stale.append(name)
            if write:
                _ = path.write_text(expected, encoding="utf-8")
    return stale


def _output(command: str, root: Path, config: Path) -> str | None:
    if command == "sync":
        return "".join(f"{name} を書き直しました\n" for name in sync(root, config))
    if command == "semgrep":
        return "".join(f"--exclude\n{destination}\n" for destination in destinations(config))
    if command == "review":
        return "、".join(f"{destination}/" for destination in destinations(config)) + "\n"
    return None


def run(args: Sequence[str], root: Path = ROOT, config: Path = CONFIG) -> int:
    try:
        output = _output(args[0], root, config) if len(args) == 1 else None
    except SyncError as error:
        _ = sys.stderr.write(f"{error}\n")
        return 1
    if output is None:
        _ = sys.stderr.write("使い方: python3 tools/vendored_paths.py sync|semgrep|review\n")
        return 2
    _ = sys.stdout.write(output)
    return 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
