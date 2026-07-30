# retro の分析観点・設計原則の根拠

SKILL.md の観点と規律がどの先行事例・研究に接地しているかの台帳。
観点の追加・変更を検討するとき、および「なぜこの規律があるのか」を確認するときだけ読む。
調査日: 2026-07-16。外部コードの複製はしていない (分類概念・設計原則の参照のみ。出典は各節に明記)。

## 設計原則 (SKILL.md が実装しているもの)

### 1. finding は外部シグナルに接地させる — 純粋な自己反省を採用しない
外部フィードバック無しの LLM 自己修正は改善せず悪化しうる (Huang et al. 2023,
arXiv:2310.01798)。外部検証付き批評が決定的 (CRITIC, arXiv:2305.11738)。
LLM judge には self-enhancement bias がある (Zheng et al. 2023, arXiv:2306.05685) ため、
「fresh subagent だから客観的」だけでは不十分で、観測可能なシグナル
(tool エラー / 拒否 / 中断・訂正 / CI / 発火実績) への接地が必須の補完になる。

### 2. 提案は行単位 delta、全文書き換え禁止
全文改稿の反復は context collapse / brevity bias (要約のたびに暗黙知が脱落) を起こす
(ACE, arXiv:2510.04618)。ExpeL (arXiv:2308.10144) の insight 操作
(ADD/EDIT/UPVOTE/DOWNVOTE + importance カウンタ) と ACE の counter 付き bullet は
独立研究の収斂進化。剪定 (dedup・削除) は追加と別パスで行う (grow-and-refine)。

### 3. 失敗だけでなく成功からも学ぶ (成功/失敗ペア比較)
ExpeL は「同一タスクの成功/失敗ペア比較」と「成功群の共通パターン抽出」の 2 経路で
insight を抽出する。成功 trajectory からの workflow 誘導だけでも大幅改善が出る
(AWM, arXiv:2409.07429)。教訓の記述は因果形式「X すると Y になる」が転移性で優る
(CLIN, arXiv:2310.10134)。

### 4. 編集の採否は eval が裁き、悪化したら roll-back
DSPy/MIPROv2/GEPA (arXiv:2310.03714 / 2406.11695 / 2507.19457) は
「メトリクスが編集を裁く」ことで人手調整を超えた。GEPA の Pareto 選択は
「平均点だけで選ぶと特定ケース群へ過適合する」対策 — trigger evals のタグ別成績
(explicit/ambiguous/adjacent/distractor) を個別に見る規律の裏付け。
AgentOptimizer (arXiv:2402.11359) は roll-back / early-stop を明示機構として持つ。
git 管理された markdown ハーネスは revert 一発で roll-back でき相性が最良。
lever「eval-case 追加」は業界の trace 分析製品 (LangSmith / Langfuse / Braintrust) と
Husain 流 error analysis に共通する「失敗例を eval データセットへ還流する」出口に対応する。

### 5. taxonomy の確定と採否は人間、LLM は候補生成と集計まで
Husain & Shankar の error analysis (hamel.dev/blog/posts/evals-faq): open coding は人間
(tribal knowledge が LLM に無い)、axial coding のクラスタ提案のみ LLM 可。
飽和基準は「新カテゴリが 20 件連続で出なければ停止」。
AutoManual (arXiv:2405.16247) も rule 管理と人間可読化を別役割に分離。
retro の「fresh subagent 解析 → 人間承認 → skill-builder 実装」の 3 段分離は
ACE の Generator/Reflector/Curator 構成と同型で、この文献群と整合する。
自動化を進める場合は承認ゲートの**後ろ** (編集適用・eval 実行・集計) からにし、
finding 採否の自動化は最後に回す。

## 定量観点の出典 (retro_scan.py が実装しているもの)

- **tool エラー taxonomy**: コミュニティで確立された Claude Code エラー分類
  (sniffly / Chip Huyen, github.com/chiphuyen/sniffly) に倣う。分類の正規表現は
  本 repo の corpus に対して独自に書いたもので、コードの複製ではない。
- **介入率 (interruption rate)・steps per prompt**: 「interruption rate は新しい
  build time」— 実測例: 介入率 ~24.5%、~10 ステップごとに人間介入 (Chip Huyen の
  自己データ。基準率ではない)。ハーネス改善の効果測定 KPI として最適。
- **cache 効率・compaction**: Claude Code 公式 OTel の `compaction` イベント /
  cache token 4 分類に対応 (code.claude.com/docs/en/monitoring-usage)。
- **transcript 形式は公式に internal/unstable 宣言済み**
  (code.claude.com/docs/en/sessions.md)。retro_scan.py は ignore_errors +
  文字列マーカーの防御的検出で読む。長期的に安定な観測面は hooks
  (PostToolUse/PostToolUseFailure 等) と OTel — スキーマ破壊で スクリプトが
  沈黙し始めたらそちらへの移行を検討する。

## 取り込みを見送った設計 (再検討の材料)

- **常時 hook でのシグナル自動収集** (claude-reflect / Claudeception / ECC v2 系):
  netresearch/retro-skill が先行方式の実測失敗 (1011 pending / 0 approved の
  write-only noise、同一問題 ~35 重複) を設計根拠として公開しており、
  事後一括解析 + 承認ゲートの現行設計を維持する。
- **outcome mode** (netresearch/retro-skill, MIT+CC-BY-SA-4.0): セッション後の現実
  (revert された commit / reject された PR / 後続 CI 失敗) と findings を照合する軸。
  価値は高いが PR 決着データの取得設計が必要なため未実装。次の拡張候補第 1 位。
- **insight の confidence カウンタ運用** (ExpeL / ACE / ECC v2 の instinct):
  提案の delta 化までを実装し、カウンタ管理は未実装。findings の再出現を
  retro が横断スコープで数えられるようになった時点で導入を再検討する。
- **skill 自動生成 + curation loop** (UniM0cha/claude-self-improving-skills):
  usage telemetry / stale archive / rollback 付き curation は工学的に最良だが、
  自動書き込みが本スキルの Iron Law (提案のみ) と衝突するため設計参考に留める。

## 主要出典
Reflexion arXiv:2303.11366 / ExpeL arXiv:2308.10144 / CRITIC arXiv:2305.11738 /
Huang et al. arXiv:2310.01798 / DSPy arXiv:2310.03714 / MIPROv2 arXiv:2406.11695 /
GEPA arXiv:2507.19457 / ACE arXiv:2510.04618 / AgentOptimizer arXiv:2402.11359 /
AWM arXiv:2409.07429 / AutoManual arXiv:2405.16247 / CLIN arXiv:2310.10134 /
LLM-as-judge arXiv:2306.05685 / Husain evals-faq hamel.dev/blog/posts/evals-faq /
sniffly github.com/chiphuyen/sniffly / netresearch/retro-skill
github.com/netresearch/retro-skill / mizchi retrospective-codify
github.com/mizchi/skills (重複チェック工程の出自) /
Claude Code monitoring code.claude.com/docs/en/monitoring-usage
