#!/usr/bin/env bash
# PreToolUse hook for the Explore subagent (.claude/agents/Explore.md).
#
# Explore is documented as read-only ("Never modify files"), but that was
# previously enforced only by the prompt — the `tools:` frontmatter grants
# unrestricted Bash, so a prompt-injected or confused run could still write,
# delete, or exfiltrate (CodeRabbit review, PR #104). This hook enforces the
# read-only contract at the tool layer instead.
#
# Fail-closed, two-stage check:
#   1. Reject outright if the command contains any shell metacharacter that
#      enables chaining, redirection, substitution, or multi-statement
#      execution (; | & < > ` $ or a newline) — this closes the "safe-looking
#      prefix, unsafe suffix via chaining" bypass regardless of what the
#      visible prefix looks like.
#   2. Otherwise, allow only if the command matches a fixed allowlist of
#      read-only prefixes. Default is deny.
#
# Input: Claude Code PreToolUse hook JSON on stdin, e.g.
#   {"tool_input": {"command": "git log -5"}}
# Output: exit 0 to allow, exit 2 (with a stderr reason) to block.
set -euo pipefail

cmd=$(jq -r '.tool_input.command // empty')

# No command to inspect (e.g. a malformed/empty invocation) — nothing to
# block, let the tool layer handle it.
if [ -z "$cmd" ]; then
  exit 0
fi

deny() {
  echo "Explore is read-only: $*" >&2
  exit 2
}

# Stage 1 — metacharacter fail-closed check.
if [[ "$cmd" == *[\;\|\&\<\>\`\$]* ]] || [[ "$cmd" == *$'\n'* ]]; then
  deny "command contains a shell metacharacter (; | & < > \` \$ or a newline)" \
"that could chain, redirect, or substitute — blocked even if the visible" \
"prefix looks read-only."
fi

# Stage 2 — fixed allowlist of read-only prefixes. Extend deliberately;
# anything not listed here is denied by the catch-all case below.
case "$cmd" in
  "git log"|"git log "*| \
  "git show"|"git show "*| \
  "git blame "*| \
  "git diff"|"git diff "*| \
  "git status"|"git status "*| \
  "git branch"|"git branch "*| \
  "git ls-files"|"git ls-files "*| \
  "git rev-parse "*| \
  "rg "*| \
  "grep "*| \
  "find "*| \
  "ls"|"ls "*| \
  "cat "*| \
  "wc "*| \
  "head "*| \
  "tail "*| \
  "pwd")
    exit 0
    ;;
  *)
    deny "command not in the read-only allowlist (git log/show/blame/diff/status/branch/ls-files/rev-parse, rg, grep, find, ls, cat, wc, head, tail, pwd)." \
"Use the Read/Grep/Glob tools for other lookups, or extend the allowlist in" \
"scripts/explore-readonly-guard.sh if this is a legitimate read-only need."
    ;;
esac
