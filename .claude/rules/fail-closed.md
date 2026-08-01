---
paths:
  - '**/*'
---
# Fail-closed discipline

矛盾するフラグの組、検証不能な入力、失敗しうる外部操作（push / resolve / API 呼び出し）の
結果に対して、「黙って通す」「黙って捨てる」「黙って続行する」を禁止する。既定は
**fail-closed**: 非ゼロ exit で明示的に止まる、呼出側や人間に escalate する、少なくとも
機械可読なエラー行を emit して沈黙しない — のいずれかを必ず取る。

なぜ rule か: silent-failure は発生時点では無症状で、下流の誤判定として初めて現れる。
局所パッチでは同型が別の箇所で再発する（同一クラスの実例が 2 PR で 4 件）:

- PR #96: 矛盾するフラグ指定を silent に無視して片方だけ適用した
- PR #96: cwd を確立できない transcript を defensive に keep し、別プロジェクトの
  データ混入を許した（→ fail closed に修正）
- PR #97: resolve 失敗が gate をすり抜け、未 resolve のまま「完了」扱いになった
- PR #97: push 失敗を silent に見逃し、リモート未反映のまま後続処理が進んだ

判定に迷ったら「この分岐が黙って選ばれたとき、下流は間違いに気づけるか」を問う。
気づけないなら fail-closed にする。
