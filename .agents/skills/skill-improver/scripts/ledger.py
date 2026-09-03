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
import contextlib
import datetime as _dt
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

try:  # POSIX のみ。Windows では advisory lock を諦めて no-op に落とす
    import fcntl
except ImportError:  # pragma: no cover - POSIX 以外でのみ通る
    fcntl = None  # type: ignore[assignment]

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
#: 上流 (`retro` / `session-retro`) が使う lever 名 → 台帳の語彙。
#: 上流の呼び名をそのまま渡して argparse に弾かれると、finding が記録されずに落ちる。
#: 語彙を 1 つに矯正するより、入口で受けて保存時に正規化する方が両者を壊さない。
LEVER_ALIASES: dict[str, str] = {"ept-handoff": "ept"}
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

#: id は「作成日 + (target_skill, 再発クラス) の要約」から決まる内容由来の値。
#: 連番にすると、同じ週に複数 finding を処理する実行で「台帳を読んで次番号を決める」
#: 経路が枝ごとに並走し、default branch を見ていない枝が同じ番号を採ってしまう
#: (1 finding = 1 PR で台帳行も PR ごとに append されるため、採番は必ず衝突する)。
ID_RE = re.compile(r"^IMP-(\d{8})-([0-9a-f]{10})$")

#: `pr` フィールドが取りうる形。Step 0 の突き合わせはこの URL を叩いて PR の
#: 実状態を見るので、空文字やブランチ名のような「開けない値」を保存させない。
#: ホスト名は縛らない (GitHub Enterprise でも同じ経路が要る)。
PR_URL_RE = re.compile(r"^https://\S+/pull/\d+$")
CREATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: hash 部の桁数。日付が前置されるので衝突は「同じ日の別 finding どうし」でしか
#: 起きないが、6 桁 (24 bit) だと運用年数ぶん積み上げたときに無視できない。
ID_HASH_LEN = 10


# --- pure functions (単体テスト対象) --------------------------------------


def normalize_skill_name(skill: str) -> str:
    """skill 指定を比較用の名前に正規化する (純関数)。

    `skills/retro` や `Retro` のような書き方でメタスキル判定をすり抜けられると
    除外リストが飾りになるため、パス末尾の要素だけを取り出して casefold する。
    """
    return Path(skill.strip()).name.strip().casefold()


def normalize_lever(lever: str) -> str:
    """上流の lever 名を台帳の語彙に落とす (純関数)。別名でなければそのまま返す。"""
    key = lever.strip()
    return LEVER_ALIASES.get(key, key)


def classify_target(skill: str, known_skills: Iterable[str] | None) -> str:
    """改善対象 skill を分類する。

    戻り値: "excluded_meta" | "unknown" | "unresolved" | "ok"
    known_skills が None のときは skill ディレクトリを解決できなかった状態を表し
    "unresolved" を返す (存在しない skill を「あった」と誤認しない fail-closed)。
    メタスキル判定は known_skills に依存せず、表記揺れ (`Retro` / `skills/retro`) も
    同じメタスキルとして弾く (ハードコードされた不変条件)。既知判定も同じ正規化で
    行う — メタ側だけ正規化すると `skills/tdd` が unknown に落ち、
    `--allow-unknown-target` と併せて正規化前の文字列が台帳に入り、
    `recurrence_for` / `report --skill` の突き合わせが一致しなくなる。
    """
    normalized = normalize_skill_name(skill)
    if normalized in META_SKILLS:
        return "excluded_meta"
    if known_skills is None:
        return "unresolved"
    return "ok" if normalized in {normalize_skill_name(k) for k in known_skills} else "unknown"


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


def validate_created(created: str) -> str:
    """`created` が実在する YYYY-MM-DD であることを確かめて返す (純関数)。

    `derive_id` の中だけで検査していると、`--id` を明示したときに検査が丸ごと
    飛んで `2026-99-99` がそのまま台帳の `created` に入る。日付は id とは独立に
    「いつの finding か」を持つフィールドなので、採番経路と切り離して常に通す。
    """
    if not CREATED_RE.match(created):
        raise ValueError(f"created は YYYY-MM-DD 形式で指定する: {created!r}")
    try:
        _dt.date.fromisoformat(created)
    except ValueError as exc:
        raise ValueError(f"created が実在しない日付: {created!r}") from exc
    return created


