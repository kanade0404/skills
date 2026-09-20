"""Tests for scripts/retired-generated-paths.mjs, the stale-path detector that
`node scripts/rulesync-sync.mjs` uses for outputs an abolished rulesync feature
used to produce.

These paths are invisible to the script's two other detectors: `diffTree` only
walks paths that exist under the freshly generated scratch root (a retired path
is absent there by definition), and `findStaleFiles` only walks `MIRRORED_DIRS`
(`.claude/skills` / `.agents/skills`). Anything retired and not listed here
therefore survives forever in an existing checkout — `--check` reports "up to
date" and write mode never removes it, because materialize is a non-deleting
`cpSync` overlay.

Driven as a subprocess through a tiny inline ESM harness rather than by
importing scripts/rulesync-sync.mjs, which runs its whole staging +
`npx rulesync generate` pipeline as an import side effect.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE = REPO_ROOT / "scripts" / "retired-generated-paths.mjs"


class ModuleHarness:
    """Runs the module's exports over scratch generated/target trees."""

    def _run(self, script: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"node exited {result.returncode}\n--- stderr ---\n{result.stderr}",
        )
        return result

    @staticmethod
    def _seed(root: pathlib.Path, layout: dict[str, str | None]) -> None:
        """Create `layout` under `root`.

        A str value writes a file with that content; None creates a directory.
        """
        for rel, content in layout.items():
            path = root / rel
            if content is None:
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

    def find(
        self,
        generated: dict[str, str | None],
        target: dict[str, str | None],
        paths: list[str] | None = None,
    ) -> list[str]:
        """Return what the detector reports for the given pair of trees."""
        with tempfile.TemporaryDirectory() as tmp:
            gen_root = pathlib.Path(tmp) / "generated"
            target_root = pathlib.Path(tmp) / "repo"
            gen_root.mkdir()
            target_root.mkdir()
            self._seed(gen_root, generated)
            self._seed(target_root, target)
            paths_arg = "" if paths is None else f", {json.dumps(paths)}"
            script = (
                "import { findRetiredGeneratedPaths } from "
                f"{json.dumps(MODULE.as_uri())};\n"
                "console.log(JSON.stringify(findRetiredGeneratedPaths("
                f"{json.dumps(str(gen_root))}, {json.dumps(str(target_root))}"
                f"{paths_arg})));\n"
            )
            return json.loads(self._run(script).stdout)

    def remove(
        self,
        target: dict[str, str | None],
        stale: list[str],
    ) -> tuple[list[str], list[str]]:
        """Remove `stale` from a seeded tree; return (survivors, log lines)."""
        with tempfile.TemporaryDirectory() as tmp:
            target_root = pathlib.Path(tmp) / "repo"
            target_root.mkdir()
            self._seed(target_root, target)
            script = (
                "import { removeRetiredGeneratedPaths } from "
                f"{json.dumps(MODULE.as_uri())};\n"
                "const log = [];\n"
                "removeRetiredGeneratedPaths("
                f"{json.dumps(str(target_root))}, {json.dumps(stale)}, "
                "(line) => log.push(line));\n"
                "console.log(JSON.stringify(log));\n"
            )
            log = json.loads(self._run(script).stdout)
            survivors = sorted(
                p.relative_to(target_root).as_posix()
                for p in target_root.rglob("*")
            )
        return survivors, log


class RetiredPathListTest(unittest.TestCase):
    """The list itself is the contract — assert its membership directly."""

    def _list(self) -> list[str]:
        script = (
            "import { RETIRED_GENERATED_PATHS } from "
            f"{json.dumps(MODULE.as_uri())};\n"
            "console.log(JSON.stringify(RETIRED_GENERATED_PATHS));\n"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_lists_claude_rules_generated_dir(self) -> None:
        # ADR 0018 条件 2: `.claude/rules` は root rule 由来の生成物。rules feature
        # 廃止後は生成されないので、既存 checkout に残っていたら再生成で消えなければ
        # ならない。MIRRORED_DIRS にも入っていないため、この一覧だけが受け皿。
        self.assertIn(".claude/rules", self._list())

    def test_lists_root_rule_aggregated_files(self) -> None:
        listed = self._list()

        self.assertIn("CLAUDE.md", listed)
        self.assertIn("AGENTS.md", listed)

    def test_does_not_list_the_codex_permissions_output(self) -> None:
        # 名前に rules を含むが `permissions` feature の生成物で、いまも生成される
        # (ADR 0018 条件 4)。一覧に入れると生きた出力を消しにいく。
        self.assertNotIn(".codex/rules/rulesync.rules", self._list())
        self.assertNotIn(".codex/rules", self._list())


class FindRetiredGeneratedPathsTest(ModuleHarness, unittest.TestCase):
    def test_reports_claude_rules_dir_left_over_in_the_repo(self) -> None:
        reported = self.find(
            generated={".claude/skills/demo/SKILL.md": "x"},
            target={
                ".claude/skills/demo/SKILL.md": "x",
                ".claude/rules/orchestration-policy.md": "old rule",
            },
        )

        self.assertEqual(reported, [".claude/rules"])

    def test_reports_root_file_left_over_in_the_repo(self) -> None:
        reported = self.find(generated={}, target={"CLAUDE.md": "stale"})

        self.assertEqual(reported, ["CLAUDE.md"])

    def test_silent_when_the_repo_has_no_retired_path(self) -> None:
        reported = self.find(
            generated={".claude/skills/demo/SKILL.md": "x"},
            target={".claude/skills/demo/SKILL.md": "x"},
        )

        self.assertEqual(reported, [])

    def test_never_reports_a_path_generation_still_produces(self) -> None:
        # The structural guard described in the module header: a still-generated
        # path listed by mistake must not be swept.
        reported = self.find(
            generated={".codex/rules/rulesync.rules": "live"},
            target={".codex/rules/rulesync.rules": "live"},
            paths=[".codex/rules/rulesync.rules"],
        )

        self.assertEqual(reported, [])


class RemoveRetiredGeneratedPathsTest(ModuleHarness, unittest.TestCase):
    def test_removes_a_retired_directory_recursively(self) -> None:
        survivors, log = self.remove(
            target={
                ".claude/rules/orchestration-policy.md": "old rule",
                ".claude/rules/nested/other.md": "old rule",
                ".claude/skills/demo/SKILL.md": "keep",
            },
            stale=[".claude/rules"],
        )

        self.assertNotIn(".claude/rules", survivors)
        self.assertIn(".claude/skills/demo/SKILL.md", survivors)
        self.assertEqual(len(log), 1)
        self.assertIn(".claude/rules", log[0])
        self.assertIn("ADR 0018", log[0])

    def test_removes_a_retired_root_file(self) -> None:
        survivors, _ = self.remove(
            target={"CLAUDE.md": "stale", "README.md": "keep"},
            stale=["CLAUDE.md"],
        )

        self.assertNotIn("CLAUDE.md", survivors)
        self.assertIn("README.md", survivors)

    def test_removes_nothing_and_logs_nothing_when_given_no_stale_paths(self) -> None:
        survivors, log = self.remove(target={"CLAUDE.md": "kept"}, stale=[])

        self.assertEqual(survivors, ["CLAUDE.md"])
        self.assertEqual(log, [])


if __name__ == "__main__":
    unittest.main()
