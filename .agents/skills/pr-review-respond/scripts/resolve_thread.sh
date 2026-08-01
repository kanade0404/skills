#!/usr/bin/env bash
# Resolve a PR review thread. The thread is resolved directly via the
# GraphQL `resolveReviewThread` mutation. The `@coderabbitai resolve`
# mention is NEVER posted (for any vendor): CodeRabbit treats it as a
# resolve-ALL directive for the entire PR, which swept an INVALID_PUSH
# thread on PR #96. The mutation resolves exactly the one target thread,
# so the mention is redundant as well as dangerous.
#
# Usage:
#   resolve_thread.sh <pr-number> <root-comment-id> <classification> <vendor> [body-file]
#
# classification must be one of: VALID VALID_DEFER DUPLICATE.
# vendor is REQUIRED (4th positional arg) and must be one of: coderabbit
# devin human. There is no implicit default — an omitted or invalid vendor
# is a hard failure (usage + exit 2). Vendor no longer changes the reply
# body, but the explicit argument keeps misclassification loud and leaves
# room for future vendor-specific wording (SKILL.md Phase D).
#
# Guard: INVALID_PUSH is REJECTED (non-zero exit, no API call made). Resolving
# an INVALID_PUSH thread would tell the reviewer "fixed" when we actually
# pushed back — this guard makes that misuse fail loudly instead of relying
# on the caller to remember the rule (skills/pr-review-respond/SKILL.md
# Phase D).
#
# Reply body construction (all vendors): body-file content only — no
# directive is ever posted. If body-file is omitted (exactly 4 arguments —
# an explicit empty-string 5th argument is rejected with exit 2, not
# treated as omission), no reply is posted; the thread is resolved
# silently via the mutation. If body-file IS given
# but its content is empty / whitespace-only, that is a caller bug and is
# rejected up front with exit 2, before any API call — posting it would 422
# after the resolve mutation had already succeeded (run left half-finished),
# and silently skipping the reply would drop an explanation the caller
# clearly intended to post. A body containing the literal
# `@coderabbitai resolve` directive is rejected before any API call
# (resolve-ALL hazard — see above).
#
# Ordering (deliberate): the thread is looked up by its root comment's
# databaseId — paginating `reviewThreads` the same way
# skills/pr-monitor/scripts/prm's fetch_unresolved_threads does — and
# resolved via `resolveReviewThread(input: {threadId: $id})` BEFORE the reply
# is posted. The mutation response's `isResolved` is verified to be true;
# anything else is a hard failure (non-zero exit) and no reply is posted at
# all. This ordering matters: if the lookup or mutation fails (transient
# GraphQL error, insufficient resolve permission, thread already gone) after
# a "Fixed in ..." reply had already been posted, the thread would be left
# open with a misleading success reply that a later `pr-review-respond`
# fetch could mistake for an already-handled (self-replied) thread and skip
# forever. Resolving first means a failure here never posts that reply; the
# worst case is a thread that resolved correctly but without an explanatory
# reply, which is a strictly safer failure mode.
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

if [ "$#" -lt 4 ] || [ "$#" -gt 5 ]; then
  echo "usage: $0 <pr-number> <root-comment-id> <classification> <vendor> [body-file]" >&2
  exit 2
fi

# Silent resolve is only the true omission case ($# = 4). An explicitly
# passed empty-string 5th argument is a caller bug, not an omission.
if [ "$#" -eq 5 ] && [ -z "$5" ]; then
  echo "error: body-file argument is an empty string (omit the 5th argument entirely to resolve silently)" >&2
  exit 2
fi

pr="$1"
comment_id="$2"
classification="$3"
vendor="$4"
body_file="${5:-}"

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
    echo "usage: $0 <pr-number> <root-comment-id> <classification> <vendor> [body-file]" >&2
    echo "error: unknown or missing vendor: '$vendor' (expected coderabbit|devin|human, no default)" >&2
    exit 2
    ;;
esac

body_content=""
if [ -n "$body_file" ]; then
  if [ ! -f "$body_file" ]; then
    echo "error: body file not found: $body_file" >&2
    exit 2
  fi
  # Content guard (before any API call): never let the CodeRabbit resolve-ALL
  # directive reach GitHub inside a reply body.
  if grep -qF '@coderabbitai resolve' "$body_file"; then
    echo "error: body contains '@coderabbitai resolve' — CodeRabbit treats it as resolve-ALL for the entire PR (swept an INVALID_PUSH thread on PR #96). Remove it; resolution is done via GraphQL resolveReviewThread (prr resolve)." >&2
    exit 1
  fi
  body_content=$(cat "$body_file")
  # An explicitly given body file must have real content: empty / whitespace-
  # only means the caller meant to post a reply but built the body wrong.
  # Fail loudly instead of silently resolving without the reply.
  if [ -z "${body_content//[[:space:]]/}" ]; then
    echo "error: body file is empty: $body_file (omit the argument to resolve silently)" >&2
    exit 2
  fi
