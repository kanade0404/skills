#!/usr/bin/env bash
# resolve-merge.sh - merge a base branch into the currently checked-out
# branch and report conflicts via a distinct exit code, instead of a prose
# "if conflict, continue" convention that's easy to get wrong.
#
# Usage: resolve-merge.sh <base-branch>
#
# Precondition: the PR head branch is already checked out and up to date
# (see pr-context.sh for PR_HEAD/PR_BASE, then `git checkout "$PR_HEAD"`
# separately). This script does not check out anything itself.
#
# Exit codes:
#   0  - merge completed with NO conflicts. A merge commit already exists;
#        skip straight to finalize.sh (there is nothing to `git add`).
#   10 - merge produced conflicts. Conflicted file paths are printed to
#        stdout, one per line. Resolve each one (regen-lockfiles.sh for
#        lockfiles, manual edit for source), THEN call finalize.sh.
#   *  - any other exit code is an unexpected merge failure (auth error,
#        unknown ref, corrupt repo, uncommitted local changes blocking the
#        merge, ...). Do NOT treat this as "conflicts to resolve" - stop and
#        report the actual error.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <base-branch>" >&2
  exit 2
fi

base="$1"

if ! git fetch origin "$base"; then
  echo "error: 'git fetch origin $base' failed" >&2
  exit 1
fi

merge_status=0
# `git merge` writes its own progress ("Auto-merging ...", "CONFLICT ...")
# to stdout, which would otherwise corrupt this script's stdout contract
# (conflicted-file-list only). Redirect it to stderr instead.
git merge --no-ff --no-edit "origin/$base" >&2 || merge_status=$?

if [ "$merge_status" -eq 0 ]; then
  echo "merge completed with no conflicts" >&2
  exit 0
fi

conflicts=$(git diff --name-only --diff-filter=U || true)

if [ -z "$conflicts" ]; then
  echo "error: git merge failed (exit $merge_status) for a reason other than conflicts; not proceeding to conflict resolution" >&2
  exit "$merge_status"
fi

printf '%s\n' "$conflicts"
exit 10
