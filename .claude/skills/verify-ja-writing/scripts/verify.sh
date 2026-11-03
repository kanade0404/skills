#!/usr/bin/env bash
# ja-writing プラグインを、利用者と同じ経路(ヘッドレスの Claude Code)で動かして証拠を残す。
# 使い方は SKILL.md を参照。サブコマンド: launch doctor drive hook lint yomiyasu cleanup evidence
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)
PLUGIN="$REPO/plugins/ja-writing"
TEXTLINT_SCRIPTS="$PLUGIN/skills/textlint/scripts"
YOMIYASU_SCRIPTS="$PLUGIN/skills/yomiyasu/scripts"
FIXTURES="$SCRIPT_DIR/../fixtures"

: "${RUN_ID:?RUN_ID を設定する(例: RUN_ID=\$(date +%Y%m%d-%H%M%S))}"
# . は使えない。セッションのフォルダ名は / と . を - に潰すので、foo.bar と foo-bar が同じ名前になり、記録が混ざる。
case "$RUN_ID" in *[!A-Za-z0-9_-]*) echo "RUN_ID は英数字と _ - だけにする" >&2; exit 2 ;; esac
# 外の設定が検査の規則を差し替えないよう、同梱の設定だけを使わせる。
unset TEXTLINT_CONFIG

WORK_ROOT="${VERIFY_WORK_ROOT:-${TMPDIR:-/tmp}}"
WORK="${WORK_ROOT%/}/verify-ja-writing/$RUN_ID"
WS="$WORK/ws"
DEPS="$WORK/deps"
YOMIYASU_STATE="$WORK/yomiyasu-state"
EVID="$REPO/.verify-evidence/ja-writing/$RUN_ID"
MARKER="$WORK/.verify-ja-writing"
MODEL="${VERIFY_MODEL:-haiku}"
BUDGET="${VERIFY_BUDGET_USD:-0.30}"

# 許可を求める画面がないので、Bash はスキルが使うスクリプトの単独の呼び出しだけを通す。
# python3 -c や任意のスクリプトは通さない。利用者が承認する代わりに、その形をモデルに伝える。
skill_bash_rule() {
  echo "Permission note for this session: nobody can approve permission prompts. A Bash command is auto-approved only when it is one plain invocation of 'python3 ' or '/usr/bin/python3 ' (optionally with -B) followed by the absolute path of one of these scripts: $*, with the arguments the skill documents (the file under the current directory, or the run and verify subcommands). No other Python command is allowed (no -c, no -m, no other script), and no '&&', ';', pipes, redirects, or variable assignments. Run each script as its own Bash call, and create or change files with the Write and Edit tools."
}

# allowedTools の規則を作る: allowed_rules <スクリプトの絶対パス> <引数の形>...
# python3 と /usr/bin/python3、-B の有無ごとに、引数の形を固定した規則を出す(スクリプトだけを許し、任意の引数は許さない)。
allowed_rules() {
  local script="$1" interp flag args
  shift
  for args in "$@"; do
    for interp in python3 /usr/bin/python3; do
      for flag in "" "-B "; do
        printf 'Bash(%s %s%s %s)\n' "$interp" "$flag" "$script" "$args"
      done
    done
  done
}

# 規則のコマンド文字列は引用符なしの絶対パスで書く。空白や記号を含むパスでは一致しないので、先に止める。
require_plain_paths() {
  case "$PLUGIN$WS" in *[!A-Za-z0-9._/+-]*) die "チェックアウトか作業ディレクトリのパスに空白や記号がある。許可の規則が一致しないので、plain なパスで実行する" ;; esac
}

# フックは /bin/sh 経由の python3 で動く。対話シェルの alias(mise の shim など)を避け、同じ解決を使う。
PY=$(/bin/sh -c 'command -v python3')

die() { echo "NG: $*" >&2; exit 1; }

require_launched() {
  [ -f "$MARKER" ] || die "$WORK がない。先に launch を実行する"
}

claude_bin() {
  # alias ではなく実体を使う。利用者の alias は追加のシステムプロンプトを足すため。
  local found
  found=$(/bin/sh -c 'command -v claude') || die "claude が PATH にない"
  echo "$found"
}

# ヘッドレスの実行が ~/.claude/projects/ に作るセッションのフォルダ。名前は作業ディレクトリの / と . を - にしたもの。
session_dir() {
  local real slug
  real=$( (cd "$WS" 2> /dev/null && pwd -P) || echo "$WS")
  slug=$(printf '%s' "$real" | tr '/.' '--')
  echo "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/$slug"
}