fi

owner=$(gh repo view --json owner --jq '.owner.login')
repo=$(gh repo view --json name --jq '.name')

# All vendors post the body-file content as-is; `@coderabbitai resolve` is
# never emitted (resolve-ALL hazard — see the header comment).
# By this point an explicitly given body file is guaranteed non-empty (the
# empty case was rejected with exit 2 above); only an omitted body-file
# argument means "resolve silently, no reply".
skip_reply=false
if [ -n "$body_file" ]; then
  body="$body_content"
else
  skip_reply=true
fi

# Look up the GraphQL thread id for this root comment's databaseId by
# paginating reviewThreads (same cursor-loop shape as
# skills/pr-monitor/scripts/prm's fetch_unresolved_threads).
find_thread_id() {
  local cursor="" has_next="true" raw pr_node page found="" err_file rc err
  while [ "$has_next" = "true" ]; do
    args=(-f owner="$owner" -f repo="$repo" -F pr="$pr")
    if [ -n "$cursor" ]; then
      args+=(-f cursor="$cursor")
    fi
    # Deliberately no --jq here: when the GraphQL response carries a
    # top-level `errors` entry (e.g. a nonexistent PR number), `gh api
    # graphql --jq` skips the filter and dumps the raw envelope to stdout
    # while still exiting non-zero. Capturing stdout and stderr separately
    # lets us tell "PR not found" (a valid GraphQL response with a null
    # pullRequest) apart from a real transport/auth/rate-limit failure
    # (empty or non-JSON stdout) and report the correct one instead of a
    # raw jq crash further down.
    err_file=$(mktemp)
    set +e
    raw=$(gh api graphql "${args[@]}" -f query='
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
      }' 2>"$err_file")
    rc=$?
    set -e
    err=$(cat "$err_file")
    rm -f "$err_file"
    if [ -z "$raw" ] || ! jq -e . >/dev/null 2>&1 <<<"$raw"; then
      echo "error: gh api graphql failed while looking up review thread for PR $pr (exit $rc): ${err:-<no output>}" >&2
      exit 3
    fi
    pr_node=$(jq -c '.data.repository.pullRequest' <<<"$raw")
    if [ "$pr_node" = "null" ]; then
      echo "error: PR $pr not found" >&2
      exit 3
    fi
    page=$(jq -c '.reviewThreads' <<<"$pr_node")
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

find_rc=0
thread_id=$(find_thread_id) || find_rc=$?
if [ "$find_rc" -ne 0 ]; then
  if [ "$find_rc" -eq 3 ]; then
    # find_thread_id already printed a descriptive error (PR not found, or
    # a transport/auth/rate-limit failure) to stderr.
    exit 1
  fi
  echo "error: could not find review thread for root comment id $comment_id on PR $pr" >&2
  exit 1
fi

# Guarded the same way as find_thread_id's lookup: `gh api graphql` can
# exit non-zero here (e.g. missing write/resolve permission), which under
# `set -e` would otherwise abort the script before the isResolved check or
# our own error message is reached. Capture stdout/stderr and the exit
# code explicitly so a transport/permission failure is reported clearly
# instead of letting `set -e` kill the script with gh's raw stderr.
mutation_err_file=$(mktemp)
set +e
mutation_resp=$(gh api graphql \
  -F id="$thread_id" \
  -f query='
    mutation($id: ID!) {
      resolveReviewThread(input: {threadId: $id}) {
        thread { id isResolved }
      }
    }' 2>"$mutation_err_file")
mutation_rc=$?
set -e
mutation_err=$(cat "$mutation_err_file")
rm -f "$mutation_err_file"

if [ -z "$mutation_resp" ] || ! jq -e . >/dev/null 2>&1 <<<"$mutation_resp"; then
  echo "error: resolveReviewThread mutation failed for thread $thread_id (exit $mutation_rc): ${mutation_err:-<no output>}" >&2
  exit 1
fi

is_resolved=$(jq -r '.data.resolveReviewThread.thread.isResolved' <<<"$mutation_resp")
if [ "$is_resolved" != "true" ]; then
  echo "error: resolveReviewThread mutation did not return isResolved=true for thread $thread_id" >&2
  exit 1
fi

# The reply is posted only after the thread lookup and resolve mutation have
# both succeeded (see the "Ordering (deliberate)" note above the usage
# comment) — a failure in either of those never leaves a misleading "Fixed
# in ..." reply behind on a thread that didn't actually get resolved.
if [ "$skip_reply" = false ]; then
  resp=$(gh api -X POST \
    -H "Accept: application/vnd.github+json" \
    "repos/$owner/$repo/pulls/$pr/comments/$comment_id/replies" \
    -f body="$body")
  jq -r '.html_url' <<<"$resp"
fi

echo "resolved $thread_id"
