# レビューの規則と強制する仕組み

ここには、レビューの指摘から昇格させた規則と、それを強制する仕組みの対応表を載せる。
表は `harness/review-rules.json` から `review_learning.py sync` が書く。印の間は手で直さない。
昇格の手順は `plugins/productivity/skills/review-learning/SKILL.md` にある。

<!-- BEGIN harness/review-rules.json (review_learning.py sync が書く) -->
| 規則 | 種類 | 強制する仕組み | 強制する場所 | 証拠 |
| --- | --- | --- | --- | --- |
| `prefix-match-identity` | `prefix-match-identity` | ast-grep | `harness/ast-grep/review-rules/prefix-match-identity.yml` | #190, `4d7fe4c`, `050c3e7`, `local:security-guidance:beb628cf761515d6` |
| `unverified-comment-author` | `unverified-comment-author` | path_instructions | `.coderabbit.yaml` | #190, `4d7fe4c`, `050c3e7`, `c570b18`, `local:security-guidance:a8a14651274151f4`, `coderabbit:33569afdf8224158cd71ff17` |
<!-- END harness/review-rules.json -->
