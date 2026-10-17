"""依存の準備・設定の選択・kernel ランナーの呼び出しをまとめた、フックと CLI の共通処理.

Claude Code・Codex・その他のエージェントのどこから呼ばれても動くよう、標準ライブラリだけを使い、
エージェント固有の環境変数には依存しない(あれば使う)。

検査そのものは kernel_runner.ts(@textlint/kernel)が行う。ランナーは同梱のルールだけを読み込み、
設定に書かれた名前からモジュールを探すことはしないので、プロジェクトの設定からコードが実行されることはない。
"""

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
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

SKILL_ROOT = Path(__file__).resolve().parent.parent
RUNNER = SKILL_ROOT / "scripts" / "kernel_runner.ts"
FILE_CONFIG = SKILL_ROOT / "config" / "textlintrc.json"
CHAT_CONFIG = SKILL_ROOT / "config" / "chat.json"
LINTABLE_EXTENSIONS = frozenset({".md", ".markdown", ".mdown", ".mkdn", ".txt", ".text"})
MINIMUM_BUN = (1, 3)
SEVERITY_ERROR = 2

_BUN_VERSION = re.compile(r"(\d+)\.(\d+)\.\d+")
# プロジェクトの設定のうち、JSON として自前で読めるもの。YAML・JS の設定は読まない。
_PROJECT_JSON_CONFIGS = (".textlintrc.json", ".textlintrc")
_PROJECT_OTHER_CONFIGS = (
    ".textlintrc.jsonc",
    ".textlintrc.yaml",
    ".textlintrc.yml",
    ".textlintrc.js",
    ".textlintrc.cjs",
)
# 依存のインストールスクリプトは任意のコードを実行できるので、動かさない。
_BUN_INSTALL = ("install", "--frozen-lockfile", "--production", "--ignore-scripts", "--no-progress", "--silent")
# bun はカレントディレクトリの bunfig.toml(preload など)と .env を読む。プロジェクトのコードや設定が混ざらないよう、
# bun は自分で管理するディレクトリで起動し、.env も読ませない。
_BUN_RUN = ("--no-env-file",)


class TextlintError(Exception):
    """bun・依存・設定を用意できないとき、kernel ランナーが失敗したときの誤り.

    フックはこれを標準エラーに出して、エージェントの作業を止めない。
    """


@dataclass(frozen=True)
class Document:
    """検査する本文. file_path は表示と拡張子の判定に使う."""

    file_path: str
    text: str


@dataclass(frozen=True)
class Message:
    """kernel ランナーが返した指摘1件. severity は 1 = warning、2 = error、3 = info."""

    rule_id: str
    message: str
    line: int
    column: int
    severity: int


@dataclass(frozen=True)
class DocumentResult:
    """1つの本文の検査結果. output は修正したときだけ入る."""

    file_path: str
    messages: tuple[Message, ...]
    output: str | None


@dataclass(frozen=True)
class Report:
    """lint_documents の結果. ignored は同梱にないため無視した設定の名前、source は表示用の設定の出どころ."""

    results: tuple[DocumentResult, ...]
    ignored: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class Config:
    """設定の中身(kernel ランナーへそのまま渡す)と、表示用の出どころ."""

    data: dict[str, object]
    source: str


def deps_dir(env: Mapping[str, str]) -> Path:
    """依存の置き場所を決める.

    ロックファイルのハッシュごとに分けるので、更新時は新しいディレクトリへ入る。

    Returns:
        TEXTLINT_SKILL_DEPS_DIR があればそれ、なければキャッシュディレクトリの下のパス。
    """
    override = env.get("TEXTLINT_SKILL_DEPS_DIR")
    if override:
        return Path(override)
    digest = hashlib.sha256((SKILL_ROOT / "bun.lock").read_bytes()).hexdigest()[:12]
    cache = Path(env.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache / "ja-writing-textlint" / digest


def installed(directory: Path) -> bool:
    """依存を入れ終えたディレクトリかを判定する.

    ensure_installed は一時ディレクトリで bun install が成功してから名前を変えてここへ置くので、
    @textlint/kernel があれば入れ終えている。

    Returns:
        @textlint/kernel が入っていれば True。
    """
    return (directory / "node_modules" / "@textlint" / "kernel" / "package.json").is_file()


def _which(command: str, env: Mapping[str, str]) -> str:
    found = shutil.which(command, path=env.get("PATH"))
    if found is None:
        msg = f"{command} が見つかりません"
        raise TextlintError(msg)
    return found


def ensure_installed(env: Mapping[str, str]) -> Path:
    """依存がなければ bun install で入れる.

    Returns:
        依存を入れたディレクトリ。

    Raises:
        TextlintError: bun が見つからないか古いとき、bun install が失敗したとき。
        OSError: 入れた依存を置けず、別のプロセスも入れ終えていないとき。
    """
    directory = deps_dir(env)
    if installed(directory):
        return directory

    bun = _bun(env)
    directory.parent.mkdir(parents=True, exist_ok=True)
    # 途中で失敗しても壊れたディレクトリを残さないよう、同じ場所の一時ディレクトリに入れてから置き換える。
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
        # 依存は全セッション・全エージェントで共有するので、別のプロセスが先に入れ終えていることがある。
        # 完成済みのディレクトリは消さず、自分の一時ディレクトリを捨てる。
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


def _as_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in cast("dict[object, object]", value).items()}


