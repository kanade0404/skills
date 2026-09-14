"""Tests for scripts/codex-workspace-roots.mjs, the post-generation patch that
rewrites the bare `"*"` catch-all rulesync emits into `.codex/config.toml`'s
`[permissions.rulesync.filesystem.":workspace_roots"]` table so codex-cli
accepts the generated config.

The patch is invisible to `node scripts/rulesync-sync.mjs --check`: --check
compares the freshly generated (and freshly patched) tree against the committed
one, so a patch that silently stops matching produces a broken config on both
sides and still reports "up to date". These tests are the only thing standing
between a rulesync serialization change and a `.codex/config.toml` that codex
refuses to load.

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
MODULE = REPO_ROOT / "scripts" / "codex-workspace-roots.mjs"

WORKSPACE_ROOTS_TABLE = '[permissions.rulesync.filesystem.":workspace_roots"]'


def config_with(*entries: str, network_domains: str | None = None) -> str:
    """Build a `.codex/config.toml` shaped like the one rulesync generates."""
    lines = [
        'default_permissions = "rulesync"',
        "",
        "[permissions.rulesync.filesystem]",
        '":minimal" = "read"',
        "",
        WORKSPACE_ROOTS_TABLE,
        *entries,
        "",
        "[permissions.rulesync.network]",
        "enabled = true",
        "",
        "[permissions.rulesync.network.domains]",
        network_domains if network_domains is not None else '"*" = "allow"',
        "",
    ]
    return "\n".join(lines)


class PatchHarness:
    """Runs the patch over a scratch tree. Mixed into each TestCase below."""

    def run_patch(self, config: str | None) -> tuple[subprocess.CompletedProcess, str | None]:
        """Run the patch over a scratch output root.

        `config=None` means "no .codex/config.toml at all". Returns the
        completed process and the config content after the run (None if the
        file is still absent).
        """
        with tempfile.TemporaryDirectory() as tmp:
            out_root = pathlib.Path(tmp)
            config_path = out_root / ".codex" / "config.toml"
            if config is not None:
                config_path.parent.mkdir(parents=True)
                config_path.write_text(config, encoding="utf-8")
            script = (
                "import { fixCodexWorkspaceRootsCatchAll } from "
                f"{json.dumps(MODULE.as_uri())};\n"
                f"fixCodexWorkspaceRootsCatchAll({json.dumps(str(out_root))});\n"
            )
            result = subprocess.run(
                ["node", "--input-type=module", "-e", script],
                capture_output=True,
                text=True,
            )
            after = (
                config_path.read_text(encoding="utf-8")
                if config_path.exists()
                else None
            )
        return result, after

    def patch_ok(self, config: str | None) -> str | None:
        result, after = self.run_patch(config)
        self.assertEqual(
            result.returncode,
            0,
            f"patch exited {result.returncode}\n--- stderr ---\n{result.stderr}",
        )
        return after


class CodexWorkspaceRootsPatchTest(PatchHarness, unittest.TestCase):
    def test_rewrites_bare_catch_all_write_to_recursive_glob(self) -> None:
        after = self.patch_ok(config_with('"*" = "write"'))

        self.assertIn('"./**" = "write"', after)
        self.assertNotIn('"*" = "write"', after)

    def test_rewrites_bare_catch_all_read_to_recursive_glob(self) -> None:
        after = self.patch_ok(config_with('"*" = "read"'))

        self.assertIn('"./**" = "read"', after)
        self.assertNotIn('"*" = "read"', after)

    def test_leaves_bare_catch_all_deny_untouched(self) -> None:
        # codex-cli accepts a bare `*` glob for `deny` — rewriting it to
        # `./**` would silently widen a deny rule into a subtree-only one.
        after = self.patch_ok(config_with('"*" = "deny"'))

        self.assertIn('"*" = "deny"', after)
        self.assertNotIn('"./**"', after)

    def test_leaves_network_domains_catch_all_untouched(self) -> None:
        # `[permissions.rulesync.network.domains]`'s `"*" = "allow"` is a
        # domain matcher, not a filesystem glob; `./**` would be nonsense there.
        after = self.patch_ok(config_with('"*" = "write"'))

        self.assertIn('[permissions.rulesync.network.domains]\n"*" = "allow"', after)

    def test_leaves_entries_above_the_first_table_header_untouched(self) -> None:
        # The `:workspace_roots` scoping is what protects every other `"*"` key
        # in the file, and it has to hold before the first table header too --
        # not just after a later header has switched it back off.
        preamble = "\n".join(
            [
                '"*" = "allow"',
                "",
                WORKSPACE_ROOTS_TABLE,
                '"*" = "write"',
                "",
            ]
        )

        after = self.patch_ok(preamble)

        self.assertEqual(after, preamble.replace('"*" = "write"', '"./**" = "write"'))

    def test_is_noop_when_entry_is_already_a_recursive_glob(self) -> None:
        already_fixed = config_with('"./**" = "write"')

        after = self.patch_ok(already_fixed)

        self.assertEqual(after, already_fixed)

    def test_rewrites_every_workspace_roots_catch_all_not_just_the_first(self) -> None:
        # Nothing guarantees rulesync emits exactly one permission profile, and
        # a second `:workspace_roots` table left unpatched is a config codex
        # still refuses to load — with no symptom until someone runs codex.
        two_profiles = "\n".join(
            [
                'default_permissions = "rulesync"',
                "",
                WORKSPACE_ROOTS_TABLE,
                '"*" = "write"',
                "",
                '[permissions.readonly.filesystem.":workspace_roots"]',
                '"*" = "read"',
                "",
            ]
        )

        after = self.patch_ok(two_profiles)

        self.assertIn('"./**" = "write"', after)
        self.assertIn('"./**" = "read"', after)
        self.assertNotIn('"*" = ', after)

    def test_is_noop_when_codex_config_is_absent(self) -> None:
        after = self.patch_ok(None)

        self.assertIsNone(after)

    def test_rewrites_catch_all_across_toml_serialization_variants(self) -> None:
        # The exact byte shape rulesync emits today (`"*" = "write"`) is not a
        # contract. Spacing, quote style and a trailing comment must all still
        # be rewritten rather than trip the fail-loud gate, and the rest of the
        # line must survive untouched (this output is diffed byte-for-byte).
        variants = {
            '"*"  =  "write"': '"./**"  =  "write"',
            "'*' = 'write'": "\"./**\" = 'write'",
            '"*" = "write" # catch-all': '"./**" = "write" # catch-all',
            '  "*" = "read"': '  "./**" = "read"',
        }

        for entry, expected in variants.items():
            with self.subTest(entry=entry):
                after = self.patch_ok(config_with(entry))

                self.assertIn(expected, after)

    def test_leaves_the_committed_codex_config_untouched(self) -> None:
        # The strongest false-fire guard available: the real generated config
        # this repo ships. Anything the patch or its fail-loud gates do must be
        # a no-op here, or `node scripts/rulesync-sync.mjs` stops producing the
        # tree that is committed and every run of it reports drift.
        committed = (REPO_ROOT / ".codex" / "config.toml").read_text(encoding="utf-8")

        after = self.patch_ok(committed)

        self.assertEqual(after, committed)

    def test_exits_nonzero_when_a_catch_all_survives_the_patch(self) -> None:
        # A value shape the rewrite does not recognize (here: an inline table
        # instead of a plain string) must not be waved through. Staying silent
        # ships a `.codex/config.toml` codex refuses to load, and --check
        # compares that broken output against an equally broken committed one
        # and reports "up to date".
        unrecognized = config_with('"*" = { access = "write" }')

        result, _ = self.run_patch(unrecognized)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("workspace_roots", result.stderr)
        self.assertIn('"*" = { access = "write" }', result.stderr)


if __name__ == "__main__":
    unittest.main()