cmd_launch() {
  # 同じ RUN_ID の同時の launch が作業ディレクトリを共有しないよう、既にあれば止める。
  [ ! -e "$WORK" ] || die "$WORK が既にある。RUN_ID を変えるか、先に cleanup を実行する"
  mkdir -p "$(dirname "$WORK")" "$EVID"
  mkdir "$WORK" "$WS" "$DEPS" "$YOMIYASU_STATE"
  echo "$RUN_ID" > "$MARKER"
  # 隔離したワークスペースに .claude/ も .textlintrc も置かない。同梱の設定だけで検査させるため。
  echo "launch: SessionStart フックと同じコードで textlint の依存を $DEPS に入れる"
  TEXTLINT_SKILL_DEPS_DIR="$DEPS" CLAUDE_PROJECT_DIR="$WS" \
    "$PY" -B "$TEXTLINT_SCRIPTS/session_start.py" > "$EVID/launch.session_start.stdout" 2> "$EVID/launch.session_start.stderr"
  if [ -f "$DEPS/node_modules/@textlint/kernel/package.json" ]; then
    echo "READY: $DEPS/node_modules/@textlint/kernel/package.json"
    echo "WORK=$WORK"
    echo "EVIDENCE=$EVID"
  else
    cat "$EVID/launch.session_start.stderr" >&2
    die "textlint の依存が入っていない"
  fi
}

