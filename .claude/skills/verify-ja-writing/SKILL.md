---
name: verify-ja-writing
description: ja-writing プラグイン（Claude Code のフック 3 つと textlint・yomiyasu スキル）を、利用者と同じ経路で動かして証拠を残す検証スキル。フックやスキルの変更を実機で確かめるとき、「プラグインが動くか確かめて」と頼まれたときに使う。ヘッドレスの claude -p にこのチェックアウトのプラグインを読ませ、隔離した作業ディレクトリで駆動する。
---

# verify-ja-writing

このリポジトリの主な製品は `plugins/ja-writing` のプラグインだ。利用者は Claude Code にプラグインを入れ、日本語の Markdown を書かせる。
するとフックが動き、セッションの開始で規約を渡し、編集のあとで textlint をかけ、ターンの終わりに答えを検査する。
このスキルは、その経路をヘッドレスの Claude Code で再現し、フックの出力と結果のファイルを証拠として残す。

`plugins/productivity` の handoff は文章だけのスキルで、実行するコードを持たないので対象にしない。
機能ごとの手順は[features/README.md](features/README.md)にまとめている。

## 全体の決まり

操作はすべて補助スクリプト `.claude/skills/verify-ja-writing/scripts/verify.sh` から実行する。
コマンドは、リポジトリの最上位をカレントディレクトリとする前提で書いている。

- `claude` を直接打たない。利用者の対話シェルの `claude` は、システムプロンプトを足す alias になっている。スクリプトは `/bin/sh` から実体を探す。
- `python3` も対話シェルでは mise の shim に化けることがある。フックは `/bin/sh` 経由で動くので、スクリプトも同じ方法で `python3` を探す。
- ヘッドレスの claude の Bash ツールは、利用者のシェルの設定を読む。このマシンでは `python3` が mise の shim になり、`ls` は別のコマンドに置き換わる。スキルの drive では、モデルが `/usr/bin/python3` で実行し直す。
- 環境変数 `RUN_ID` を必ず決める。作業ディレクトリと証拠の置き場は `RUN_ID` ごとに分かれるので、並行して動かしても混ざらない。
- 作業ディレクトリは `${VERIFY_WORK_ROOT:-$TMPDIR}/verify-ja-writing/$RUN_ID/` に作る。セッション専用の scratchpad があれば `VERIFY_WORK_ROOT` に渡す。
- 証拠は `.verify-evidence/ja-writing/$RUN_ID/`（リポジトリの最上位）に残る。`.gitignore` 済みで、cleanup でも消えない。
- 利用者の `~/.claude/settings.json` や入れているプラグインには触れない。ヘッドレスの実行は `--setting-sources ""` で利用者とプロジェクトの設定を読まない。
- このリポジトリの作業ツリーでは `claude -p` を動かさない。`.claude/settings.json` が別のプラグインを有効にしているからである。

| 変数 | 既定 | 使い道 |
| --- | --- | --- |
| `RUN_ID` | なし（必須） | 実行の名前。英数字と `.` `_` `-` だけ |
| `VERIFY_WORK_ROOT` | `$TMPDIR` | 作業ディレクトリを作る場所 |
| `VERIFY_MODEL` | `haiku` | ヘッドレスの実行に使うモデル |
| `VERIFY_BUDGET_USD` | `0.30` | 1 回の実行で使える金額の上限（`--max-budget-usd`） |
| `TEXTLINT_HOOK_MODE`・`TEXTLINT_STOP_MODE` | プラグインの既定 | フックのモードを切り替える。そのまま claude に渡る |

## Launch

常駐するサーバーはない。launch は作業ディレクトリを作り、textlint の依存を隔離した場所に入れて、駆動できる状態にする。

```bash
export RUN_ID=$(date +%Y%m%d-%H%M%S)
.claude/skills/verify-ja-writing/scripts/verify.sh launch
```

依存は SessionStart フックと同じ `session_start.py` で入れる。入れ先は `TEXTLINT_SKILL_DEPS_DIR` で作業ディレクトリの `deps/` に向けるので、利用者の `~/.cache/ja-writing-textlint/` は使わない。
初回は `bun install` が走り、数十秒かかることがある。
`READY: .../deps/node_modules/@textlint/kernel/package.json` の行が出れば準備ができている。
この行が出ずに `NG:` で終わったら、`.verify-evidence/ja-writing/$RUN_ID/launch.session_start.stderr` に bun の誤りが残っている。

作業ディレクトリには `ws/`（claude を起動する空のワークスペース）、`deps/`、`yomiyasu-state/` ができる。
`ws/` には `.claude/` と `.textlintrc` を置かないので、検査には同梱の `config/textlintrc.json` が使われる。
片付けは Cleanup の節の 1 コマンドで済む。

## Doctor

おかしいと感じたら、まず次の読み取りだけのコマンドで状態を確かめる。

```bash
.claude/skills/verify-ja-writing/scripts/verify.sh doctor
```

確かめる項目は次の 8 つで、すべて `OK:` なら終了コード 0 になる。

