#!/usr/bin/env bash
set -euo pipefail

# Prepare a cloud/web coding environment for this repository.
# - Installs GitHub CLI (gh) when missing.
# - Restores an origin remote when cloud checkout metadata leaves `git remote -v` empty.
#
# Usage: scripts/setup-web.sh [repo-dir]
# Optional environment variables:
#   SETUP_WEB_DRY_RUN=1        print install command instead of installing gh
#   SETUP_WEB_FORCE_GH_INSTALL=1  exercise install path even when gh exists
#   SETUP_WEB_REPOSITORY=owner/repo  preferred repo slug for origin
#   GH_REPO=owner/repo         fallback repo slug
#   GITHUB_REPOSITORY=owner/repo fallback repo slug used by GitHub Actions/cloud envs
#   SETUP_WEB_REMOTE_URL=https://... or git@... explicit origin URL

repo_dir=${1:-$(pwd)}

log() {
  printf '%s\n' "$*"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

run_or_print() {
  if [[ "${SETUP_WEB_DRY_RUN:-}" == "1" ]]; then
    printf 'dry-run: %s\n' "$*"
  else
    "$@"
  fi
}

effective_euid() {
  printf '%s\n' "${SETUP_WEB_ASSUME_EUID:-$EUID}"
}

privileged() {
  if [[ "$(effective_euid)" == "0" ]]; then
    run_or_print "$@"
  elif [[ "${SETUP_WEB_DRY_RUN:-}" == "1" ]]; then
    run_or_print sudo "$@"
  elif have sudo; then
    run_or_print sudo "$@"
  else
    log "This command needs elevated privileges but sudo is unavailable: $*"
    return 1
  fi
}

install_gh() {
  if have gh && [[ "${SETUP_WEB_FORCE_GH_INSTALL:-}" != "1" ]]; then
    log "gh is already installed: $(gh --version | head -n 1)"
    return
  fi

  if [[ "${SETUP_WEB_FORCE_GH_INSTALL:-}" == "1" ]]; then
    log "gh install path requested; installing GitHub CLI."
  else
    log "gh is not installed; installing GitHub CLI."
  fi

  if have apt-get; then
    privileged mkdir -p -m 755 /etc/apt/keyrings
    if [[ "${SETUP_WEB_DRY_RUN:-}" == "1" ]]; then
      if [[ "$(effective_euid)" == "0" ]]; then
        log "dry-run: curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null"
        log "dry-run: chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg"
        log "dry-run: add GitHub CLI apt source to /etc/apt/sources.list.d/github-cli.list"
      else
        log "dry-run: curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null"
        log "dry-run: sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg"
        log "dry-run: add GitHub CLI apt source to /etc/apt/sources.list.d/github-cli.list"
      fi
    else
      if [[ "$(effective_euid)" == "0" ]]; then
        curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
          | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
        chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
        printf 'deb [arch=%s signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main\n' \
          "$(dpkg --print-architecture)" \
          | tee /etc/apt/sources.list.d/github-cli.list >/dev/null
      else
        curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
          | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
        sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
        printf 'deb [arch=%s signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main\n' \
          "$(dpkg --print-architecture)" \
          | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
      fi
    fi
    privileged apt-get update
    privileged apt-get install -y gh
  elif have brew; then
    run_or_print brew install gh
  elif have apk; then
    privileged apk add --no-cache github-cli
  else
    log "No supported package manager found. Install GitHub CLI manually: https://cli.github.com/"
    return 1
  fi
}

repo_slug() {
  if [[ -n "${SETUP_WEB_REPOSITORY:-}" ]]; then
    printf '%s\n' "$SETUP_WEB_REPOSITORY"
  elif [[ -n "${GH_REPO:-}" ]]; then
    printf '%s\n' "$GH_REPO"
  elif [[ -n "${GITHUB_REPOSITORY:-}" ]]; then
    printf '%s\n' "$GITHUB_REPOSITORY"
  else
    return 1
  fi
}

ensure_origin_remote() {
  if ! git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    log "$repo_dir is not a git working tree; skipping remote setup."
    return
  fi

  if git -C "$repo_dir" remote get-url origin >/dev/null 2>&1; then
    log "origin remote already configured: $(git -C "$repo_dir" remote get-url origin)"
    return
  fi

  local remote_url
  if [[ -n "${SETUP_WEB_REMOTE_URL:-}" ]]; then
    remote_url="$SETUP_WEB_REMOTE_URL"
  else
    local slug
    slug=$(repo_slug) || {
      log "origin remote is missing and no repository slug was provided. Set SETUP_WEB_REPOSITORY=owner/repo or SETUP_WEB_REMOTE_URL."
      return 1
    }
    remote_url="https://github.com/${slug%.git}.git"
  fi

  git -C "$repo_dir" remote add origin "$remote_url"
  log "Added origin remote: $remote_url"
}

install_gh
ensure_origin_remote

log "web setup complete."
