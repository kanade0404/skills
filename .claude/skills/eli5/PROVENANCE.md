# Provenance

This skill is a **vendored copy** of a third-party skill, brought in under the
copy-in exception in the repo `CLAUDE.md` / `README.md` ("サードパーティ skill は
vendor しない。明示的に copy-in した例外は、該当 skill ディレクトリ内に出典と
ライセンスを残す"). It was copied — not fetched at runtime — deliberately, to
avoid executing upstream tooling (supply-chain hardening).

## Source

- **Upstream repo**: https://github.com/anthropics/claude-plugins-community
- **Upstream path**: `eli5/skills/eli5`
- **Author**: Thariq Shihipar
- **License**: MIT (see [LICENSE](./LICENSE); declared in the upstream plugin's
  `.claude-plugin/plugin.json`, which is not itself vendored here). The upstream
  repo ships no standalone LICENSE file for this plugin (only the `"license":
  "MIT"` SPDX identifier), so there is no upstream copyright year to match —
  `LICENSE` here uses the retrieval year below and the author name from
  `plugin.json`.
- **Commit pinned**: `f4c9452f5ca091f1be7064d9faab1b001ea21645` (`main`)
- **Retrieved**: 2026-08-22

In the **source-of-truth** directory `skills/eli5/`, the following file is a
byte-for-byte copy of the upstream at that commit:

- `SKILL.md`

`LICENSE` and this `PROVENANCE.md` are added by the vendoring; everything else
is untouched. To audit, diff `SKILL.md` against the pinned upstream URL:

```text
https://raw.githubusercontent.com/anthropics/claude-plugins-community/f4c9452f5ca091f1be7064d9faab1b001ea21645/eli5/skills/eli5/SKILL.md
```

### Generated mirrors are not byte-for-byte

The byte-for-byte claim above is about `skills/eli5/` only. This repo also
commits **rulesync-generated** mirrors under `.claude/skills/` and
`.agents/skills/` (see `scripts/rulesync-sync.mjs`; regenerate, don't
hand-edit). Those are derived artifacts and rulesync transforms frontmatter
per target. Verify the mirrors with `node scripts/rulesync-sync.mjs --check`,
not by diffing against upstream.

## Behavior note

`SKILL.md`'s body ends with `Topic: $ARGUMENTS` — a slash-command style
template variable from the upstream plugin's original packaging (it ships as
a plugin whose `eli5` skill doubles as the `/eli5 <topic>` command target).
Kept verbatim for fidelity; `$ARGUMENTS` is substituted by the invoking
harness where supported, and otherwise reads as the literal topic prompt text
typed after `/eli5`.

## Updating

Re-run the copy against a newer upstream commit and update the pinned commit /
retrieval date above.
