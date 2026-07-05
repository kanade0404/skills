# Provenance

This skill is a **vendored copy** of a third-party skill, brought in under the
copy-in exception in the repo `CLAUDE.md` ("サードパーティ skill は vendor しない。
copy-in 例外は skill ディレクトリ内に出典 + LICENSE を残す"). It was copied — not
fetched at runtime via `npx skill` / `gh skill` — deliberately, to avoid executing
upstream tooling (supply-chain hardening).

## Source

- **Upstream repo**: https://github.com/mattpocock/skills
- **Upstream path**: `skills/engineering/setup-matt-pocock-skills`
- **Author**: Matt Pocock
- **License**: MIT (see [LICENSE](./LICENSE))
- **Commit pinned**: `272f99b22574f50e4266791c86b9302682970e23` (`main`)
- **Retrieved**: 2026-07-05

The following files are byte-for-byte copies of the upstream at that commit:

- `SKILL.md`
- `domain.md`
- `issue-tracker-github.md`
- `issue-tracker-gitlab.md`
- `issue-tracker-local.md`
- `triage-labels.md`

`LICENSE` and this `PROVENANCE.md` are added by the vendoring; everything else is
untouched. To audit, diff against the pinned upstream URL:

```
https://raw.githubusercontent.com/mattpocock/skills/272f99b22574f50e4266791c86b9302682970e23/skills/engineering/setup-matt-pocock-skills/<file>
```

## Caveat: sibling-skill dependencies

`SKILL.md` references other Matt Pocock engineering skills that are **not** vendored
here — e.g. `to-issues`, `triage`, `to-prd`, `qa`, `wayfinder`, `domain-modeling`,
`grill-with-docs`, `improve-codebase-architecture`, `diagnosing-bugs`, `tdd`. This
setup skill only scaffolds the per-repo config (`docs/agents/*.md`, the `## Agent
skills` block) that those skills read; it is a no-op in isolation. Vendor the sibling
skills the same way if you intend to use the rest of the suite.

## Updating

Re-run the copy against a newer upstream commit, update the pinned commit / retrieval
date above, and re-verify the byte-for-byte claim.
