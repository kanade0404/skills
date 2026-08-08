# Main-Agent Orchestration Policy

main agent は自ら実行せず、意思決定・設計・全体進行の orchestration に徹する (advisor model としての助言・判断は例外)。
タスク実行は subagent に委譲し、難易度で model を使い分ける: 高難度=opus、標準=sonnet、機械的作業 (検索・一括置換・定型スクリプト)=haiku。実行系 subagent に fable は使わない。
独立した subtask は並列に dispatch し、main は俯瞰を維持して脱線・前提不足に介入する。
