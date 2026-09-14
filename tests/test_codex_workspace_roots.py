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


class CodexWorkspaceRootsPatchTest(unittest.TestCase):
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

    def test_is_noop_when_entry_is_already_a_recursive_glob(self) -> None:
        already_fixed = config_with('"./**" = "write"')

        after = self.patch_ok(already_fixed)

        self.assertEqual(after, already_fixed)

    def test_is_noop_when_codex_config_is_absent(self) -> None:
        after = self.patch_ok(None)

        self.assertIsNone(after)


if __name__ == "__main__":
    unittest.main()
