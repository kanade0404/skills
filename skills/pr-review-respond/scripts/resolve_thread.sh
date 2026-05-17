#!/usr/bin/env bash
# Mark a CodeRabbit review thread as resolved by posting a reply with the
# `@coderabbitai resolve` directive. The CodeRabbit bot listens for that
# directive and flips the thread state.
#
# Usage:
#   resolve_thread.sh <pr-number> <root-comment-id> [body-file]
#
# If body-file is omitted, the reply is just `@coderabbitai resolve`. When
# given, body-file content is concatenated BEFORE the directive line, so the
# caller can include "Fixed in <SHA>" / "Tracked in #N" etc.
#
# Important: this script is intended for VALID / VALID_DEFER / DUPLICATE
# classifications only. Do NOT call it for INVALID_PUSH (we leave reviewer
# the right to re-open).

set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: $0 <pr-number> <root-comment-id> [body-file]" >&2
  exit 2
fi

pr="$1"
comment_id="$2"
body_file="${3:-}"

owner=$(gh repo view --json owner --jq '.owner.login')
repo=$(gh repo view --json name --jq '.name')

prefix=""
if [ -n "$body_file" ]; then
  if [ ! -f "$body_file" ]; then
    echo "error: body file not found: $body_file" >&2
    exit 2
  fi
  prefix=$(cat "$body_file")
  prefix="${prefix}"$'\n\n'
fi

body="${prefix}@coderabbitai resolve"

resp=$(gh api -X POST \
  -H "Accept: application/vnd.github+json" \
  "repos/$owner/$repo/pulls/$pr/comments/$comment_id/replies" \
  -f body="$body")

jq -r '.html_url' <<<"$resp"