def derive_id(target_skill: str, class_key: str, created: str) -> str:
    """内容由来の id `IMP-<YYYYMMDD>-<sha1 先頭 10 桁>` を返す (純関数)。

    材料は作成日と `target_skill` + 改行 + 再発クラスキーだけ。台帳の既存行を
    読まないので、複数の finding を別ブランチで並行に処理しても採番が競合しない
    (連番だと、それぞれの枝が「自分の見た台帳の最大値 + 1」を採って必ず衝突する)。
    同じ日・同じ skill・同じクラスなら同じ id になる — それは同一 finding の
    二重登録であり、`add` が重複として弾く。
    """
    validate_created(created)
    digest = hashlib.sha1(  # noqa: S324 - 衝突耐性ではなく短い安定 id が目的
        f"{target_skill}\n{class_key}".encode()
    ).hexdigest()[:ID_HASH_LEN]
    return f"IMP-{created.replace('-', '')}-{digest}"


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
    pr: str = "",
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
        "pr": pr or None,
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
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"metric {key} の値が数値でない: {raw!r}") from exc
    # float() は "nan" / "inf" / "-Infinity" も通す。台帳に入ると delta が nan / inf に
    # なり、比較が全て False に倒れて悪化を見逃す (revert candidate の検出が壊れる)。
    if not math.isfinite(value):
        raise ValueError(f"metric {key} の値が有限数でない: {raw!r}")
    return key, value


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
    """台帳ファイルを読み、1 行 1 エントリの list にする (壊れた行は例外)。"""
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
    """台帳を丸ごと書き直す (同一ディレクトリの一時ファイル経由で原子的に差し替える)。"""
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


@contextlib.contextmanager
def ledger_lock(path: Path) -> Iterator[None]:
    """台帳の read-modify-write 全体を排他する (advisory lock)。

    `save_entries` の `os.replace` が守るのは「壊れたファイルを残さない」ことだけで、
    読んでから書くまでの間に別プロセスが書いた更新は上書きで消える。workflow 実行
    どうしは Actions の `concurrency` グループが直列化するが、手元で 2 つ走らせた
    ときはそれが効かないので、ここで `<ledger>.lock` を掴む。
    `fcntl` の無い環境では諦めて素通しする (壊すより、守れない環境で動く方を採る)。
    """
    if fcntl is None:  # pragma: no cover - POSIX 以外でのみ通る
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def pr_url_problem(pr: str) -> str | None:
    """`pr` に保存してよい URL かを見て、駄目なら理由を返す (純関数)。"""
    if not pr.strip():
        return "pr が空"
    if not PR_URL_RE.match(pr.strip()):
        return f"pr {pr!r} が PR URL の形をしていない (https://.../pull/<番号>)"
    return None


def has_usable_pr(entry: dict[str, Any]) -> bool:
    """その行の `pr` が実際に開ける PR URL かを返す (純関数)。

    「空でない」だけでは足りない。ブランチ名や書きかけの文字列が入っていても
    真になってしまい、Step 2.5 はそれを「PR が実在する」と読んで finding を
    抑止する一方、Step 0 はその値で PR を引けない。
    """
    return pr_url_problem(str(entry.get("pr") or "")) is None


def is_pending_row(entry: dict[str, Any]) -> bool:
    """その行が「もう PR として動いている finding」かを返す (純関数)。

    Step 2.5 (未 merge の improve/* PR の台帳を読む) で、新規候補を抑止してよいのは
    次の 2 つだけ:

    - `status == "pr_open"` — PR が現に開いている
    - `status == "proposed"` かつ `pr` が入っている — PR は作られたが `set-status` の
      前に落ちた残骸 (`link-pr --keep-status` 直後の状態)。PR は実在するので、
      新規候補として二重に起票してはいけない

    どちらも **`pr` が実際に開ける PR URL であること**が条件。空でも、ブランチ名の
    ような値でも pending にしない — 追える PR が無いのに pending として数えると、
    Step 0 はその行を決着させる手段が無く、Step 2.5 は同じ finding を永久に抑止
    する。そういう行は `is_inconsistent_row` で拾って修復する。

    merged / rejected / reverted は過去の記録なので、本当の再発を抑止しないよう
    pending に数えない。
    """
    if entry.get("status") not in ("pr_open", "proposed"):
        return False
    return has_usable_pr(entry)


