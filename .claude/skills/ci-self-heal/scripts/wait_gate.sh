#!/usr/bin/env bash
# wait_gate.sh — CI 終端待ちゲート。終端 or deadline で必ず exit し、結果を exit code で機械分類する。
#
# Usage: wait_gate.sh <pr-number> [deadline-sec] [interval-sec]
#   deadline-sec: 既定 480 (8 分)。foreground 呼び出し時はツール timeout (10 分) 未満に保つこと。
#   interval-sec: 既定 30。
#
# Exit codes:
#   0 = 全 check 終端かつ緑 (fail / cancel が 0。skipping / neutral は非失敗)
#   1 = 終端したが fail または cancel を含む
#   2 = deadline 到達 (未終端のまま)
#   3 = gh の連続失敗 (観測不能)
#
# 待ち方の契約 (2026-08-01 実測):
#   - subagent から使う場合は foreground で呼ぶ (唯一の待ち方 — subagent の bg タスクは
#     return 時に harness に回収され、await 原語 TaskOutput も subagent には存在しない)
#   - セッション main から使う場合は run_in_background で呼んでよい (exit → 完了通知が届く)
set -u
export NO_COLOR=1
export CLICOLOR_FORCE=0
unset GH_FORCE_TTY 2>/dev/null || true

pr="${1:?usage: wait_gate.sh <pr-number> [deadline-sec] [interval-sec]}"
deadline="${2:-480}"
interval="${3:-30}"

start=$(date +%s)
prev=""
errs=0

while :; do
  if s=$(gh pr checks "$pr" --json name,bucket 2>/dev/null); then
    errs=0
    cur=$(jq -r '.[] | select(.bucket!="pending") | "\(.name): \(.bucket)"' <<<"$s" | sort)
    # 新たに終端した check を 1 行ずつ emit する (pass/fail/cancel/skipping すべて —
    # 緑でも赤でも沈黙しない coverage 規律)
    comm -13 <(printf '%s\n' "$prev") <(printf '%s\n' "$cur")
    prev="$cur"
    # `length > 0` ガード必須: push 直後は GitHub が check を登録する前に `[]` が返り、
    # 空配列に対する `all(...)` は vacuous-true → CI 未起動を「緑」と誤認する
    if jq -e 'length > 0 and all(.bucket != "pending")' <<<"$s" >/dev/null; then
      if jq -e 'any(.bucket == "fail" or .bucket == "cancel")' <<<"$s" >/dev/null; then
        echo "WAIT_GATE_RESULT=red"
        exit 1
      fi
      echo "WAIT_GATE_RESULT=green"
      exit 0
    fi
  else
    errs=$((errs + 1))
    if [ "$errs" -ge 5 ]; then
      echo "WAIT_GATE_RESULT=gh-unreachable (gh pr checks が ${errs} 回連続失敗)"
      exit 3
    fi
  fi
  if [ $(($(date +%s) - start)) -ge "$deadline" ]; then
    echo "WAIT_GATE_RESULT=deadline"
    exit 2
  fi
  sleep "$interval"
done