- `/bin/sh` から見た `python3` が 3.9 以上である。
- `bun` が 1.3 以上である。
- `claude auth status` がログイン済みを返す。
- `claude plugin validate plugins/ja-writing` が通る。アイコンがない警告は出るが、結果は通過になる。
- この `RUN_ID` の作業ディレクトリが launch 済みである。
- 隔離した `deps/` に `@textlint/kernel` が入っている。
- `ws/.claude/` がない。
- 証拠の置き場が git に無視されている。

先頭には、リポジトリの HEAD とブランチ、使う `python3`・`bun`・`claude` のパスと版が出る。別のチェックアウトを駆動していないかをここで確かめる。

## Drive

利用者の経路は、ヘッドレスの Claude Code に機能ごとの依頼を 1 回送る方法で再現する。

```bash
.claude/skills/verify-ja-writing/scripts/verify.sh drive post-edit
```

機能の名前は `session-start`、`post-edit`、`stop`、`textlint-skill`、`yomiyasu-skill` の 5 つで、中身は `features/` の各ファイルにある。
スクリプトは `ws/` に移ってから、次の引数で claude を起動する。

- `-p "<依頼>"` で 1 回のターンを走らせる。依頼の本文は証拠の `prompt.txt` に残る。
- `--plugin-dir plugins/ja-writing` で、インストールせずにこのチェックアウトのプラグインを読ませる。フックの `${CLAUDE_PLUGIN_ROOT}` もここを指す。
- `--setting-sources ""` と `--strict-mcp-config` で、利用者の設定・プラグイン・MCP サーバーを読ませない。
- `--model haiku` と `--max-budget-usd 0.30` で費用を抑える。1 回の post-edit は 0.002 ドル前後だった。
- `--permission-mode acceptEdits --permission-prompts none` で、確認を待たずに編集させ、それ以外の許可が要る操作は拒否させる。
- `--output-format stream-json --verbose --include-hook-events` で、フックの開始と結果を記録に含める。
- `--debug-file <証拠>/debug.log` で、フックの実行のログを残す。

`textlint-skill` と `yomiyasu-skill` では、Bash を `python3` で始まる単独のコマンドだけに許す。承認する人がいないので、その形を `--append-system-prompt` でモデルに伝える。
`yomiyasu-skill` は haiku では手順を守りきれないので、`VERIFY_MODEL=sonnet VERIFY_BUDGET_USD=1.00` を付ける。

`--bare` は使わない。フックを飛ばすうえ、認証が `ANTHROPIC_API_KEY` だけになり、claude.ai のログインでは動かない。

### 代替の駆動

ヘッドレスの claude が使えないとき（未ログイン、ネットワークなし、費用をかけられない）は、次の代替でフックとスキルのコードだけを確かめる。
これは Claude Code がフックを呼ぶ配線を通らないので、利用者の経路の証明にはならない。報告では必ず「代替の駆動」と書く。

```bash
.claude/skills/verify-ja-writing/scripts/verify.sh hook post-edit
.claude/skills/verify-ja-writing/scripts/verify.sh hook session-start
.claude/skills/verify-ja-writing/scripts/verify.sh hook stop
.claude/skills/verify-ja-writing/scripts/verify.sh lint
.claude/skills/verify-ja-writing/scripts/verify.sh lint --fix
.claude/skills/verify-ja-writing/scripts/verify.sh yomiyasu
```

`hook` は Claude Code と同じ形の JSON をフックのスクリプトの標準入力に渡す。`lint` は textlint スキルの `lint.py` を `ws/draft.md` にかける。
`yomiyasu` は `yomiyasu_check.py run` で原文と最終稿を比べる。記録は `yomiyasu-state/` に書き、リポジトリに `.yomiyasu/` を作らない。

## Evidence

証拠は `.verify-evidence/ja-writing/$RUN_ID/<機能>/` に入る。ヘッドレスの実行で残るファイルは次のとおり。

| ファイル | 中身 |
| --- | --- |
| `prompt.txt` | claude に送った依頼 |
| `transcript.jsonl` | stream-json の全記録。一次の証拠 |
| `hooks.jsonl` | 記録からフックの開始と結果だけを抜いたもの |
| `init.json` | 読み込んだプラグインとモデル。`ja-writing@inline` と組み込みだけなら隔離できている |
| `result.json` | ターンの結果、ターン数、費用 |
| `files/` | 実行後の `ws/` の中身。編集の結果を示す |
| `session/` | Claude Code が `~/.claude/projects/` に書いたセッションの記録の写し |
| `debug.log`・`stderr.log`・`exit_code` | claude のログと終了コード |

代替の駆動は `fallback-hook-<イベント>/`、`fallback-lint/`、`fallback-yomiyasu/` に、標準入力、標準出力、終了コードを残す。

証明として認めるには、次の 4 つの条件をすべて満たす。

- 利用者の経路を通す。フックの結果は `transcript.jsonl` の `hook_response` で示し、スクリプトの直接実行で代えない。
- 操作と結果の両方を残す。編集のフックなら、指摘を返した `hook_response` と、その後の `files/` のファイルをそろえる。
- 副作用を確かめる。ファイルが書き換わったか、セッションの記録がどこに書かれたかを見る。
- `init.json` で、ja-writing 以外の利用者のプラグインが読まれていないことを確かめる。

