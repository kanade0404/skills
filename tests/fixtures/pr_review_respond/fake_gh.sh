#!/usr/bin/env bash
# Stateful stand-in for `gh`, used by tests/test_pr_review_respond_scripts.py.
# Copied into a temp dir as `gh` and put first on PATH. Never talks to GitHub.
#
# State dir ($FAKE_GH_STATE):
#   calls.log       one line per invocation: "GH_REPO=<value> <args...>"
#   prs             lines "owner/repo#<n>" that exist
#   issues/<n>.json issues created via `gh api -X POST repos/<o>/<r>/issues`
# Env:
#   FAKE_GH_CWD_REPO     what `gh repo view` reports (like a clone's remote)
#   FAKE_GH_GRAPHQL_NULL if "1", GraphQL answers with a null pullRequest
#
# Fidelity notes (match real gh 2.x behavior):
#   - `gh repo view` IGNORES GH_REPO (always the cwd repository)
#   - `gh pr view` honors -R, then GH_REPO, then the cwd repository
#   - `gh search issues` matching is fuzzy, so this fake returns EVERY stored
#     issue of the repo; callers must filter exactly themselves
set -euo pipefail
: "${FAKE_GH_STATE:?}"
mkdir -p "$FAKE_GH_STATE/issues"
touch "$FAKE_GH_STATE/prs"
printf 'GH_REPO=%s %s\n' "${GH_REPO:-}" "$*" >>"$FAKE_GH_STATE/calls.log"

repo_flag=""
jqf=""
fields=()
rest=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -R|--repo) repo_flag="$2"; shift 2 ;;
    --jq) jqf="$2"; shift 2 ;;
    -f|-F) fields+=("$2"); shift 2 ;;
    *) rest+=("$1"); shift ;;
  esac
done

emit() {
  if [ -n "$jqf" ]; then jq -r "$jqf"; else cat; fi
}

field() {
  local k="$1" kv
  for kv in "${fields[@]+"${fields[@]}"}"; do
    if [ "${kv%%=*}" = "$k" ]; then
      printf '%s' "${kv#*=}"
      return 0
    fi
  done
  return 1
}

pr_exists() {
  grep -qxF "$1#$2" "$FAKE_GH_STATE/prs"
}

case "${rest[0]:-} ${rest[1]:-}" in
  "repo view")
    r="${FAKE_GH_CWD_REPO:?}"
    jq -n --arg o "${r%/*}" --arg n "${r#*/}" \
      '{owner: {login: $o}, name: $n, nameWithOwner: "\($o)/\($n)"}' | emit
    ;;
  "pr view")
    n="${rest[2]}"
    r="${repo_flag:-${GH_REPO:-${FAKE_GH_CWD_REPO:?}}}"
    if ! pr_exists "$r" "$n"; then
      echo "GraphQL: Could not resolve to a PullRequest with the number of $n. (repository.pullRequest)" >&2
      exit 1
    fi
    jq -n --arg r "$r" --argjson n "$n" '{
        number: $n, title: "t", url: "https://github.com/\($r)/pull/\($n)",
        headRefOid: "abc", baseRefName: "main",
        headRepository: {name: ($r | split("/")[1])},
        headRepositoryOwner: {login: ($r | split("/")[0])}
      }' | emit
    ;;
  "pr comment" | "pr edit" | "pr checks")
    echo "https://github.com/${repo_flag:-${GH_REPO:-$FAKE_GH_CWD_REPO}}/pull/${rest[2]}#issuecomment-1"
    ;;
  "api graphql")
    o=$(field owner)
    r=$(field repo)
    n=$(field pr)
    if [ "${FAKE_GH_GRAPHQL_NULL:-0}" = "1" ] || ! pr_exists "$o/$r" "$n"; then
      echo '{"data":{"repository":{"pullRequest":null}}}'
      exit 0
    fi
    echo '{"data":{"repository":{"pullRequest":{"author":{"login":"me"},"reviewThreads":{"pageInfo":{"hasNextPage":false,"endCursor":null},"nodes":[]}}}}}'
    ;;
  "search issues")
    # --repo is captured as repo_flag; the query itself is ignored on purpose
    jq -s --arg r "$repo_flag" '[.[] | select(.repo == $r) | {number, url, body}]' \
      "$FAKE_GH_STATE"/issues/*.json 2>/dev/null || echo '[]'
    ;;
  *)
    if [ "${rest[0]:-}" = "api" ]; then
      path="${rest[${#rest[@]}-1]}"
      case "$path" in
        repos/*/issues)
          r="${path#repos/}"
          r="${r%/issues}"
          count=$(find "$FAKE_GH_STATE/issues" -name '*.json' | wc -l | tr -d ' ')
          num=$((count + 1))
          jq -n --arg r "$r" --argjson n "$num" --arg t "$(field title)" --arg b "$(field body)" \
            '{repo: $r, number: $n, title: $t, body: $b,
              url: "https://github.com/\($r)/issues/\($n)",
              html_url: "https://github.com/\($r)/issues/\($n)"}' \
            | tee "$FAKE_GH_STATE/issues/$num.json"
          ;;
        */replies)
          echo "{\"html_url\": \"https://github.com/${path#repos/}\"}"
          ;;
        *)
          # paginated REST lists (reviews, issue comments, ...)
          echo '[]'
          ;;
      esac
    else
      echo "fake gh: unsupported invocation: ${rest[*]}" >&2
      exit 99
    fi
    ;;
esac
