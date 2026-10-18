"""上流のリポジトリにあるスキルを、このリポジトリへ写す.

写す元は tools/vendored.json に書く。各ファイルの先頭に参照元(リポジトリ・コミット・パス)のコメントを入れ、
ライセンスの本文も一緒に写す。依存として取り込まずに写すのは、上流の変更が自動で入らないようにするため。

使い方(更新するときも同じ手順):

    git clone <tools/vendored.json の repository> <dir>
    git -C <dir> checkout <見直したコミット>
    uv run python tools/vendor_skill.py <tools/vendored.json の destination> <dir>
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - git を固定の引数で起動するために使う
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "tools" / "vendored.json"
_ALWAYS_EXCLUDED = frozenset({".claude-plugin"})
_HASH_COMMENT_SUFFIXES = frozenset({".py", ".txt", ".yaml", ".yml"})
_ROOT_LICENSES = ("LICENSE", "LICENSE.md", "LICENSE.txt")
_SYMLINK_MODE = "120000"
_SHA = re.compile(r"[0-9a-f]{40}")
_FRONTMATTER_END = "\n---\n"


class VendorError(Exception):
    pass


@dataclass(frozen=True)
class Spec:
    destination: str
    repository: str
    path: str
    exclude: tuple[str, ...]


def _field(entry: dict[str, object], name: str) -> str:
    value = entry.get(name)
    if not isinstance(value, str) or not value:
        msg = f"tools/vendored.json の項目に {name} がありません"
        raise VendorError(msg)
    return value


def load_specs(path: Path = CONFIG) -> dict[str, Spec]:
    entries = cast("object", json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(entries, list):
        msg = f"{path} は配列ではありません"
        raise VendorError(msg)
    specs: dict[str, Spec] = {}
    for raw in cast("list[object]", entries):
        if not isinstance(raw, dict):
            msg = f"{path} の項目がオブジェクトではありません"
            raise VendorError(msg)
        entry = {str(key): value for key, value in cast("dict[object, object]", raw).items()}
        exclude = entry.get("exclude", [])
        if not isinstance(exclude, list):
            msg = f"{path} の exclude が配列ではありません"
            raise VendorError(msg)
        spec = Spec(
            destination=_field(entry, "destination"),
            repository=_field(entry, "repository"),
            path=_field(entry, "path"),
            exclude=tuple(str(name) for name in cast("list[object]", exclude)),
        )
        specs[spec.destination] = spec
    return specs


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


def header(spec: Spec, relative: str, sha: str) -> str | None:
    text = (
        f"参照元: {spec.repository}/blob/{sha}/{spec.path}/{relative} "
        "(ライセンスと著作権表示は、このスキルの LICENSE を参照)"
    )
    suffix = PurePosixPath(relative).suffix
    if suffix in _HASH_COMMENT_SUFFIXES:
        return f"# {text}\n"
    if suffix == ".md":
        return f"<!-- {text} -->\n"
    return None


def annotate(spec: Spec, body: str, relative: str, sha: str) -> str:
    comment = header(spec, relative, sha)
    if comment is None or "LICENSE" in relative:
        return body
    if body.startswith("#!"):
        end = body.index("\n") + 1
        return body[:end] + comment + body[end:]
    if relative == "SKILL.md" and body.startswith("---\n"):
        end = body.index(_FRONTMATTER_END, len("---")) + len(_FRONTMATTER_END)
        return body[:end] + "\n" + comment + body[end:]
    return comment + body


def _tracked(source: Path, pathspec: str) -> dict[str, str]:
    # 出力は「<mode> <object> <stage>\t<path>」を NUL で区切ったもの。
    listing = _git(source, "ls-files", "-s", "-z", "--", pathspec)
    files: dict[str, str] = {}
    for record in filter(None, listing.split("\0")):
        meta, path = record.split("\t", 1)
        files[path] = meta.split(" ", 1)[0]
    return files


def _files_to_copy(source: Path, spec: Spec) -> dict[str, tuple[str, str]]:
    prefix = f"{spec.path}/"
    excluded = _ALWAYS_EXCLUDED | set(spec.exclude)
    files = {
        path.removeprefix(prefix): (path, mode)
        for path, mode in _tracked(source, spec.path).items()
        if path.removeprefix(prefix).split("/", 1)[0] not in excluded
    }
    if "SKILL.md" not in files:
        msg = f"{source / spec.path} に SKILL.md がありません"
        raise VendorError(msg)
    if not any(PurePosixPath(relative).name.startswith("LICENSE") for relative in files):
        root = _tracked(source, ".")
        license_path = next((name for name in _ROOT_LICENSES if name in root), None)
        if license_path is None:
            msg = f"{source} にライセンスのファイルがありません"
            raise VendorError(msg)
        files["LICENSE"] = (license_path, root[license_path])
    return files


def vendor(source: Path, spec: Spec, root: Path = ROOT) -> str:
    sha = _git(source, "rev-parse", "HEAD").strip()
    if _SHA.fullmatch(sha) is None:
        msg = f"コミットを取得できません: {source}"
        raise VendorError(msg)
    if _git(source, "status", "--porcelain").strip():
        msg = f"作業ツリーに変更があります: {source}"
        raise VendorError(msg)
    files = _files_to_copy(source, spec)

    destination = root / spec.destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.new-", dir=destination.parent))
    retired = destination.parent / f".{destination.name}.old"
    try:
        for relative, (upstream, mode) in sorted(files.items()):
            if mode == _SYMLINK_MODE:
                msg = f"シンボリックリンクは写しません: {upstream}"
                raise VendorError(msg)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            body = (source / upstream).read_bytes()
            if header(spec, relative, sha) is not None:
                body = annotate(spec, body.decode("utf-8"), relative, sha).encode("utf-8")
            _ = target.write_bytes(body)
        shutil.rmtree(retired, ignore_errors=True)
        if destination.exists():
            _ = destination.rename(retired)
        _ = staging.rename(destination)
        shutil.rmtree(retired, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return sha


def _spec_for(destination: str, config: Path) -> Spec:
    specs = load_specs(config)
    spec = specs.get(destination.rstrip("/"))
    if spec is None:
        msg = f"tools/vendored.json に {destination} がありません(候補: {', '.join(sorted(specs))})"
        raise VendorError(msg)
    return spec


def run(args: Sequence[str], config: Path = CONFIG, root: Path = ROOT) -> Literal[0, 1, 2]:
    if len(args) != 2:  # ruff: ignore[magic-value-comparison] - 引数は写す先と clone したディレクトリの2つ
        _ = sys.stderr.write("使い方: python tools/vendor_skill.py <写す先> <clone したディレクトリ>\n")
        return 2
    try:
        spec = _spec_for(args[0], config)
        sha = vendor(Path(args[1]), spec, root)
    except VendorError as error:
        _ = sys.stderr.write(f"{error}\n")
        return 1
    _ = sys.stdout.write(f"{spec.repository}@{sha} の {spec.path} を {spec.destination} へ写しました\n")
    return 0


def main() -> Literal[0, 1, 2]:
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
