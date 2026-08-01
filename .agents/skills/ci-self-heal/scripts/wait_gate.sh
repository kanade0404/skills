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

# gh 呼び出し 1 回あたりの上限秒。外部コマンドが stall しても deadline 契約
# (「deadline で必ず exit」) を破らないための有界化。timeout(1) は GNU coreutils
# 依存で macOS 標準に無いため、bash のみで watchdog を実装する。
gh_call_cap=60

# gh pr checks を有界実行する。stdout を $2 のファイルへ書き、
# exit code: gh の exit code そのまま / 124 = cap 超過で強制終了 (timeout(1) 互換)
bounded_gh_checks() {
  local out_file="$1" cap="$2" pid waited=0
  gh pr checks "$pr" --json name,bucket >"$out_file" 2>/dev/null &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$cap" ]; then
      kill "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
      return 124
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid"
}

start=$(date +%s)
prev=""
errs=0
gh_out=$(mktemp)
trap 'rm -f "$gh_out"' EXIT

while :; do
  # deadline 契約の厳守: gh 呼び出しの前に残り時間を確認し、呼び出し 1 回の
  # cap も残り時間で clamp する。これを怠ると cap (60s) + interval (30s) の
  # 分だけ exit が deadline を超過しうる (最悪 ~90 秒)。
  remaining=$(( deadline - ($(date +%s) - start) ))
  if [ "$remaining" -le 0 ]; then
    echo "WAIT_GATE_RESULT=deadline"
    exit 2
  fi
  call_cap=$gh_call_cap
  [ "$remaining" -lt "$call_cap" ] && call_cap=$remaining
  bounded_gh_checks "$gh_out" "$call_cap"
  rc=$?
  s=$(<"$gh_out")
  # gh pr checks は pending を含むと exit 8 を返すため、exit code ではなく
  # 「stdout が有効な JSON 配列か」で観測成功を判定する。errs に数えるのは
  # 観測不能 (cap 超過 / 出力なし / JSON 解析不能) のみ。
  if [ "$rc" -ne 124 ] && [ -n "$s" ] && jq -e 'type == "array"' <<<"$s" >/dev/null 2>&1; then
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
      echo "WAIT_GATE_RESULT=gh-unreachable (gh pr checks が ${errs} 回連続で観測不能 — 失敗または ${gh_call_cap} 秒 cap 超過)"
      exit 3
    fi
  fi
  if [ $(($(date +%s) - start)) -ge "$deadline" ]; then
    echo "WAIT_GATE_RESULT=deadline"
    exit 2
  fi
  # sleep も残り時間で clamp する (interval 分の deadline 超過を防ぐ。
  # 目覚めた直後にループ先頭の remaining 判定が deadline exit する)
  sleep_for=$interval
  remaining=$(( deadline - ($(date +%s) - start) ))
  [ "$remaining" -lt "$sleep_for" ] && sleep_for=$remaining
  sleep "$sleep_for"
done
