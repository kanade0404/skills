#!/usr/bin/env bash
set -euo pipefail

# This suite machine-parses stub logs and command output (grep) throughout.
# Disable inherited terminal color/decoration settings at the entry point so
# a colorized environment can't corrupt what's being asserted on, matching
# the same discipline applied inside scripts/session-setup.sh itself.
export NO_COLOR=1
export CLICOLOR_FORCE=0
unset GH_FORCE_TTY

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="$ROOT_DIR/scripts/session-setup.sh"

# Minimal system PATH that (on this dev machine) does not contain nix or
# direnv, so tests can control their presence/absence deterministically via
# a stub bin directory prepended in front of it.
BASE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"

# poll_for_pattern waits (up to ~2s) for "$file" to contain "$pattern",
# returning success as soon as it appears. The build the script under test
# starts is detached (nohup + disown), so its stub-log line is written
# asynchronously — checking $file immediately after the script under test
# returns is a race. Callers that assert *absence* wait out the full budget
# before concluding the pattern never appeared.
poll_for_pattern() {
  local file=$1
  local pattern=$2
  local attempts=${3:-40}
  local i
  for ((i = 0; i < attempts; i++)); do
    if [[ -f "$file" ]] && grep -q "$pattern" "$file"; then
      return 0
    fi
    sleep 0.05
  done
  [[ -f "$file" ]] && grep -q "$pattern" "$file"
}

assert_eq() {
  local expected=$1
  local actual=$2
  local message=$3

  if [[ "$actual" != "$expected" ]]; then
    echo "$message: expected $expected, got $actual" >&2
    exit 1
  fi
}

# write_stub creates an executable no-op command at "$dir/$name" that
# records every invocation (name + args) as a line in "$STUB_LOG".
write_stub() {
  local dir=$1
  local name=$2
  cat >"$dir/$name" <<'EOF'
#!/bin/sh
printf '%s %s\n' "$(basename "$0")" "$*" >>"$STUB_LOG"
exit 0
EOF
  chmod +x "$dir/$name"
}

# write_direnv_status_stub creates an executable "direnv" stub that, like
# write_stub, logs every invocation, but additionally answers
# `direnv status --json` with a canned response describing whether
# "$envrc_path" is allowed (0) or not (1) — mirroring the real `direnv
# status --json` shape (state.foundRC.{allowed,path}) so the gate logic
# under test can be exercised without a real direnv install.
write_direnv_status_stub() {
  local dir=$1
  local allowed=$2
  local envrc_path=$3
  cat >"$dir/direnv" <<EOF
#!/bin/sh
printf '%s %s\n' "\$(basename "\$0")" "\$*" >>"\$STUB_LOG"
if [ "\$1" = "status" ] && [ "\$2" = "--json" ]; then
  cat <<JSON
{
  "config": {},
  "state": {
    "foundRC": {
      "allowed": ${allowed},
      "path": "${envrc_path}"
    },
    "loadedRC": null
  }
}
JSON
fi
exit 0
EOF
  chmod +x "$dir/direnv"
}

# make_script_copy copies session-setup.sh under a fresh tmp "repo" so each
# test gets its own $root (the script derives root from its own location)
# and never touches this checkout's real .direnv/ directory.
make_script_copy() {
  local tmpdir=$1
  mkdir -p "$tmpdir/scripts"
  cp "$SCRIPT" "$tmpdir/scripts/session-setup.sh"
  chmod +x "$tmpdir/scripts/session-setup.sh"
}

test_exits_zero_and_warns_when_nix_is_missing() {
  local tmpdir rc
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN
  make_script_copy "$tmpdir"

  set +e
  PATH="$BASE_PATH" bash "$tmpdir/scripts/session-setup.sh" >"$tmpdir/out" 2>"$tmpdir/err"
  rc=$?
  set -e

  assert_eq "0" "$rc" "exit code when nix is missing"
  if ! grep -q "nix not found" "$tmpdir/err"; then
    echo "expected stderr to warn that nix was not found" >&2
    exit 1
  fi
}

test_exits_zero_when_direnv_is_missing() {
  local tmpdir stubbin rc
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN
  make_script_copy "$tmpdir"
  stubbin="$tmpdir/stubbin"
  mkdir -p "$stubbin"
  STUB_LOG="$tmpdir/stub.log" write_stub "$stubbin" nix

  set +e
  STUB_LOG="$tmpdir/stub.log" PATH="$stubbin:$BASE_PATH" \
    bash "$tmpdir/scripts/session-setup.sh" >"$tmpdir/out" 2>"$tmpdir/err"
  rc=$?
  set -e

  assert_eq "0" "$rc" "exit code when direnv is missing"
  if ! grep -q "direnv not found" "$tmpdir/err"; then
    echo "expected stderr to warn that direnv was not found" >&2
    exit 1
  fi
}

test_direnv_allow_not_called_without_auto_allow_env() {
  local tmpdir stubbin rc
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN
  make_script_copy "$tmpdir"
  stubbin="$tmpdir/stubbin"
  mkdir -p "$stubbin"
  write_stub "$stubbin" nix
  write_stub "$stubbin" direnv

  set +e
  env -u SKILLS_DIRENV_AUTO_ALLOW \
    STUB_LOG="$tmpdir/stub.log" PATH="$stubbin:$BASE_PATH" \
    bash "$tmpdir/scripts/session-setup.sh" >"$tmpdir/out" 2>"$tmpdir/err"
  rc=$?
  set -e

  assert_eq "0" "$rc" "exit code without SKILLS_DIRENV_AUTO_ALLOW"
  if [[ -f "$tmpdir/stub.log" ]] && grep -q "^direnv allow" "$tmpdir/stub.log"; then
    echo "expected 'direnv allow' NOT to be called without SKILLS_DIRENV_AUTO_ALLOW=1" >&2
    exit 1
  fi
  if ! grep -q "SKILLS_DIRENV_AUTO_ALLOW" "$tmpdir/err"; then
    echo "expected stderr to mention SKILLS_DIRENV_AUTO_ALLOW as the opt-in knob" >&2
    exit 1
  fi
}