フックの結果は次のコマンドで読める。

```bash
jq -c 'select(.subtype == "hook_response") | {hook_name, exit_code, stdout}' .verify-evidence/ja-writing/$RUN_ID/post-edit/transcript.jsonl
```

`stdout` は JSON の文字列なので、中身は `.stdout | fromjson` で開く。
スラッシュコマンドで呼んだスキルは `Skill` の `tool_use` にならない。スキルが読まれた証拠は `session/*.jsonl` の `<command-name>` にある。

## Cleanup

作業が終わったら、成否にかかわらず次の 1 コマンドで片付ける。失敗した試行のあとも毎回実行する。

```bash
.claude/skills/verify-ja-writing/scripts/verify.sh cleanup
```

cleanup は 3 つを片付ける。

- 駆動の途中で止まった claude があれば、記録した PID のものだけを止める。プロセスの名前では止めない。
- この実行が `~/.claude/projects/` に作ったセッションのフォルダを消す。名前が `verify-ja-writing-<RUN_ID>-ws` で終わるものに限る。
- 作業ディレクトリ `verify-ja-writing/$RUN_ID/` を消す。目印のファイルの `RUN_ID` が合わなければ消さずに止まる。

証拠の置き場は消さない。cleanup は最後に `.verify-evidence/ja-writing/$RUN_ID/` のファイルを一覧する。あとから確かめるには `verify.sh evidence` を実行する。
証拠が要らなくなったら、利用者に断ってから `.verify-evidence/ja-writing/<RUN_ID>/` を手で消す。

## Helpers

補助スクリプトは `scripts/verify.sh` の 1 つだけで、実行権限が付いている。どのサブコマンドも `RUN_ID` を求める。

| サブコマンド | 働き |
| --- | --- |
| `launch` | 作業ディレクトリを作り、textlint の依存を入れる |
| `doctor` | 読み取りだけの確認。NG があれば終了コード 1 |
| `drive <機能>` | ヘッドレスの claude で機能を 1 回駆動する |
| `hook <イベント>` | 代替。フックのスクリプトに JSON を直接渡す |
| `lint [--fix]` | 代替。`lint.py` を見本のファイルにかける |
| `yomiyasu` | 代替。`yomiyasu_check.py run` を見本の原文と最終稿にかける |
| `cleanup` | 起動したものと作業ディレクトリを片付け、証拠を一覧する |
| `evidence` | 証拠の置き場を一覧する。なければ終了コード 1 |

見本のファイルは `fixtures/` にある。わざと指摘が出る文にしているので、直さない。

- `bad.md` は `することができる` を含む。ファイル用の設定で `ja-no-redundant-expression` が 1 件出る。
- `stop-answer.md` は太字のラベルとコロンで始まる箇条書きである。答え用の `chat.json` で `no-ai-list-formatting` が出る。
- `yomiyasu-source.md` と `yomiyasu-final.md` は、yomiyasu の差分の検査に渡す原文と最終稿である。

## 確かめた実行

2026-11-01 に 5 つの機能をすべてヘッドレスで駆動した。claude は 2.1.316 で、RUN_ID ごとに launch、doctor、drive、cleanup を通した。
どの cleanup のあとも証拠は残り、作業ディレクトリと `~/.claude/projects/` のセッションのフォルダは消えた。

| RUN_ID | 機能とモード | 結果 |
| --- | --- | --- |
| `m2-session-start` | session-start | `SessionStart:startup` に規約が入り、`result` が見出しと一致 |
| `m3-session-start-off` | `TEXTLINT_SESSION_CONTEXT=false` | `stdout` が空、`result` は `NONE` |
| `m4-post-edit` | post-edit（block） | 指摘 1 件、Edit で直り、2 回目は空 |
| `m5-post-edit-warn` | `TEXTLINT_HOOK_MODE=warn` | `additionalContext` だけで `decision` なし |
| `m6-stop` | stop（notify） | `systemMessage` に指摘 1 件 |
| `m7-stop-block` | `TEXTLINT_STOP_MODE=block` | Stop が 2 回、1 回目が block、2 回目が空 |
| `m12-textlint-skill` | textlint-skill（haiku） | `lint.py` で 1 件から 0 件に直した |
| `m11-yomiyasu-skill` | yomiyasu-skill（sonnet） | `run` と `verify` が `executed`、原文は変わらない |

費用は haiku の実行が 1 回 0.001〜0.006 ドル、sonnet の yomiyasu が 0.25 ドルだった。代替の `lint` と `lint --fix` も RUN_ID `m9-fallback-lint` で通し、終了コードは 1 と 0 だった。

確かめていない入口は、セッションの再開、`/clear`、compact、MultiEdit、`apply_patch`、`bun install` の失敗の 6 つだ。yomiyasu の原文の lint は、モデルが実行しなかったので確かめていない。
プラグインが変わって地図が古くなったら、`/pstack:maintain-verification-skill` で直す。
