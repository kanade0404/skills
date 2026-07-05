# ruff: noqa: T201, INP001
"""Check trigger eval predictions with a known-failures ledger."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
KNOWN_FAILURES_PATH = ROOT / ".github" / "trigger-evals-known-failures.json"
SCORER = ROOT / "skills" / "skill-builder" / "scripts" / "score_triggers.py"


@dataclass(frozen=True)
class KnownFailure:
    skill: str
    case_id: str
    reason: str
    recorded: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.skill, self.case_id)


@dataclass(frozen=True)
class Mismatch:
    skill: str
    case_id: str
    kind: str
    prompt: str
    expected: bool
    predicted: bool | None
    cases_path: Path
    preds_path: Path

    @property
    def key(self) -> tuple[str, str]:
        return (self.skill, self.case_id)


def repo_path(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def annotation_escape(value: object) -> str:
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotate(level: str, file: Path, title: str, message: str) -> None:
    print(
        f"::{level} file={repo_path(file)},title={annotation_escape(title)}::"
        f"{annotation_escape(message)}",
        flush=True,
    )


def changed_paths() -> set[Path]:
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return set()
    base_ref = os.environ["GITHUB_BASE_REF"]
    subprocess.run(["git", "fetch", "origin", base_ref, "--depth=1"], check=True)
    output = subprocess.check_output(
        ["git", "diff", "--name-only", f"origin/{base_ref}...HEAD"],
        text=True,
    )
    return {Path(line) for line in output.splitlines() if line}


def skill_from_path(path: Path) -> str | None:
    parts = path.parts
    if len(parts) >= 2 and parts[0] == "skills":
        return parts[1]
    return None


def is_trigger_input(path: Path) -> bool:
    return (
        len(path.parts) == 3
        and path.parts[0] == "skills"
        and path.parts[2] == "SKILL.md"
    ) or (
        len(path.parts) >= 4
        and path.parts[0] == "skills"
        and path.parts[2] == "evals"
        and path.name.endswith("-trigger.json")
    )


def is_trigger_result(path: Path) -> bool:
    return (
        len(path.parts) >= 4
        and path.parts[0] == "skills"
        and path.parts[2] == "evals"
        and "-trigger-results-" in path.name
        and path.suffix == ".jsonl"
    )


def is_skill_file(path: Path, skill: str) -> bool:
    return path == Path("skills") / skill / "SKILL.md"


def input_changed_for_eval(
    changed_trigger_inputs: set[Path],
    skill: str,
    rel_cases: Path,
    rel_eval_dir: Path,
) -> bool:
    return any(
        is_skill_file(path, skill) or path == rel_cases or path.parent == rel_eval_dir
        for path in changed_trigger_inputs
    )


def load_known_failures(path: Path) -> dict[tuple[str, str], KnownFailure]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{repo_path(path)} must contain a JSON array")

    known: dict[tuple[str, str], KnownFailure] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"{repo_path(path)} entry {index} must be an object")
        failure = KnownFailure(
            skill=str(item["skill"]),
            case_id=str(item["case_id"]),
            reason=str(item["reason"]),
            recorded=str(item["recorded"]),
        )
        if failure.key in known:
            raise ValueError(
                f"{repo_path(path)} duplicates {failure.skill}/{failure.case_id}"
            )
        known[failure.key] = failure
    return known


def load_cases(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(case["id"]): case for case in data["cases"]}


def load_preds(path: Path) -> dict[str, dict[str, Any]]:
    preds: dict[str, dict[str, Any]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        pred = json.loads(stripped)
        preds[str(pred["id"])] = pred
    return preds


def find_mismatches(cases_path: Path, preds_path: Path) -> list[Mismatch]:
    skill = cases_path.parent.parent.name
    cases = load_cases(cases_path)
    preds = load_preds(preds_path)

    mismatches: list[Mismatch] = []
    for case_id, case in cases.items():
        expected = bool(case["should_trigger"])
        pred = preds.get(case_id)
        predicted = None if pred is None else bool(pred["predicted"])
        if predicted is None:
            kind = "MISSING"
        elif expected == predicted:
            continue
        elif expected:
            kind = "FN"
        else:
            kind = "FP"

        mismatches.append(
            Mismatch(
                skill=skill,
                case_id=case_id,
                kind=kind,
                prompt=str(case["prompt"]),
                expected=expected,
                predicted=predicted,
                cases_path=cases_path,
                preds_path=preds_path,
            )
        )
    return mismatches


def emit_mismatch(mismatch: Mismatch, known: dict[tuple[str, str], KnownFailure]) -> bool:
    expected = "trigger" if mismatch.expected else "skip"
    predicted = "missing" if mismatch.predicted is None else (
        "trigger" if mismatch.predicted else "skip"
    )
    message = (
        f"{mismatch.skill}/{mismatch.case_id} {mismatch.kind}: "
        f"expected={expected}, predicted={predicted}; prompt={mismatch.prompt}"
    )
    known_failure = known.get(mismatch.key)
    if known_failure is not None:
        annotate(
            "warning",
            mismatch.cases_path,
            f"Known trigger mismatch: {mismatch.skill}/{mismatch.case_id}",
            f"{message}; known since {known_failure.recorded}: {known_failure.reason}",
        )
        return False

    annotate(
        "error",
        mismatch.cases_path,
        f"New trigger mismatch: {mismatch.skill}/{mismatch.case_id}",
        f"{message}; update predictions or add an intentional ledger entry",
    )
    return True


def emit_stale_known_failures(
    known: dict[tuple[str, str], KnownFailure],
    mismatched_keys: set[tuple[str, str]],
    evaluated_keys: dict[tuple[str, str], Path],
) -> None:
    for key, failure in sorted(known.items()):
        if key in mismatched_keys or key not in evaluated_keys:
            continue
        annotate(
            "warning",
            evaluated_keys[key],
            f"Stale known trigger mismatch: {failure.skill}/{failure.case_id}",
            (
                f"{failure.skill}/{failure.case_id} is listed in "
                f"{repo_path(KNOWN_FAILURES_PATH)} but now matches predictions; "
                "remove the stale ledger entry"
            ),
        )


def run_scorer(cases_path: Path, preds_path: Path) -> bool:
    result = subprocess.run(
        [
            sys.executable,
            str(SCORER),
            "--cases",
            str(cases_path),
            "--preds",
            str(preds_path),
        ],
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    known_failures = load_known_failures(KNOWN_FAILURES_PATH)
    changed = changed_paths()
    changed_trigger_inputs = {path for path in changed if is_trigger_input(path)}
    changed_trigger_results = {path for path in changed if is_trigger_result(path)}
    changed_result_dirs = {path.parent for path in changed_trigger_results}
    changed_trigger_skills = {
        skill
        for path in changed_trigger_inputs | changed_trigger_results
        if (skill := skill_from_path(path))
    }

    all_case_files = sorted((ROOT / "skills").glob("*/evals/*-trigger.json"))
    if os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        case_files = [
            path for path in all_case_files if path.parent.parent.name in changed_trigger_skills
        ]
    else:
        case_files = all_case_files

    failed = False
    skipped = 0
    scored = 0
    mismatched_keys: set[tuple[str, str]] = set()
    evaluated_keys: dict[tuple[str, str], Path] = {}

    if not case_files:
        print("No changed trigger eval inputs or result files found.")
        return 0

    for cases_path in case_files:
        skill = cases_path.parent.parent.name
        eval_dir = cases_path.parent
        rel_cases = cases_path.relative_to(ROOT)
        rel_eval_dir = eval_dir.relative_to(ROOT)
        result_files = sorted(eval_dir.glob("*-trigger-results-*.jsonl"))

        if not result_files:
            message = f"{skill}: no *-trigger-results-*.jsonl found next to {rel_cases}"
            if rel_eval_dir in changed_result_dirs or input_changed_for_eval(
                changed_trigger_inputs,
                skill,
                rel_cases,
                rel_eval_dir,
            ):
                failed = True
                annotate("error", cases_path, "Missing trigger predictions", message)
            else:
                skipped += 1
                print(f"::notice title=Skipped trigger eval::{message}", flush=True)
            continue

        if input_changed_for_eval(changed_trigger_inputs, skill, rel_cases, rel_eval_dir):
            if rel_eval_dir not in changed_result_dirs:
                failed = True
                annotate(
                    "error",
                    cases_path,
                    "Stale trigger predictions",
                    (
                        f"{skill}: update a sibling *-trigger-results-*.jsonl file "
                        "when trigger inputs change"
                    ),
                )
                continue

        preds_path = result_files[-1]
        rel_preds = preds_path.relative_to(ROOT)
        print(f"::group::{skill}: {rel_cases} vs {rel_preds}", flush=True)
        scored += 1
        if not run_scorer(cases_path, preds_path):
            failed = True
            annotate(
                "error",
                cases_path,
                f"{skill} trigger scoring failed",
                f"{rel_preds} could not be scored",
            )
            print("::endgroup::", flush=True)
            continue

        for case_id in load_cases(cases_path):
            evaluated_keys[(skill, case_id)] = cases_path

        mismatches = find_mismatches(cases_path, preds_path)
        mismatched_keys.update(mismatch.key for mismatch in mismatches)
        for mismatch in mismatches:
            failed = emit_mismatch(mismatch, known_failures) or failed

        if not mismatches:
            print(f"No trigger prediction mismatches for {skill}.", flush=True)
        print("::endgroup::", flush=True)

    emit_stale_known_failures(known_failures, mismatched_keys, evaluated_keys)
    print(
        f"Scored {scored} trigger eval file(s); skipped {skipped} without result JSONL.",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"::error title=Trigger eval check failed::{annotation_escape(exc)}")
        raise
