from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] - bun を固定の引数で起動するために使う
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from protocol import as_object, integer, items, loads, objects, parse_object, string

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from protocol import JsonObject

SKILL_ROOT = Path(__file__).resolve().parent.parent
RUNNER = SKILL_ROOT / "scripts" / "kernel_runner.ts"
FILE_CONFIG = SKILL_ROOT / "config" / "textlintrc.json"
CHAT_CONFIG = SKILL_ROOT / "config" / "chat.json"
LINTABLE_EXTENSIONS = frozenset({".md", ".markdown", ".mdown", ".mkdn", ".txt", ".text"})
MINIMUM_BUN = (1, 3)
SEVERITY_ERROR = 2

_BUN_VERSION = re.compile(r"(\d+)\.(\d+)\.\d+")
_PROJECT_JSON_CONFIGS = (".textlintrc.json", ".textlintrc")
_PROJECT_OTHER_CONFIGS = (
    ".textlintrc.jsonc",
    ".textlintrc.yaml",
    ".textlintrc.yml",
    ".textlintrc.js",
    ".textlintrc.cjs",
)
_BUN_INSTALL = ("install", "--frozen-lockfile", "--production", "--ignore-scripts", "--no-progress", "--silent")
# bun はカレントディレクトリの bunfig.toml(preload など)と .env を読む。プロジェクトのコードや設定が混ざらないよう、
# bun は自分で管理するディレクトリで起動し、.env も読ませない。
_BUN_RUN = ("--no-env-file",)


class TextlintError(Exception):
    pass


@dataclass(frozen=True)
class Document:
    file_path: str
    text: str


@dataclass(frozen=True)
class Message:
    rule_id: str
    message: str
    line: int
    column: int
    severity: int


@dataclass(frozen=True)
class DocumentResult:
    file_path: str
    messages: tuple[Message, ...]
    output: str | None


@dataclass(frozen=True)
class Report:
    results: tuple[DocumentResult, ...]
    ignored: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class Config:
    data: JsonObject
    source: str


def deps_dir(env: Mapping[str, str]) -> Path:
    override = env.get("TEXTLINT_SKILL_DEPS_DIR")
    if override:
        return Path(override)
    digest = hashlib.sha256((SKILL_ROOT / "bun.lock").read_bytes()).hexdigest()[:12]
    cache = Path(env.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache / "ja-writing-textlint" / digest


def installed(directory: Path) -> bool:
    return (directory / "node_modules" / "@textlint" / "kernel" / "package.json").is_file()


def _which(command: str, env: Mapping[str, str]) -> str:
    found = shutil.which(command, path=env.get("PATH"))
    if found is None:
        msg = f"{command} が見つかりません"
        raise TextlintError(msg)
    return found


def ensure_installed(env: Mapping[str, str]) -> Path:
    directory = deps_dir(env)
    if installed(directory):
        return directory

    bun = _bun(env)
    directory.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{directory.name}.tmp-", dir=directory.parent))
    try:
        for name in ("package.json", "bun.lock"):
            _ = shutil.copyfile(SKILL_ROOT / name, work / name)
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - bun は which で解決した絶対パス、引数は固定
            [bun, *_BUN_INSTALL],
            cwd=work,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            msg = f"textlint の依存を入れられませんでした(bun install)\n{result.stderr}"
            raise TextlintError(msg)
        try:
            _ = work.rename(directory)
        except OSError:
            if not installed(directory):
                raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return directory


def _bun(env: Mapping[str, str]) -> str:
    bun = _which("bun", env)
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - bun は which で解決した絶対パス、引数は固定
        [bun, "--version"], cwd=SKILL_ROOT, capture_output=True, text=True, check=False, env=dict(env)
    )
    match = _BUN_VERSION.fullmatch(result.stdout.strip())
    if match is None or (int(match.group(1)), int(match.group(2))) < MINIMUM_BUN:
        msg = f"bun {MINIMUM_BUN[0]}.{MINIMUM_BUN[1]} 以上が必要です(見つかったもの: {result.stdout.strip()})"
        raise TextlintError(msg)
    return bun


def load_config(path: Path) -> Config:
    try:
        data = as_object(loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        msg = f"設定 {path} を読めません: {error}"
        raise TextlintError(msg) from error
    if data is None:
        msg = f"設定 {path} が JSON のオブジェクトではありません"
        raise TextlintError(msg)
    return Config(data, str(path))


def choose_file_config(project_dir: Path, env: Mapping[str, str]) -> Config:
    explicit = env.get("TEXTLINT_CONFIG")
    if explicit:
        return load_config(Path(explicit))
    for name in _PROJECT_JSON_CONFIGS:
        if (project_dir / name).is_file():
            return load_config(project_dir / name)
    bundled = load_config(FILE_CONFIG)
    for name in _PROJECT_OTHER_CONFIGS:
        if (project_dir / name).is_file():
            note = f"{bundled.source}({project_dir / name} は JSON でないため使っていません)"
            return Config(bundled.data, note)
    return bundled


def display_path(file_path: str, base: Path) -> str:
    try:
        relative = os.path.relpath(file_path, base)
    except ValueError:
        return file_path
    return file_path if relative.startswith(os.pardir) else relative


def _ext(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    return suffix if suffix in LINTABLE_EXTENSIONS else ".md"


def _parse_message(fields: JsonObject) -> Message | None:
    rule_id, message = string(fields, "ruleId"), string(fields, "message")
    line, column, severity = integer(fields, "line"), integer(fields, "column"), integer(fields, "severity")
    if rule_id is None or message is None or line is None or column is None or severity is None:
        return None
    return Message(rule_id, message, line, column, severity)


def _parse_result(fields: JsonObject) -> DocumentResult:
    messages = (_parse_message(item) for item in objects(fields.get("messages")))
    return DocumentResult(
        file_path=string(fields, "filePath") or "",
        messages=tuple(message for message in messages if message is not None),
        output=string(fields, "output"),
    )


def lint_documents(
    documents: Iterable[Document],
    *,
    config: Config,
    env: Mapping[str, str],
    fix: bool = False,
) -> Report:
    bun = _bun(env)
    directory = ensure_installed(env)
    request = {
        "depsDir": str(directory),
        "fix": fix,
        "config": config.data,
        "items": [{"filePath": doc.file_path, "ext": _ext(doc.file_path), "text": doc.text} for doc in documents],
    }
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - bun は which で解決した絶対パス、ランナーは同梱のファイル
        [bun, *_BUN_RUN, str(RUNNER)],
        cwd=directory,
        input=json.dumps(request),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=dict(env),
    )
    response = parse_object(result.stdout) if result.returncode == 0 else None
    if response is None:
        msg = f"kernel ランナーが失敗しました\n{result.stderr or result.stdout}"
        raise TextlintError(msg)
    return Report(
        results=tuple(_parse_result(as_object(item) or {}) for item in items(response.get("results"))),
        ignored=tuple(name for name in items(response.get("ignored")) if isinstance(name, str)),
        source=config.source,
    )
