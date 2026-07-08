#!/usr/bin/env bash
# Resolve a PR review thread. The thread is resolved directly via the
# GraphQL `resolveReviewThread` mutation — resolution no longer depends on
# CodeRabbit noticing an `@coderabbitai resolve` mention. For CodeRabbit
# threads the mention is still posted alongside (belt-and-suspenders, since
# some CodeRabbit UI affordances key off it), but the thread's `isResolved`
# state is set by this script's mutation call, not by waiting on the bot.
#
# Usage:
#   resolve_thread.sh <pr-number> <root-comment-id> <classification> [body-file] [vendor]
#
# classification must be one of: VALID VALID_DEFER DUPLICATE.
# vendor must be one of: coderabbit devin human (default: coderabbit, kept
# for backward compatibility with existing 3/4-arg callers).
#
# Guard: INVALID_PUSH is REJECTED (non-zero exit, no API call made). Resolving
# an INVALID_PUSH thread would tell the reviewer "fixed" when we actually
# pushed back — this guard makes that misuse fail loudly instead of relying
# on the caller to remember the rule (skills/pr-review-respond/SKILL.md
# Phase D).
#
# Reply body construction (posted before the resolve mutation, if at all):
#   - vendor=coderabbit: body-file content (if given) followed by a blank
#     line and the `@coderabbitai resolve` directive. If body-file is
#     omitted, the reply is just the directive line.
#   - vendor=devin|human: body-file content only — no directive posted (a
#     bot mention in a human/Devin thread would be confusing). If body-file
#     is omitted, no reply is posted at all; the thread is resolved silently.
#
# After the reply (if any), the thread is looked up by its root comment's
# databaseId — paginating `reviewThreads` the same way
# skills/pr-monitor/scripts/prm's fetch_unresolved_threads does — and
# resolved via `resolveReviewThread(input: {threadId: $id})`. The mutation
# response's `isResolved` is verified to be true; anything else is a hard
# failure (non-zero exit).
#
# stdout:
#   - reply posted:  line 1 = reply html_url, last line = "resolved <thread_id>"
#   - reply skipped: "resolved <thread_id>" only
#
# Known limitation: `resolveReviewThread` requires write access to the repo
# (it is a mutation, not a read). This assumes the caller is running against
# a PR / repo where the authenticated `gh` user has write permission.

set -euo pipefail

# 直接実行にも耐えるよう、dispatcher (prr) 頼みにせず自前でも色強制を無効化する
export NO_COLOR=1
export CLICOLOR_FORCE=0
unset GH_FORCE_TTY
export GH_PAGER=cat

if [ "$#" -lt 3 ] || [ "$#" -gt 5 ]; then
  echo "usage: $0 <pr-number> <root-comment-id> <classification> [body-file] [vendor]" >&2
  exit 2
fi

pr="$1"
comment_id="$2"
classification="$3"
body_file="${4:-}"
vendor="${5:-coderabbit}"

case "$classification" in
  VALID|VALID_DEFER|DUPLICATE)
    ;;
  INVALID_PUSH)
    echo "error: refusing to resolve thread $comment_id on PR $pr: classification is INVALID_PUSH." >&2
    echo "       INVALID_PUSH threads must stay open (reply only, never resolve)." >&2
    exit 1
    ;;
  *)
    echo "error: unknown classification: $classification (expected VALID|VALID_DEFER|DUPLICATE|INVALID_PUSH)" >&2
    exit 2
    ;;
esac

case "$vendor" in
  coderabbit|devin|human)
    ;;
  *)
    echo "usage: $0 <pr-number> <root-comment-id> <classification> [body-file] [vendor]" >&2
    echo "error: unknown vendor: $vendor (expected coderabbit|devin|human)" >&2
    exit 2
    ;;
esac

owner=$(gh repo view --json owner --jq '.owner.login')
repo=$(gh repo view --json name --jq '.name')

skip_reply=false
case "$vendor" in
  coderabbit)
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
    ;;
  devin|human)
    if [ -n "$body_file" ]; then
      if [ ! -f "$body_file" ]; then
        echo "error: body file not found: $body_file" >&2
        exit 2
      fi
      body=$(cat "$body_file")
    else
      skip_reply=true
    fi
    ;;
esac

if [ "$skip_reply" = false ]; then
  resp=$(gh api -X POST \
    -H "Accept: application/vnd.github+json" \
    "repos/$owner/$repo/pulls/$pr/comments/$comment_id/replies" \
    -f body="$body")
  jq -r '.html_url' <<<"$resp"
fi

# Look up the GraphQL thread id for this root comment's databaseId by
# paginating reviewThreads (same cursor-loop shape as
# skills/pr-monitor/scripts/prm's fetch_unresolved_threads).
find_thread_id() {
  local cursor="" has_next="true" page found=""
  while [ "$has_next" = "true" ]; do
    args=(-F owner="$owner" -F repo="$repo" -F pr="$pr")
    if [ -n "$cursor" ]; then
      args+=(-F cursor="$cursor")
    fi
    page=$(gh api graphql "${args[@]}" -f query='
      query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
        repository(owner: $owner, name: $repo) {
          pullRequest(number: $pr) {
            reviewThreads(first: 100, after: $cursor) {
              pageInfo { hasNextPage endCursor }
              nodes {
                id
                comments(first: 1) {
                  nodes { databaseId }
                }
              }
            }
          }
        }
      }' --jq '.data.repository.pullRequest.reviewThreads')
    found=$(jq -r --arg cid "$comment_id" \
      '.nodes[] | select((.comments.nodes[0].databaseId | tostring) == $cid) | .id' \
      <<<"$page")
    if [ -n "$found" ]; then
      printf '%s\n' "$found"
      return 0
    fi
    has_next=$(jq -r '.pageInfo.hasNextPage' <<<"$page")
    cursor=$(jq -r '.pageInfo.endCursor // empty' <<<"$page")
  done
  return 1
}

thread_id=$(find_thread_id) || {
  echo "error: could not find review thread for root comment id $comment_id on PR $pr" >&2
  exit 1
}

mutation_resp=$(gh api graphql \
  -F id="$thread_id" \
  -f query='
    mutation($id: ID!) {
      resolveReviewThread(input: {threadId: $id}) {
        thread { id isResolved }
      }
    }')

is_resolved=$(jq -r '.data.resolveReviewThread.thread.isResolved' <<<"$mutation_resp")
if [ "$is_resolved" != "true" ]; then
  echo "error: resolveReviewThread mutation did not return isResolved=true for thread $thread_id" >&2
  exit 1
fi

echo "resolved $thread_id"
