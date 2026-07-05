#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SCRIPT="$ROOT_DIR/scripts/setup-web.sh"

assert_eq() {
  local expected=$1
  local actual=$2
  local message=$3

  if [[ "$actual" != "$expected" ]]; then
    echo "$message: expected $expected, got $actual" >&2
    exit 1
  fi
}

test_adds_origin_remote_from_github_repository() {
  local tmpdir repo output remote_url
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN
  repo="$tmpdir/repo"
  output="$tmpdir/setup-web-test.out"
  mkdir -p "$repo"
  git -C "$repo" init -q

  SETUP_WEB_DRY_RUN=1 SETUP_WEB_FORCE_GH_INSTALL=1 GITHUB_REPOSITORY=kanade0404/skills "$SCRIPT" "$repo" >"$output"

  remote_url=$(git -C "$repo" remote get-url origin)
  assert_eq "https://github.com/kanade0404/skills.git" "$remote_url" "origin remote"

  if ! grep -q "installing GitHub CLI" "$output"; then
    echo "expected dry-run output to mention GitHub CLI installation" >&2
    exit 1
  fi
}

test_root_dry_run_does_not_require_sudo() {
  local tmpdir repo output
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN
  repo="$tmpdir/repo"
  output="$tmpdir/setup-web-test.out"
  mkdir -p "$repo"
  git -C "$repo" init -q

  SETUP_WEB_DRY_RUN=1 SETUP_WEB_FORCE_GH_INSTALL=1 SETUP_WEB_ASSUME_EUID=0 GITHUB_REPOSITORY=kanade0404/skills "$SCRIPT" "$repo" >"$output"

  if grep -q "dry-run: sudo " "$output"; then
    echo "expected root dry-run install commands not to require sudo" >&2
    exit 1
  fi
}

test_adds_origin_remote_from_github_repository
test_root_dry_run_does_not_require_sudo