def _as_list(value: object) -> list[object]:
    return list(cast("list[object]", value)) if isinstance(value, list) else []


def load_config(path: Path) -> Config:
    """JSON の設定ファイルを読む.

    Returns:
        出どころをファイルのパスにした設定。

    Raises:
        TextlintError: 読めないか、JSON のオブジェクトでないとき。
    """
    try:
        data = _as_dict(cast("object", json.loads(path.read_text(encoding="utf-8"))))
    except (OSError, ValueError) as error:
        msg = f"設定 {path} を読めません: {error}"
        raise TextlintError(msg) from error
    if data is None:
        msg = f"設定 {path} が JSON のオブジェクトではありません"
        raise TextlintError(msg)
    return Config(data, str(path))


def choose_file_config(project_dir: Path, env: Mapping[str, str]) -> Config:
    """ファイルの検査に使う設定を選ぶ.

    1. TEXTLINT_CONFIG(JSON)
    2. プロジェクトの .textlintrc.json か .textlintrc(JSON)
    3. 同梱の config/textlintrc.json

    Returns:
        選んだ設定。プロジェクトに JSON 以外の設定しかなければ、同梱の設定にその旨を添える。
    """
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
    """表示用のパスを作る.

    Returns:
        base の中のファイルなら base からの相対パス。外のファイル(../ をたどる)や、
        Windows でドライブが違うときは、渡されたパスのまま。
    """
    try:
        relative = os.path.relpath(file_path, base)
    except ValueError:
        return file_path
    return file_path if relative.startswith(os.pardir) else relative


def _ext(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    return suffix if suffix in LINTABLE_EXTENSIONS else ".md"


def _parse_message(value: object) -> Message | None:
    fields = _as_dict(value) or {}
    rule_id, message = fields.get("ruleId"), fields.get("message")
    line, column, severity = fields.get("line"), fields.get("column"), fields.get("severity")
    if not (isinstance(rule_id, str) and isinstance(message, str)):
        return None
    if not (isinstance(line, int) and isinstance(column, int) and isinstance(severity, int)):
        return None
    return Message(rule_id, message, line, column, severity)


def _parse_result(value: object) -> DocumentResult:
    fields = _as_dict(value) or {}
    output = fields.get("output")
    messages = (_parse_message(item) for item in _as_list(fields.get("messages")))
    return DocumentResult(
        file_path=str(fields.get("filePath", "")),
        messages=tuple(message for message in messages if message is not None),
        output=output if isinstance(output, str) else None,
    )


def lint_documents(
    documents: Iterable[Document],
    *,
    config: Config,
    env: Mapping[str, str],
    fix: bool = False,
) -> Report:
    """本文を kernel ランナーで検査する. fix が真なら修正した本文も返す.

    Returns:
        本文ごとの結果、設定のうち同梱にないため無視した名前、設定の出どころ。

    Raises:
        TextlintError: bun や依存を用意できないか、ランナーが失敗したとき。
    """
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
    try:
        response = _as_dict(cast("object", json.loads(result.stdout))) if result.returncode == 0 else None
    except ValueError:
        response = None
    if response is None:
        msg = f"kernel ランナーが失敗しました\n{result.stderr or result.stdout}"
        raise TextlintError(msg)
    return Report(
        results=tuple(_parse_result(item) for item in _as_list(response.get("results"))),
        ignored=tuple(str(name) for name in _as_list(response.get("ignored"))),
        source=config.source,
    )