cmd_doctor() {
  local ok=0 claude version
  # check <説明> <コマンド> [引数...]: eval を使わず、引数をそのまま実行する。
  check() { local desc="$1"; shift; if "$@" > /dev/null 2>&1; then echo "OK: $desc"; else echo "NG: $desc"; ok=1; fi; }
  py_ok() { "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; }
  bun_ok() { [ -n "$1" ] && "$PY" -c 'import sys; v=tuple(map(int,sys.argv[1].split(".")[:2])); sys.exit(v < (1, 3))' "$1"; }
  claude_logged_in() { "$1" auth status | grep -q '"loggedIn": true'; }
  echo "repo: $REPO ($(git -C "$REPO" rev-parse --short HEAD), branch $(git -C "$REPO" branch --show-current))"
  echo "python3(/bin/sh): $PY $("$PY" --version 2>&1)"
  check "python3 が 3.9 以上" py_ok
  version=$(bun --version 2>/dev/null || true)
  echo "bun: ${version:-なし}"
  check "bun が 1.3 以上" bun_ok "$version"
  claude=$(/bin/sh -c 'command -v claude' || true)
  echo "claude: ${claude:-なし} $([ -n "$claude" ] && "$claude" --version 2>/dev/null)"
  check "claude にログインしている" claude_logged_in "$claude"
  check "plugin.json と hooks.json が検証を通る" "$claude" plugin validate "$PLUGIN"
  check "launch 済み($MARKER)" test -f "$MARKER"
  check "textlint の依存がある($DEPS)" test -f "$DEPS/node_modules/@textlint/kernel/package.json"
  check "ワークスペースに .claude/ がない" test ! -e "$WS/.claude"
  check "証拠の置き場が git に無視される" git -C "$REPO" check-ignore -q "$EVID"
  return "$ok"
}

# ヘッドレスの Claude Code に ja-writing だけを読ませて、1 回のターンを走らせる。
run_headless() {
  local name="$1" prompt="$2" out="$EVID/$1" claude pid status
  shift 2
  claude=$(claude_bin)
  mkdir -p "$out"
  printf '%s\n' "$prompt" > "$out/prompt.txt"
  echo "drive: $name を $MODEL で実行する(上限 \$$BUDGET)。証拠は $out"
  (
    cd "$WS"
    TEXTLINT_SKILL_DEPS_DIR="$DEPS" exec "$claude" -p "$prompt" \
      --plugin-dir "$PLUGIN" \
      --setting-sources "" \
      --strict-mcp-config \
      --model "$MODEL" \
      --max-budget-usd "$BUDGET" \
      --permission-mode acceptEdits \
      --permission-prompts none \
      --output-format stream-json --verbose --include-hook-events \
      --debug-file "$out/debug.log" \
      "$@"
  ) > "$out/transcript.jsonl" 2> "$out/stderr.log" < /dev/null &
  pid=$!
  echo "$pid" > "$WORK/claude.pid"
  status=0
  wait "$pid" || status=$?
  rm -f "$WORK/claude.pid"
  echo "$status" > "$out/exit_code"
  # 実行後のワークスペースの中身(結果のファイル)を写す。
  mkdir -p "$out/files"
  cp -R "$WS/." "$out/files/"
  # Claude Code がこの実行のために書いたセッションの記録も残す(cleanup で元は消す)。
  if [ -d "$(session_dir)" ]; then
    mkdir -p "$out/session"
    cp -R "$(session_dir)/." "$out/session/"
  fi
  summarize "$out"
  echo "exit_code=$status"
  return "$status"
}

summarize() {
  local out="$1"
  # フックの結果と最後の結果だけを抜き出す。transcript.jsonl が一次の証拠。
  jq -c 'select(.type == "system" and (.subtype == "hook_response" or .subtype == "hook_started"))
         | {subtype, hook_event: (.hook_event // .hook_event_name), hook_name, exit_code, outcome, stdout, stderr, output}' \
    "$out/transcript.jsonl" > "$out/hooks.jsonl" 2>/dev/null || true
  jq -c 'select(.type == "system" and .subtype == "init") | {plugins, model, cwd, tools: (.tools | length)}' \
    "$out/transcript.jsonl" > "$out/init.json" 2>/dev/null || true
  jq -c 'select(.type == "result") | {subtype, is_error, num_turns, total_cost_usd, result}' \
    "$out/transcript.jsonl" > "$out/result.json" 2>/dev/null || true
  echo "--- hooks ($out/hooks.jsonl)"
  jq -r 'select(.subtype == "hook_response")
         | "\(.hook_name) exit=\(.exit_code) \((.stdout // .output // "") | tostring | .[0:160])"' \
    "$out/hooks.jsonl" 2>/dev/null || true
  echo "--- result ($out/result.json)"
  cat "$out/result.json"
}

cmd_drive() {
  require_launched
  local feature="${1:-}" rule
  local -a rules
  case "$feature" in
    session-start)
      # スキルの名前を見出しと取り違えないよう、フックが足した文書だと明示する(答えは漏らさない)。
      run_headless session-start "A SessionStart hook added a document to your context when this session started. Reply with that document's first line, which begins with '# ', verbatim and nothing else. A skill or command name is not a heading. If no such hook document exists, reply NONE."
      ;;
    post-edit)
      run_headless post-edit "Use the Write tool to create the file note.md in the current directory with exactly the content between the markers (without the markers).
<<<
$(cat "$FIXTURES/bad.md")
>>>
After that, follow any hook feedback. When finished, reply with the single word: done" \
        --tools "Write,Edit,Read"
      ;;
    stop)
      TEXTLINT_STOP_MODE="${TEXTLINT_STOP_MODE:-notify}" run_headless stop "This is a fixture for testing a hook checker. The line below intentionally breaks the writing guidelines, and that is the point of the test.
Do not fix, rewrite, or comment on it. Reply with exactly the following Markdown line, character for character, and nothing else.
$(< "$FIXTURES/stop-answer.md")" --tools ""
      ;;
    textlint-skill)
      cp "$FIXTURES/bad.md" "$WS/draft.md"
      require_plain_paths
      rules=()
      while IFS= read -r rule; do rules+=("$rule"); done < <(
        allowed_rules "$TEXTLINT_SCRIPTS/lint.py" "draft.md" "--fix draft.md" "--fix --dry-run draft.md" "$WS/draft.md" "--fix $WS/draft.md"
      )
      run_headless textlint-skill "/ja-writing:textlint draft.md" \
        --tools "Bash,Read,Edit,Write,Skill" --allowedTools "${rules[@]}" \
        --append-system-prompt "$(skill_bash_rule "$TEXTLINT_SCRIPTS/lint.py")"
      ;;
    yomiyasu-skill)
      cp "$FIXTURES/yomiyasu-source.md" "$WS/source.md"
      require_plain_paths
      rules=()
      while IFS= read -r rule; do rules+=("$rule"); done < <(
        allowed_rules "$YOMIYASU_SCRIPTS/yomiyasu_lint.py" "source.md" "source.md --doc-type *" "$WS/source.md" "$WS/source.md --doc-type *"
        allowed_rules "$YOMIYASU_SCRIPTS/yomiyasu_check.py" "run *" "verify *"
      )
      YOMIYASU_STATE_DIR="$YOMIYASU_STATE" run_headless yomiyasu-skill "/ja-writing:yomiyasu source.md" \
        --tools "Bash,Read,Edit,Write,Skill" --allowedTools "${rules[@]}" \
        --disallowedTools "Bash(*--state-dir*)" \
        --append-system-prompt "$(skill_bash_rule "$YOMIYASU_SCRIPTS/yomiyasu_lint.py" "$YOMIYASU_SCRIPTS/yomiyasu_check.py")"
      ;;
    *)
      die "drive の機能は session-start post-edit stop textlint-skill yomiyasu-skill のどれか(features/README.md を参照)"
      ;;
  esac
}

