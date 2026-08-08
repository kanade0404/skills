#!/usr/bin/env bash
# SessionStart hook: ensure this checkout's nix devshell (flake.nix) is
# authorized with direnv and built. Must never block or fail the session:
# the (possibly slow) first build runs in the background, and every failure
# path exits 0 with a warning on stderr.
#
# `direnv allow` is opt-in (SKILLS_DIRENV_AUTO_ALLOW=1), not automatic: this
# repository has an automated flow that checks out arbitrary PR branches
# into worktrees, and unconditionally allowing would defeat direnv's
# trust-on-first-use model for whatever .envrc happens to be checked out.
#
# The background devshell build below is gated the same way: `nix develop`
# executes the flake's shellHook unconditionally (direnv is not involved at
# all for that invocation), so triggering it every SessionStart whenever nix
# happens to be on PATH would run untrusted shellHook code for whatever
# branch is checked out into this worktree, regardless of the direnv-allow
# gate above. The build only runs when EITHER (a) SKILLS_DIRENV_AUTO_ALLOW=1
# (the same opt-in as `direnv allow` above), OR (b) $root's own .envrc has
# already been `direnv allow`-ed previously (by a human, outside this hook)
# — in that case direnv's normal operation would build the flake on its own
# the next time a shell enters $root anyway, so building it here does not
# create new exposure. If neither holds, no build is started.
#
# Note: the devshell build below runs detached (nohup + disown). If it
# fails after this hook has already exited, there is no later notification
# for that failure — the warning below is the only signal you get; check
# the log path it prints if the devshell later seems broken/missing.
set -u

# This script machine-parses `direnv status --json` output with grep/sed
# below (see root_is_direnv_allowed). Inherited terminal color/decoration
# settings (e.g. CLICOLOR_FORCE=1) can inject ANSI escapes into that output
# even when piped, silently breaking the grep/sed parsing — disable them at
# the entry point rather than relying on the environment being clean.
export NO_COLOR=1
export CLICOLOR_FORCE=0
unset GH_FORCE_TTY

root=$(cd "$(dirname "$0")/.." && pwd)

warn() { printf 'session-setup: %s\n' "$*" >&2; }

# root_is_direnv_allowed: true iff "$1"'s own .envrc has already been
# `direnv allow`-ed. We must not shell out to jq — jq typically lives inside
# the not-yet-built devshell, so it may not be on PATH yet, which is exactly
# the situation this function runs in. `direnv status --json` is parsed with
# grep/sed instead: its foundRC object is a flat, one-key-per-line pretty
# printed block (`"foundRC": {`, `"allowed": N`, `"path": "..."`, `}`), a
# stable characteristic of Go's json.MarshalIndent across direnv versions,
# so grep -A2 right after the "foundRC": { line reliably captures both
# fields without needing a real JSON parser. `direnv status` always reports
# on the *current* directory, so we cd into "$1" in a subshell first.
root_is_direnv_allowed() {
  local target="$1/.envrc" json block allowed_line path_line path_val
  json=$(cd "$1" 2>/dev/null && direnv status --json 2>/dev/null) || return 1
  [ -n "$json" ] || return 1
  block=$(printf '%s\n' "$json" | grep -A2 '"foundRC": {') || return 1
  [ -n "$block" ] || return 1
  allowed_line=$(printf '%s\n' "$block" | grep '"allowed"') || return 1
  path_line=$(printf '%s\n' "$block" | grep '"path"') || return 1
  path_val=$(printf '%s\n' "$path_line" | sed -E 's/.*"path"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/')
  [ "$path_val" = "$target" ] || return 1
  printf '%s\n' "$allowed_line" | grep -Eq '"allowed"[[:space:]]*:[[:space:]]*0[,]?[[:space:]]*$'
}

if ! command -v nix >/dev/null 2>&1; then
  warn "nix not found; skipping devshell setup (see https://nixos.org/download)"
  exit 0
fi

# build_authorized gates the background `nix develop` build below (see the
# header comment for the threat this closes). It is deliberately computed
# independently of direnv's presence: SKILLS_DIRENV_AUTO_ALLOW=1 authorizes
# the build on its own even if direnv itself is missing.
build_authorized=0
if [ "${SKILLS_DIRENV_AUTO_ALLOW:-}" = "1" ]; then
  build_authorized=1
fi

if command -v direnv >/dev/null 2>&1; then
  if [ "${SKILLS_DIRENV_AUTO_ALLOW:-}" = "1" ]; then
    direnv allow "$root" || warn "direnv allow failed for $root"
  else
    warn "direnv found but not auto-allowed; set SKILLS_DIRENV_AUTO_ALLOW=1 to auto-allow, or run 'direnv allow $root' manually"
    if root_is_direnv_allowed "$root"; then
      build_authorized=1
    fi
  fi
else
  warn "direnv not found; enter the devshell manually with 'nix develop'"
fi

if [ "$build_authorized" != 1 ]; then
  warn "skipping background devshell build for $root (not authorized): run 'direnv allow $root' manually, or set SKILLS_DIRENV_AUTO_ALLOW=1 to auto-allow and build automatically on future sessions"
  exit 0
fi

# Build/install the devshell in the background so session start is not
# blocked by a cold nix build. Logged under .direnv/ (gitignored, per-repo)
# rather than a fixed path under $TMPDIR, which would be predictable and
# shared/clobbered across concurrent sessions (CWE-377).
log_dir="$root/.direnv"
mkdir -p "$log_dir" 2>/dev/null || true
log="$log_dir/session-setup.log"
nohup nix develop --extra-experimental-features 'nix-command flakes' \
  "$root#default" --command true >"$log" 2>&1 &
disown 2>/dev/null || true
warn "devshell build started in background (log: $log; failures after this point are not re-notified, check the log)"
exit 0