test_direnv_allow_called_with_auto_allow_env() {
  local tmpdir stubbin rc
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN
  make_script_copy "$tmpdir"
  stubbin="$tmpdir/stubbin"
  mkdir -p "$stubbin"
  write_stub "$stubbin" nix
  write_stub "$stubbin" direnv

  set +e
  SKILLS_DIRENV_AUTO_ALLOW=1 \
    STUB_LOG="$tmpdir/stub.log" PATH="$stubbin:$BASE_PATH" \
    bash "$tmpdir/scripts/session-setup.sh" >"$tmpdir/out" 2>"$tmpdir/err"
  rc=$?
  set -e

  assert_eq "0" "$rc" "exit code with SKILLS_DIRENV_AUTO_ALLOW=1"
  if ! grep -q "^direnv allow" "$tmpdir/stub.log"; then
    echo "expected 'direnv allow' to be called when SKILLS_DIRENV_AUTO_ALLOW=1" >&2
    exit 1
  fi
  if [[ ! -f "$tmpdir/.direnv/session-setup.log" ]]; then
    echo "expected background build log at \$root/.direnv/session-setup.log" >&2
    exit 1
  fi
}

test_build_not_started_when_gate_off_and_root_not_allowed() {
  local tmpdir stubbin rc
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN
  make_script_copy "$tmpdir"
  stubbin="$tmpdir/stubbin"
  mkdir -p "$stubbin"
  write_stub "$stubbin" nix
  # root's own .envrc is NOT allowed (allowed: 1 == direnv's "not allowed").
  write_direnv_status_stub "$stubbin" 1 "$tmpdir/.envrc"

  set +e
  env -u SKILLS_DIRENV_AUTO_ALLOW \
    STUB_LOG="$tmpdir/stub.log" PATH="$stubbin:$BASE_PATH" \
    bash "$tmpdir/scripts/session-setup.sh" >"$tmpdir/out" 2>"$tmpdir/err"
  rc=$?
  set -e

  assert_eq "0" "$rc" "exit code when gate is off and root is not direnv-allowed"
  if poll_for_pattern "$tmpdir/stub.log" "^nix develop"; then
    echo "expected 'nix develop' NOT to be called when gate is off and root is not direnv-allowed" >&2
    exit 1
  fi
}

test_build_started_when_root_already_direnv_allowed_without_auto_allow_env() {
  local tmpdir stubbin rc
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN
  make_script_copy "$tmpdir"
  stubbin="$tmpdir/stubbin"
  mkdir -p "$stubbin"
  write_stub "$stubbin" nix
  # root's own .envrc IS already allowed (allowed: 0 == direnv's "allowed"),
  # e.g. because a human previously ran `direnv allow` manually.
  write_direnv_status_stub "$stubbin" 0 "$tmpdir/.envrc"

  set +e
  env -u SKILLS_DIRENV_AUTO_ALLOW \
    STUB_LOG="$tmpdir/stub.log" PATH="$stubbin:$BASE_PATH" \
    bash "$tmpdir/scripts/session-setup.sh" >"$tmpdir/out" 2>"$tmpdir/err"
  rc=$?
  set -e

  assert_eq "0" "$rc" "exit code when root is already direnv-allowed"
  if ! poll_for_pattern "$tmpdir/stub.log" "^nix develop"; then
    echo "expected 'nix develop' to be called when root is already direnv-allowed, even without SKILLS_DIRENV_AUTO_ALLOW" >&2
    exit 1
  fi
  if [[ ! -f "$tmpdir/.direnv/session-setup.log" ]]; then
    echo "expected background build log at \$root/.direnv/session-setup.log" >&2
    exit 1
  fi
}

test_build_started_with_auto_allow_env_even_when_root_not_allowed() {
  local tmpdir stubbin rc
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN
  make_script_copy "$tmpdir"
  stubbin="$tmpdir/stubbin"
  mkdir -p "$stubbin"
  write_stub "$stubbin" nix
  # root's own .envrc is NOT (yet) allowed; SKILLS_DIRENV_AUTO_ALLOW=1 must
  # be sufficient on its own to authorize the build.
  write_direnv_status_stub "$stubbin" 1 "$tmpdir/.envrc"

  set +e
  SKILLS_DIRENV_AUTO_ALLOW=1 \
    STUB_LOG="$tmpdir/stub.log" PATH="$stubbin:$BASE_PATH" \
    bash "$tmpdir/scripts/session-setup.sh" >"$tmpdir/out" 2>"$tmpdir/err"
  rc=$?
  set -e

  assert_eq "0" "$rc" "exit code with SKILLS_DIRENV_AUTO_ALLOW=1 and root not yet allowed"
  if ! poll_for_pattern "$tmpdir/stub.log" "^nix develop"; then
    echo "expected 'nix develop' to be called when SKILLS_DIRENV_AUTO_ALLOW=1, regardless of prior direnv-allow state" >&2
    exit 1
  fi
}

test_exits_zero_and_warns_when_nix_is_missing
test_exits_zero_when_direnv_is_missing
test_direnv_allow_not_called_without_auto_allow_env
test_direnv_allow_called_with_auto_allow_env
test_build_not_started_when_gate_off_and_root_not_allowed
test_build_started_when_root_already_direnv_allowed_without_auto_allow_env
test_build_started_with_auto_allow_env_even_when_root_not_allowed

echo "session-setup-test.sh: all tests passed"