def is_inconsistent_row(entry: dict[str, Any]) -> bool:
    """`pr` が辿れないのに PR があるかのように見える行かを返す (純関数)。

    2 つの形がある:

    - `status == "pr_open"` なのに `pr` が空 — 決着させる URL が無い
    - `pr` が空でないのに PR URL の形をしていない — 引けない値を指している

    書き込み経路 (`add` / `set-status` / `link-pr`) はどちらも作らせないが、
    過去の実行が残した行や手編集で入り込みうる。放置すると finding が永久に
    抑止されるので、Step 0 が `list --inconsistent` で列挙して修復する。
    """
    pr = str(entry.get("pr") or "").strip()
    if not pr:
        return entry.get("status") == "pr_open"
    return pr_url_problem(pr) is not None


def missing_after(entries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """merged なのに after 指標が 1 つも無いエントリを返す (純関数)。

    after が取れないまま merged にすると、そのエントリは `report` の delta にも
    revert candidate にも現れず、突き合わせ済みという理由で `pr_open` の列挙からも
    外れる — 改善の効果が測られないまま静かに消える唯一の経路なので、明示的に拾う。
    """
    return [
        entry
        for entry in entries
        if entry.get("status") == "merged" and not (entry.get("after") or {})
    ]


def find_entry(entries: Sequence[dict[str, Any]], entry_id: str) -> dict[str, Any]:
    """id でエントリを引く。無ければ ValueError (黙って作り直さない)。"""
    for entry in entries:
        if entry.get("id") == entry_id:
            return entry
    raise ValueError(f"id {entry_id!r} が台帳に無い")


def ledger_path(args: argparse.Namespace) -> Path:
    """使う台帳のパスを決める (--ledger 優先、既定は repo root の規約パス)。"""
    if args.ledger:
        return Path(args.ledger)
    return find_repo_root() / LEDGER_RELPATH


def skills_root(args: argparse.Namespace) -> Path:
    """target skill を探す起点を返す。

    `--ledger` で別リポジトリの台帳を指したのに skill の実在確認だけ cwd の
    チェックアウトで行うと、別カタログの skill 名で「既知」と判定してしまう。
    台帳は `<repo>/improvements/ledger.jsonl` に置く規約なので、その 2 つ上を
    対象リポジトリの root とみなす。規約から外れた場所を使うときは
    `--skills-root` で明示する。
    """
    if getattr(args, "skills_root", None):
        return Path(args.skills_root)
    if args.ledger:
        return Path(args.ledger).resolve().parent.parent
    return find_repo_root()


def today() -> str:
    """今日の日付を UTC の YYYY-MM-DD で返す (実行環境の TZ に依存させない)。"""
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
    """finding を 1 件記録する (メタスキル除外・id の形式/重複検査を通す)。"""
    path = ledger_path(args)
    entries = load_entries(path)
    root = skills_root(args)
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

    # --id を渡しても検査は飛ばさない (created は id とは独立のフィールド)
    created = validate_created(args.created or today())
    class_key = args.finding_class.strip() or finding_class(args.finding)
    # 台帳に入れるのは正規化済みの skill 名。`skills/tdd` のような書き方をそのまま
    # 保存すると、同じ skill が 2 つのキーに割れて recurrence が伸びなくなる。
    target = normalize_skill_name(args.target)
    entry_id = args.id.strip() if args.id else derive_id(target, class_key, created)
    # id は find_entry / link-pr / set-status の宛先そのもの。形式違反や重複を
    # 通すと、後続の更新が「最初に一致した行」に当たって別 finding を書き換える。
    if not ID_RE.match(entry_id):
        print(
            f"error: id {entry_id!r} が形式に合わない (IMP-YYYYMMDD-xxxxxx、"
            "末尾は 16 進 10 桁)",
            file=sys.stderr,
        )
        return EXIT_FAIL
    if any(entry.get("id") == entry_id for entry in entries):
        print(
            f"error: id {entry_id} は台帳に既にある。同じ日・同じ skill・同じ"
            " finding クラスの二重登録なら記録は不要 — 既存エントリを更新すること"
            " (別 finding なら --class で別クラスキーを付けるか --id で明示する)",
            file=sys.stderr,
        )
        return EXIT_FAIL

    # `pr_open` は「追える PR がある」ことを意味する status。URL 無しで記録すると
    # Step 0 は決着させる手段が無く、Step 2.5 はその finding を永久に抑止する。
    pr = (args.pr or "").strip()
    # status を問わず、保存する `pr` は必ず開ける URL であること。`proposed` でも
    # `pr` が入っていれば Step 2.5 は pending として finding を抑止するので、
    # 形の検査を pr_open だけに掛けると同じ穴が残る。
    if pr:
        problem = pr_url_problem(pr)
        if problem is not None:
            print(f"error: {problem}", file=sys.stderr)
            return EXIT_FAIL
    elif status == "pr_open":
        print("error: --status pr_open には --pr が要る (pr が空)", file=sys.stderr)
        return EXIT_FAIL

    entry = new_entry(
        entry_id=entry_id,
        created=created,
        source=args.source,
        evidence=args.evidence,
        target_skill=target,
        finding=args.finding,
        lever=normalize_lever(args.lever),
        status=status,
        recurrence=recurrence_for(entries, target, args.finding, args.finding_class),
        class_key=args.finding_class,
        notes=args.notes,
        pr=pr,
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
    """エントリの status を更新する (メタスキルは記録/却下への更新だけ通す)。"""
    path = ledger_path(args)
    entries = load_entries(path)
    entry = find_entry(entries, args.id)
    blocked = guard_meta(entry, args.status)
    if blocked is not None:
        return blocked
    pr = (args.pr or "").strip()
    if pr and args.clear_pr:
        print("error: --pr と --clear-pr は同時に使えない", file=sys.stderr)
        return EXIT_FAIL
    # `pr` の無い `pr_open` は書き込み経路が作ってはいけない形 (Step 0 は決着させる
    # URL を持たず、Step 2.5 はその finding を pending として永久に抑止する)。
    # 片方ずつは正当な操作なので、組み合わせだけを **書き換える前に** 拒否する。
    if args.clear_pr and args.status == "pr_open":
        print(
            "error: --status pr_open と --clear-pr は同時に使えない"
            " (pr の無い pr_open は決着させる URL が無く、その finding を永久に"
            "抑止する)。pr を消すなら --status proposed に戻すこと",
            file=sys.stderr,
        )
        return EXIT_FAIL
    # status を問わず、保存する `pr` は必ず開ける URL であること。
    if pr:
        problem = pr_url_problem(pr)
        if problem is not None:
            print(f"error: {problem}", file=sys.stderr)
            return EXIT_FAIL
    if args.status == "pr_open":
        # 既に紐付いているならそれを使い、無ければ --pr で渡させる。
        candidate = pr or str(entry.get("pr") or "")
        problem = pr_url_problem(candidate)
        if problem is not None:
            print(
                f"error: status を pr_open にするには PR URL が要る ({problem})。"
                "--pr で渡すか、先に link-pr で紐付けること",
                file=sys.stderr,
            )
            return EXIT_FAIL
    if pr:
        entry["pr"] = pr
    elif args.clear_pr:
        # Step 0 の修復経路: 辿れない `pr` を消して finding を出し直せる形に戻す。
        entry["pr"] = None
    entry["status"] = args.status
    if args.notes:
        entry["notes"] = args.notes
    save_entries(path, entries)
    print(json.dumps(entry, ensure_ascii=False))
    return EXIT_OK


def cmd_link_pr(args: argparse.Namespace) -> int:
    """エントリに PR URL を紐付ける (既定で status を pr_open にする)。"""
    path = ledger_path(args)
    entries = load_entries(path)
    entry = find_entry(entries, args.id)
    blocked = guard_meta(entry)
    if blocked is not None:
        return blocked
    pr = args.pr.strip()
    problem = pr_url_problem(pr)
    if problem is not None:
        print(f"error: {problem}", file=sys.stderr)
        return EXIT_FAIL
    entry["pr"] = pr
    if not args.keep_status:
        entry["status"] = "pr_open"
    save_entries(path, entries)
    print(json.dumps(entry, ensure_ascii=False))
    return EXIT_OK


def cmd_record_metrics(args: argparse.Namespace) -> int:
    """before / after の指標を記録し、両相が揃った指標の delta を表示する。"""
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


#: reconcile ブランチが台帳の既存行に対して変えてよいフィールド。
#: これ以外 (finding / target_skill / lever / evidence / recurrence 等) が動いていたら、
#: 突き合わせに見せかけた履歴の書き換えである。
RECONCILE_MUTABLE_FIELDS: frozenset[str] = frozenset({"status", "pr", "after", "notes"})

#: 突き合わせで許す status 遷移。それ以外は人間の判断を要する。
#: proposed からの 3 つは **PR 起票後に link-pr / 補償が落ちた行の回収経路**。
#: base 側の pr が空でも (publish の補償が push できなかった残骸)、既に PR URL を
#: 持っていても (link-pr --keep-status までは通った残骸) 同じ扱いにする —
#: どちらも「PR は実在するが台帳が追い付いていない」行で、Step 0 は head branch
#: improve/<skill>-<id> の PR を全状態で引いて open / merged / closed に決着させる。
#: 条件は **head 側に開ける PR URL が入っていること** (check_reconcile_diff が動的に
#: 見る)。PR を名指しできないまま merged / rejected に進めるのは、突き合わせでは
#: なく台帳の書き換えなので通さない。
#: pr_open -> proposed は表に持たない — pr が空の行に限って
#: check_reconcile_diff が動的に足す (辿れない行を出し直すための修復経路)。
ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "pr_open": frozenset({"merged", "rejected"}),
    "merged": frozenset({"reverted"}),
    "proposed": frozenset({"pr_open", "merged", "rejected"}),
}


def index_by_id(entries: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """id をキーにしたインデックスを返す (純関数)。重複 id は最初の行を採る。"""
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        key = str(entry.get("id", ""))
        indexed.setdefault(key, entry)
    return indexed


def duplicate_ids(entries: Sequence[dict[str, Any]]) -> list[str]:
    """同じ id が 2 行以上あるならその id を返す (純関数)。

    `index_by_id` は id ごとに最初の行だけを採るので、重複があると 2 行目以降が
    差分の計算から消える。「追加は 1 行だけ」の検査は、同じ id を 2 行書けば
    1 行分しか見えないまますり抜けてしまうため、重複そのものを違反として扱う。
    """
    seen: dict[str, int] = {}
    for entry in entries:
        key = str(entry.get("id", ""))
        seen[key] = seen.get(key, 0) + 1
    return sorted(key for key, count in seen.items() if count > 1)


def _duplicate_problems(
    base: Sequence[dict[str, Any]], head: Sequence[dict[str, Any]]
) -> list[str]:
    """base / head 双方の id 重複を違反として並べる。

    base 側の重複は「信頼しているはずの台帳が既に壊れている」状態なので、
    こちらも通さない (fail-closed)。
    """
    problems: list[str] = []
    for label, entries in (("base", base), ("head", head)):
        dupes = duplicate_ids(entries)
        if dupes:
            problems.append(f"{label} に id の重複がある ({', '.join(dupes)})")
    return problems


def diff_ledgers(
    base: Sequence[dict[str, Any]], head: Sequence[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[tuple[dict, dict]]]:
    """base → head の (added, removed, modified) を id 基準で返す (純関数)。"""
    base_idx = index_by_id(base)
    head_idx = index_by_id(head)
    added = [e for k, e in head_idx.items() if k not in base_idx]
    removed = [e for k, e in base_idx.items() if k not in head_idx]
    modified = [
        (base_idx[k], head_idx[k])
        for k in head_idx
        if k in base_idx and base_idx[k] != head_idx[k]
    ]
    return added, removed, modified


def _proposed_entry_problems(entry: dict[str, Any], label: str) -> list[str]:
    """新規追加行が「これから PR にする finding」の形をしているかを検査する。"""
    problems: list[str] = []
    entry_id = str(entry.get("id", ""))
    if not ID_RE.match(entry_id):
        problems.append(f"{label}: id が形式に合わない ({entry_id!r})")
    if entry.get("status") != "proposed":
        problems.append(f"{label}: status が proposed でない ({entry.get('status')!r})")
    if entry.get("pr") is not None:
        problems.append(f"{label}: pr が設定されている ({entry.get('pr')!r})")
    return problems


def check_candidate_diff(
    base: Sequence[dict[str, Any]],
    head: Sequence[dict[str, Any]],
    ledger_id: str,
) -> list[str]:
    """改善ブランチの台帳差分を検査する (純関数)。

    改善ブランチが触ってよいのは **自分の finding 1 行の追加だけ**。パスの
    allow-list は `improvements/ledger.jsonl` 全体を許してしまうので、行の粒度でも
    見ないと、1 行足すついでに他の行 (別 skill の merged 記録など) を書き換えられる。
    """
    problems = _duplicate_problems(base, head)
    if problems:
        return problems
    added, removed, modified = diff_ledgers(base, head)
    if removed:
        problems.append(
            f"既存の行が消えている ({', '.join(str(e.get('id')) for e in removed)})"
        )
    if modified:
        problems.append(
            f"既存の行が書き換わっている ({', '.join(str(b.get('id')) for b, _ in modified)})"
        )
    if len(added) != 1:
        problems.append(f"追加行はちょうど 1 行でなければならない (実際: {len(added)})")
        return problems
    entry = added[0]
    problems.extend(_proposed_entry_problems(entry, "追加行"))
    if str(entry.get("id", "")) != ledger_id:
        problems.append(
            f"追加行の id が manifest の ledger_id と一致しない"
            f" ({entry.get('id')!r} != {ledger_id!r})"
        )
    return problems


def check_reconcile_diff(
    base: Sequence[dict[str, Any]], head: Sequence[dict[str, Any]]
) -> list[str]:
    """突き合わせブランチの台帳差分を検査する (純関数)。

    許すのは「決着した PR の status / pr / after / notes を進める」ことだけ。
    finding 本文や target_skill が動いていたら、それは突き合わせではない。

    `proposed` の行を進められるのは **head 側に開ける PR URL がある**ときだけ。
    publish の補償が落ちた残骸 (`pr` が空 / `link-pr --keep-status` まで通った行)
    を Step 0 が全状態の PR 検索で回収するための経路で、PR を名指しできないまま
    決着させることは許さない。
    """
    problems = _duplicate_problems(base, head)
    if problems:
        return problems
    added, removed, modified = diff_ledgers(base, head)
    if removed:
        problems.append(
            f"既存の行が消えている ({', '.join(str(e.get('id')) for e in removed)})"
        )
    for before, after in modified:
        entry_id = str(before.get("id"))
        changed = {
            key
            for key in set(before) | set(after)
            if before.get(key) != after.get(key)
        }
        illegal = sorted(changed - RECONCILE_MUTABLE_FIELDS)
        if illegal:
            problems.append(f"{entry_id}: 変更してはいけない項目 ({', '.join(illegal)})")
        # 触った行に、開けない `pr` を残させない。空でない限り形を検査する
        # (空に戻すのは辿れない行を出し直すための修復経路なので許す)。
        head_pr = str(after.get("pr") or "").strip()
        if head_pr:
            problem = pr_url_problem(head_pr)
            if problem is not None:
                problems.append(f"{entry_id}: {problem}")
        old_status = str(before.get("status", ""))
        new_status = str(after.get("status", ""))
        if old_status != new_status:
            allowed = ALLOWED_STATUS_TRANSITIONS.get(old_status, frozenset())
            # 追える PR が無い pr_open は決着させようがない (pr が空でも、
            # 開けない値が入っていても同じ)。対応する PR が見つからなかった
            # 場合に限り、finding を出し直せるよう proposed へ戻すことを許す。
            # 開ける PR URL を持つ pr_open では許さない。
            if old_status == "pr_open" and not has_usable_pr(before):
                allowed = allowed | {"proposed"}
            if new_status not in allowed:
                problems.append(
                    f"{entry_id}: 許されない status 遷移 ({old_status} -> {new_status})"
                )
            elif old_status == "proposed" and not has_usable_pr(after):
                # proposed からの遷移は「PR が実在した」ことの記録に限る。
                # head 側に開ける PR URL が無いまま pr_open / merged / rejected へ
                # 進めるのは、突き合わせに見せかけた status の書き換え。
                problems.append(
                    f"{entry_id}: proposed からの遷移には head 側に開ける PR URL が要る"
                    f" ({old_status} -> {new_status})"
                )
    for entry in added:
        problems.extend(_proposed_entry_problems(entry, f"追加行 {entry.get('id')}"))
    return problems


def cmd_verify_diff(args: argparse.Namespace) -> int:
    """base と head の台帳を突き合わせ、ブランチが許された変更だけをしているか見る。"""
    base = load_entries(Path(args.base)) if args.base else []
    head = load_entries(Path(args.head))
    if args.mode == "candidate":
        if not args.ledger_id:
            print("error: --mode candidate には --ledger-id が要る", file=sys.stderr)
            return EXIT_FAIL
        problems = check_candidate_diff(base, head, args.ledger_id)
    else:
        problems = check_reconcile_diff(base, head)
    if problems:
        print(f"verify-diff: {len(problems)} 件の違反")
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_FAIL
    print("verify-diff: ok")
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
            # merge されたものだけが revert しうる。rejected / reverted まで並べると、
            # 既に取り消した差分の revert を毎回要求し続けることになる。
            if entry.get("status") == "merged" and is_revert_candidate(entry)
        ],
        # after を取り損ねた merged は delta にも revert candidate にも出ない。
        # ここで名指ししないと「効果が測られないまま完了扱い」で静かに消える。
        "merged_without_after": [
            {
                "id": entry.get("id"),
                "target_skill": entry.get("target_skill"),
                "pr": entry.get("pr"),
            }
            for entry in missing_after(entries)
        ],
    }


def cmd_report(args: argparse.Namespace) -> int:
    """再発クラス・指標の delta・revert candidate を人間向け / JSON で出力する。"""
    path = ledger_path(args)
    entries = load_entries(path)
    if args.skill:
        # `skills/tdd` のような書き方でも同じ skill として絞る。生の文字列比較だと
        # add 側が正規化して保存しているぶんと食い違い、静かに 0 件になる。
        wanted_skill = normalize_skill_name(args.skill)
        entries = [
            e
            for e in entries
            if normalize_skill_name(str(e.get("target_skill", ""))) == wanted_skill
        ]
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
            # クラスキーを出さないと、再発ゲート (SKILL.md Step 2) で
            # `add --class <key>` に渡す既存キーが読み取れず、言い換えのたびに
            # 別クラスへ分裂して recurrence が伸びなくなる。
            for key, count in sorted(bucket["classes"].items()):
                print(f"    class {key}: {count}")
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
        print(
            f"## Merged without after metrics ({len(report['merged_without_after'])})"
        )
        if not report["merged_without_after"]:
            print("  (なし)")
        for item in report["merged_without_after"]:
            print(f"  {item['id']} {item['target_skill']} (pr={item['pr']})")
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


def cmd_list(args: argparse.Namespace) -> int:
    """台帳のエントリを status で絞って並べる (Step 0 の reconcile 用)。

    `report` は集計しか出さないため、「まだ pr_open のまま残っている PR はどれか」を
    機械的に取り出せない。実行ごとの突き合わせはこのサブコマンドを起点にする。
    """
    path = ledger_path(args)
    entries = load_entries(path)
    if args.missing_after:
        entries = missing_after(entries)
    if args.inconsistent:
        entries = [e for e in entries if is_inconsistent_row(e)]
    if args.status:
        wanted = set(args.status)
        entries = [e for e in entries if str(e.get("status")) in wanted]
    if args.skill:
        # `skills/tdd` のような書き方でも同じ skill として絞る。生の文字列比較だと
        # add 側が正規化して保存しているぶんと食い違い、静かに 0 件になる。
        wanted_skill = normalize_skill_name(args.skill)
        entries = [
            e
            for e in entries
            if normalize_skill_name(str(e.get("target_skill", ""))) == wanted_skill
        ]

    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return EXIT_OK

    if not entries:
        print("  (該当なし)")
        return EXIT_OK
    for entry in entries:
        print(
            f"  {entry.get('id')} {entry.get('status')} {entry.get('target_skill')}"
            f" pr={entry.get('pr')} created={entry.get('created')}"
        )
    return EXIT_OK


def cmd_check_target(args: argparse.Namespace) -> int:
    """skill を改善対象にしてよいかだけを判定する (台帳は書かない)。"""
    root = skills_root(args)
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
    """サブコマンドを持つ CLI パーサを組み立てる。"""
    parser = argparse.ArgumentParser(
        prog="ledger.py",
        description="skill-improver の改善台帳 (improvements/ledger.jsonl) を操作する",
    )
    parser.add_argument(
        "--ledger",
        help="台帳のパス (既定: <repo root>/improvements/ledger.jsonl)",
    )
    parser.add_argument(
        "--skills-root",
        dest="skills_root",
        help=(
            "target skill を探すリポジトリ root"
            " (既定: --ledger の 2 つ上、--ledger 省略時は cwd から見た repo root)"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="finding を 1 件記録する")
    add.add_argument("--source", required=True, choices=SOURCES)
    add.add_argument("--target", required=True, help="対象 skill 名")
    add.add_argument("--finding", required=True, help="finding を 1 文で")
    add.add_argument(
        "--lever",
        required=True,
        choices=(*LEVERS, *LEVER_ALIASES),
        help=(
            f"改善の手段。{', '.join(LEVERS)} を受け付ける。上流の呼び名 "
            f"({', '.join(f'{k} -> {v}' for k, v in LEVER_ALIASES.items())}) も"
            "そのまま渡せる (保存時に正規化する)"
        ),
    )
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
    add.add_argument(
        "--pr",
        default="",
        help=(
            "PR URL (--status pr_open で記録するときは必須)。"
            "status を問わず https://.../pull/<番号> の形であること"
        ),
    )
    add.add_argument("--notes", default="")
    add.add_argument(
        "--id",
        help=(
            "id を明示する (既定: IMP-<YYYYMMDD>-<target_skill+クラスの sha1 先頭 10 桁>)。"
            "形式違反と重複は拒否する"
        ),
    )
    add.add_argument("--created", help="作成日 YYYY-MM-DD (既定: 今日 UTC)")
    add.add_argument(
        "--allow-unknown-target",
        action="store_true",
        help="skill ディレクトリで解決できない target でも記録する",
    )
    add.set_defaults(func=cmd_add, locks=True)

    set_status = sub.add_parser("set-status", help="status を更新する")
    set_status.add_argument("--id", required=True)
    set_status.add_argument("--status", required=True, choices=STATUSES)
    set_status.add_argument(
        "--pr",
        default="",
        help="PR URL を同時に紐付ける (pr_open にする行に PR URL が無いときは必須)",
    )
    set_status.add_argument(
        "--clear-pr",
        dest="clear_pr",
        action="store_true",
        help="紐付いている pr を空に戻す (Step 0 が辿れない行を修復するときに使う)",
    )
    set_status.add_argument("--notes", default="")
    set_status.set_defaults(func=cmd_set_status, locks=True)

    link = sub.add_parser("link-pr", help="PR URL を紐付ける (既定で status=pr_open)")
    link.add_argument("--id", required=True)
    link.add_argument("--pr", required=True)
    link.add_argument("--keep-status", action="store_true", help="status を変えない")
    link.set_defaults(func=cmd_link_pr, locks=True)

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
    metrics.set_defaults(func=cmd_record_metrics, locks=True)

    report = sub.add_parser("report", help="再発回数と before->after の delta を集計する")
    report.add_argument("--skill", help="対象 skill で絞る")
    report.add_argument("--json", action="store_true")
    report.add_argument(
        "--fail-on-revert",
        action="store_true",
        help="revert candidate があれば exit 1",
    )
    report.set_defaults(func=cmd_report)

    listing = sub.add_parser("list", help="エントリを status で絞って並べる")
    listing.add_argument(
        "--status",
        action="append",
        default=[],
        choices=STATUSES,
        help="この status のエントリだけ (繰り返し可、既定: 全件)",
    )
    listing.add_argument("--skill", help="対象 skill で絞る")
    listing.add_argument(
        "--missing-after",
        dest="missing_after",
        action="store_true",
        help="merged なのに after 指標が 1 つも無いエントリだけ (Step 0 の取りこぼし回収)",
    )
    listing.add_argument(
        "--inconsistent",
        action="store_true",
        help="pr_open なのに pr が空の行だけ (Step 0 の修復対象)",
    )
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_list)

    verify_diff = sub.add_parser(
        "verify-diff",
        help="ブランチの台帳差分が許された変更だけかを検査する (workflow の verify job 用)",
    )
    verify_diff.add_argument("--base", help="base (default branch) 側の台帳ファイル")
    verify_diff.add_argument("--head", required=True, help="ブランチ側の台帳ファイル")
    verify_diff.add_argument(
        "--mode",
        required=True,
        choices=("candidate", "reconcile"),
        help="candidate: 改善ブランチ (1 行追加のみ) / reconcile: 突き合わせブランチ",
    )
    verify_diff.add_argument(
        "--ledger-id",
        dest="ledger_id",
        default="",
        help="candidate モードで追加行に期待する id",
    )
    verify_diff.set_defaults(func=cmd_verify_diff)

    check = sub.add_parser("check-target", help="改善対象にしてよい skill かを判定する")
    check.add_argument("skill")
    check.set_defaults(func=cmd_check_target)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI エントリポイント。ValueError は exit 1 のエラー出力に落とす。

    書き込み系サブコマンドは read-modify-write の全体を advisory lock で囲む
    (個々の cmd_* を触らずに済むよう、dispatch の外側で 1 か所だけ掴む)。
    """
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "locks", False):
            with ledger_lock(ledger_path(args)):
                return int(args.func(args))
        return int(args.func(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