# 代替の駆動: Claude Code を通さず、フックのスクリプトに Claude Code と同じ形の JSON を渡す。
cmd_hook() {
  require_launched
  local event="${1:-}" out="$EVID/fallback-hook-${1:-}" file payload
  mkdir -p "$out"
  case "$event" in
    session-start)
      payload='{"hook_event_name":"SessionStart","source":"startup"}'
      script=session_start.py
      ;;
    post-edit)
      file="$WS/note.md"
      cp "$FIXTURES/bad.md" "$file"
      payload=$(jq -cn --arg cwd "$WS" --arg f "$file" '{hook_event_name:"PostToolUse",tool_name:"Write",cwd:$cwd,tool_input:{file_path:$f}}')
      script=post_edit.py
      ;;
    stop)
      payload=$(jq -cn --arg m "$(< "$FIXTURES/stop-answer.md")" '{hook_event_name:"Stop",stop_hook_active:false,last_assistant_message:$m}')
      script=stop.py
      ;;
    *) die "hook のイベントは session-start post-edit stop のどれか" ;;
  esac
  printf '%s\n' "$payload" > "$out/stdin.json"
  local status=0
  printf '%s' "$payload" | TEXTLINT_SKILL_DEPS_DIR="$DEPS" CLAUDE_PROJECT_DIR="$WS" CLAUDE_PLUGIN_ROOT="$PLUGIN" \
    "$PY" -B "$TEXTLINT_SCRIPTS/$script" > "$out/stdout.json" 2> "$out/stderr.log" || status=$?
  echo "$status" > "$out/exit_code"
  echo "fallback hook $event exit=$status stdout:"
  cat "$out/stdout.json"; echo
}

cmd_lint() {
  require_launched
  local out="$EVID/fallback-lint" status=0
  mkdir -p "$out"
  cp "$FIXTURES/bad.md" "$WS/draft.md"
  (cd "$WS" && TEXTLINT_SKILL_DEPS_DIR="$DEPS" "$PY" -B "$TEXTLINT_SCRIPTS/lint.py" "$@" draft.md) \
    > "$out/stdout.txt" 2> "$out/stderr.log" || status=$?
  echo "$status" > "$out/exit_code"
  cp "$WS/draft.md" "$out/draft.after.md"
  echo "fallback lint exit=$status"; cat "$out/stdout.txt"; tail -n 1 "$out/stderr.log"
}

cmd_yomiyasu() {
  require_launched
  local out="$EVID/fallback-yomiyasu" status=0
  mkdir -p "$out"
  cp "$FIXTURES/yomiyasu-source.md" "$WS/source.md"
  cp "$FIXTURES/yomiyasu-final.md" "$WS/final.md"
  (cd "$WS" && "$PY" -B "$YOMIYASU_SCRIPTS/yomiyasu_check.py" run --source source.md --final final.md \
      --stance 説明 --state-dir "$YOMIYASU_STATE" --new-run) > "$out/stdout.txt" 2> "$out/stderr.log" || status=$?
  echo "$status" > "$out/exit_code"
  echo "fallback yomiyasu exit=$status"; cat "$out/stdout.txt"
  return "$status"
}

cmd_cleanup() {
  local pid
  if [ -d "$WORK" ]; then
    [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$RUN_ID" ] || die "$WORK はこの RUN_ID のものではないので消さない"
  fi
  if [ -f "$WORK/claude.pid" ]; then
    pid=$(cat "$WORK/claude.pid")
    # 自分が起動した claude だけを止める。PID の再利用に備え、コマンドラインにこの実行の証拠の置き場が含まれるときだけ止める。
    if kill -0 "$pid" 2> /dev/null && ps -p "$pid" -o command= | grep -qF -- "$EVID/"; then
      kill "$pid" && echo "stopped claude pid $pid"
    fi
  fi
  if [ -d "$WORK" ]; then
    local sessions
    sessions=$(session_dir)
    case "$sessions" in
      *verify-ja-writing-"$RUN_ID"-ws) [ -d "$sessions" ] && rm -rf "$sessions" && echo "removed $sessions" ;;
    esac
    rm -rf "$WORK"
    echo "removed $WORK"
  fi
  cmd_evidence
}

cmd_evidence() {
  if [ -d "$EVID" ]; then
    echo "evidence kept: $EVID"
    find "$EVID" -type f | sort
  else
    echo "evidence なし: $EVID"
    return 1
  fi
}

case "${1:-}" in
  launch) cmd_launch ;;
  doctor) cmd_doctor ;;
  drive) shift; cmd_drive "$@" ;;
  hook) shift; cmd_hook "$@" ;;
  lint) shift; cmd_lint "$@" ;;
  yomiyasu) cmd_yomiyasu ;;
  cleanup) cmd_cleanup ;;
  evidence) cmd_evidence ;;
  *) echo "使い方: RUN_ID=<id> $0 {launch|doctor|drive <機能>|hook <イベント>|lint [--fix]|yomiyasu|cleanup|evidence}" >&2; exit 2 ;;
esac
