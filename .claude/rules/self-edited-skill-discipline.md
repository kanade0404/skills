---
paths:
  - '**/*'
---
# Self-edited skill discipline

自分が現セッションで編集した `SKILL.md` を、同セッション内で実行に移す
(その skill のワークフローを自分で駆動する / dogfood する) 前に、確定版を
必ず一度 Read する。可能なら Skill ツールで正式に起動し、記憶や推測で
代行しない。

なぜ: 実測で、編集直後の skill を「記憶している内容」で 173 ターン実行し、
Skill 起動の traceability が欠落した事例がある。編集内容の記憶は、
その後の subagent による追加編集や自分自身の当初の意図とズレていく —
セッション内で skill ファイルが変化し続ける以上、最後に読んだ版が
最新版である保証は無い。

適用場面: skill を編集した直後にその skill のワークフローへ自分で移る
構成すべて。例:

- `skill-builder` で SKILL.md を編集した直後の検証・ドッグフード
- `shipping` / `ci-self-heal` のようなオーケストレータ skill 自身の
  SKILL.md を編集した直後に、そのオーケストレーションを自分で駆動する場合
