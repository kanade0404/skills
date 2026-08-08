---
root: true
targets: ["*"]
description: "Main-agent orchestration policy"
globs: ["**/*"]
---

# Main-Agent Orchestration Policy

main agent は自ら実行せず、意思決定・設計・全体進行の orchestration に徹する (advisor model としての助言・判断は例外)。
タスク実行は subagent に委譲し、難易度で model を使い分ける: 高難度=opus、標準=sonnet、機械的作業 (検索・一括置換・定型スクリプト)=haiku。実行系 subagent に fable は使わない。
独立した subtask は並列に dispatch し、main は俯瞰を維持して脱線・前提不足に介入する。

この方針は main agent が skill の外で自由裁量に実行する場合に適用する。個々の skill
(`commit` / `tdd` / `tidy-first` / `shipping` Phase 3 の PR push / `rulesync-sync` 等) が、
その skill 自身の手順として git 操作や機械的処理を直接行うことは対象外 — 委譲するか自ら行うかは
各 skill が自身の canonical source で明示する設計判断であり、本方針はそれを上書きしない。
