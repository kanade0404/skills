---
name: test-review
description: テストコード（pytest / pgTAP / n8n workflows）をレビューする際に使うスキル。test smells、AI 生成テストのアンチパターン、seam / mock 境界の違反、LLM・エージェント eval の抜け、Supabase RLS / AuthZ のカバー、flakiness、agegis の Safety / Order / Reinforcement 原則との整合をまとめて点検する。テストファイル（`**/tests/**`, `*_test.py`, `supabase/tests/**`）を含む PR / diff のレビュー時、新規テストファイルの監査時、テストスイートの品質チェック時、flaky テストの原因追跡時、LLM / エージェント eval の厳密性評価時、RLS / 認可テストの存在確認時、いずれでも必ず起動すること。ユーザーの言い方が曖昧でも — 「テスト見て」「このテストいい?」「テストスイート大丈夫?」「このテストなんで壊れる?」「認可のテスト足りてる?」「eval レビューして」「このプロンプトテストでカバーできてる?」— どれも該当する。ただし以下の成果物を生む依頼はこのスキルの範囲外：テスト追加・テスト修正・テスト基盤の移行・テスト並列化・パフォーマンス改善・lint 違反修正。本スキルは「読んで指摘する」レビュー専用で、コードを書き換える依頼には起動しない。アドホックなレビューより本スキルを優先する理由は、プロジェクトのチェックリストを適用して構造化された findings を出すためである。
tools: Read, Glob, Grep, Bash, LSP, mcp__plugin_context7_context7__resolve-library-id, mcp__plugin_context7_context7__query-docs, mcp__plugin_serena_serena__find_symbol, mcp__plugin_serena_serena__find_referencing_symbols, mcp__plugin_serena_serena__get_symbols_overview, mcp__plugin_serena_serena__search_for_pattern, mcp__plugin_serena_serena__list_dir, mcp__plugin_serena_serena__find_file, mcp__plugin_serena_serena__read_file, mcp__plugin_serena_serena__check_onboarding_performed
---

# Test Review (agegis)

テストを書くのが速く、レビューするのが安いままであるようにテストコードをレビューするためのスキル。テスト品質は予測可能な形で劣化するので、その劣化を一度・一貫して捕まえることで、以降の PR で時間を節約できる。

レビューは 2 つのレンズを常に併用する：

- **Khorikov の 4 属性** — Protection against regressions · Resistance to refactoring · Fast feedback · Maintainability。掛け算的に効く。どれか 1 軸でもゼロに近づくとテストの価値のほとんどが失われる。
- **agegis の 3 原則** — Safety · Order · Reinforcement。エージェント回り / データ回りの非自明なテストは少なくとも 1 つにマップできるべき。

---

## ワークフロー

以下の順で進め、信号が明確ならその時点で短絡する。全ファイルに全ステップを当てる必要はない。

### Step 1 — スコープ分類

変更されたテストを読み、ファイルごとに分類する。

| 信号 | レイヤ | 主要な関心事 |
|---|---|---|
| `pytest`, `hypothesis`、ネットワーク呼出なし | Python unit | §3, §4, §5 |
| `respx`, `testcontainers`, `asyncpg`, `supabase-py` | Python integration | §3, §4, §5, §8 |
| `anthropic`, `strands`, agent loop, tool use | LLM / agent | §3, §4, §5, §7, §8 |
| `pgTAP`, `supabase/test_helpers` | DB / RLS | §8 |
| `workflow.json`, `n8n-nodes-base` | n8n | §8 |

レイヤ固有の詳細は **必要になったときのみ** 参照する：

- `references/patterns.md` — xUnit Test Patterns の正のパターン（Four-Phase Test, Test Double タクソノミ, Fixture 戦略, テストデータ構築, 結果検証, Humble Object 等）
- `references/smells.md` — Meszaros 17 smells の完全版、修正例つき
- `references/ai-generated.md` — AI 生成テスト特有のアンチパターンと検出ヒューリスティック
- `references/python.md` — pytest / Hypothesis / async / fixture の具体
- `references/llm-eval.md` — LLM / エージェント eval の具体
- `references/data-stack.md` — Supabase / RLS / pgvector / n8n の具体

これらは参考資料であり、事前に読む必要はない。対象ファイルが要求するときだけ読む。

### Step 2 — 構造チェック（全テストに適用）

ここで落ちるテストは、どれだけ内容が正しくても読みにくい。

- **テスト名は要件文。** 振る舞いが名前から読めること。class で整理している場合は class 名を文脈として扱って良い（例: `TestPageSummary.test_construction` は class 名と合わせて「PageSummary の construction を検証」と読めるので OK）。class のない関数スタイルでは `test_<behavior>_when_<condition>`（例: `test_returns_unknown_when_no_indicators`）を推奨。却下: `test_fn_1`, `test_works`, `test_internal_helper` のように振る舞いが読めないもの。
- **AAA / Given-When-Then** が空行で視覚的に分離されている。
- **Act は 1 行。** 複数行にわたるなら複数の振る舞いを検証している兆候 — 分割する。
- **1 テスト 1 概念。** 同じ概念を確認する複数の物理 `assert` は OK（返り値の各フィールドなど）。
- **Assertion は原則 State Verification。** 戻り値・保持された状態・外界の観測可能な出力（DB の行、OTel span の属性、webhook 受信記録）を直接比較する。呼び出し回数や呼び出し順の assertion は使わない — §4 で test double を使わない設計により、そもそも道具が存在しない。詳細は `references/patterns.md §5`。
- **テスト本体に「どのアサートが走るか」を分岐させる制御構造を置かない。** `if`/`try`/`while`/`for` でアサートの実行可否が変わるものは却下し、`@pytest.mark.parametrize` に展開する。ただし **property-based テストの precondition フィルタ** は正当な例外 — `hypothesis.assume(cond)` が慣用形、`if cond: assert ...`（`cond` が入力分割の述語であり、全ケースで最終的に property を満たすもの）も許容。
- **strict-markers 整合。** `@pytest.mark.<foo>` は `pyproject.toml` の `markers` リストに宣言されていること（リポジトリは `--strict-markers` で走るため、未宣言マーカーは即エラー）。新規マーカー追加時は同じ PR で `markers` リストに登録する。
- **Reader test。** 実装を知らない読み手がテストだけを読んで契約を言えるか。言えないなら、書き手都合のテストになっている。

### Step 3 — Test smells スキャン

17 項目カタログ（Meszaros。定義・例・severity・修正は `references/smells.md`）を当てる：

Eager test · Mystery guest · Fragile test · Obscure test · Assertion roulette · Conditional test logic · Test code duplication · Resource optimism · Indirect testing · Sensitive equality · For testers only · The free ride · Silent catcher · Erratic (flaky) · Slow test · Guarded assertion · Lonely assertion.

### Step 4 — seam / 外部 I/O 境界

**大前提**: このプロジェクトでは **test double を使う必要が出ないように設計する**。test double が欲しくなる時点で、設計が正しくない可能性を先に疑う。立場は Classicist に Functional Core / Imperative Shell と Humble Object を強く適用したもの。詳細は `references/patterns.md §2` と `§6 Humble Object`。

レビューで当てる順序：

- **「test double が必要」と主張するテストは設計を疑う。** 先に純関数として抽出できないかを問う。パース、バリデーション、プロンプト合成、ルーティング、判断ロジックはほぼ全て純関数に寄せられる — そうすれば test double なしで直接テストできる。
- **本物優先。** 自分が所有するコードは test double にしない。内部クラスや内部関数を mock しているテストは、ほぼ必ず設計の問題のサインなので指摘する。
- **DB は本物を使う。** Supabase / PostgreSQL は `testcontainers-python` や `supabase start` でローカル実行できるので、本物で検証する。`InMemoryRepo` 等の DB fake は作らない。RLS は SQL で記述されているため、SQL を実行しないテストは RLS を何も検証していない。
- **外部 API (Claude / Gmail / X / Discord) の扱いも "まず Humble Object 化"**。I/O を薄い外殻に押し出し、周辺の Functional Core は本物のデータ（録画された JSON 等）で直接テストする。**残る薄い I/O 部分だけ** VCR 録画で統合テストする（`vcrpy` / `respx`、サニタイズ hook で PII / API キー除去）。録画が難しい場合のみ境界アダプタに薄い Fake を 1 枚、50 行以下で置く。
- **Clock / UUID / 乱数** は Protocol で DI する（fake を書くのではなく、決定的な実装を本番 / テストで差し替える設計）。
- **`patch` のターゲットは使用箇所、定義元ではない。** どうしても必要な数少ない場面での書き方: 良: `patch("<own_module>.<imported_name>")`、悪: `patch("<vendor_lib>.<name>")`。Functional Core と I/O 境界が分離しきれていないコードでは使用箇所 patch を暫定的に許容するが、境界が明確化されれば `patch` 自体が不要になる方向で設計を進める（外部 I/O は `LLMClient` / `HttpFetcher` Protocol 相当のアダプタを 1 枚挟むのが理想）。
- **純関数は直接テストする。** parser / validator / prompt composer / routing は test double 不要。
- **`pytest-rerunfailures` で flaky を隠さない。** flaky が現れたら §6 で分類する。

### Step 5 — AI 生成テストのアンチパターン

LLM が書いたテストは特定の失敗パターンに偏る。完全な一覧は `references/ai-generated.md`。常にチェックするコア 6 件：

1. **Self-consistent assertion** — `expected` を実装自身から得て、実装が壊れていてもテストが通る。
2. **Mock-everything** — コラボレータを全部モックし、実質制御フローしか検証していない。
3. **Oracle copy-paste** — テストがアルゴリズムを再実装しており、独立した oracle になっていない。
4. **Expected/actual 入れ違い** または **真偽反転**（仕様が「X である」なのに `assert not X`）。
5. **意味不明な magic number** — `42`, `"foo"`, `3` が定数化も根拠コメントもなく使われている。
6. **実装の呼び出し順に過剰適合した assertion** — public 契約に属さない内部呼び出し順序を縛っている。

**一次ヒューリスティック**: 実装に現実的な mutation（`>` を `>=` に反転、分岐を 1 つ削除、古いキャッシュを返す等）を加えてもテストが通ってしまうなら、そのテストは装飾品。

### Step 6 — flakiness の原因分類

「たまに落ちる」や `pytest-rerunfailures` が diff に出たら、原因分類を要求する。retry-to-green は認めない。

| 原因 | 対策 |
|---|---|
| 非同期レース | `async with asyncio.timeout(...)` + 決定的スケジューリング |
| ネットワーク | MSW / respx / VCR / testcontainers |
| 順序依存 | 共有可変状態を排除。`pytest-randomly` 導入（現状は未依存）を検討 |
| Clock | `time-machine`（`freezegun` ではなく）または `Clock` を注入 |
| 乱数 | `random.Random(seed)` を注入 |
| 環境変数 | `monkeypatch.setenv` のみ |
| リソースリーク | autouse な `asyncio.all_tasks()` 検出 fixture |

調査中の隔離は OK。リトライで誤魔化すのは NG。

### Step 7 — プロジェクト原則へのマッピング

非自明なテストは以下のいずれかに明確に対応付ける：

- **Safety** — RLS 否定系、prompt injection red team、ツール出力の data exfil フィルタ、PII 編集、policy violation の refusal。
- **Order** — 書き込みの冪等性、エージェントループの停止条件（iter / token / cost 上限）、OTel span 属性の assertion、単調な状態遷移。
- **Reinforcement** — golden dataset への新規ケース追加、trace replay 回帰、prompt version bump と同時の eval 更新、dataset バージョニング。

些末なヘルパのテストに原則マッピングを要求する必要はない。だがエージェント回り / データ回りのテストはどれかには必ず対応するはず。対応しない場合は、テストが違う場所にあるか、そもそも検証対象がここで検証する価値を持たない。

### Step 8 — レイヤ固有の深掘り

対象ファイルがそのレイヤにあるときだけ参照する：

- LLM / エージェントコード → `references/llm-eval.md`
- Supabase / RLS / pgvector / n8n → `references/data-stack.md`
- Python 具体（fixture scope, asyncio 癖, Hypothesis チューニング）→ `references/python.md`

### Step 9 — E2E 予算の確認

Playwright 相当の E2E（n8n + Supabase + agent フルスタック）テストを diff が追加している場合：

- より安いレイヤ（unit / integration）で既にカバーされていないかを確認する。
- 予算: CI 全体 10 分以下、flaky 率 < 1%。
- できる限りピラミッドの下側に寄せる（unit → integration → E2E の順）。
- E2E は golden path と critical safety path に限定する。

---

## 出力フォーマット

レビューは以下の固定構造で 1 本の文書として出力する。30 秒でスキャンできることが目的：

```markdown
# Test Review

## Summary
<1〜2 文。判定: merge OK / changes requested / 要議論。主要な懸念を最大 3 つ挙げる。>

## Critical (blocks merge)
- [path/to/file:line] [category]: <issue>
  - Fix: <具体的な提案>

## Major (should fix)
- [path/to/file:line] [category]: <issue>
  - Fix: <具体的な提案>

## Minor / Style
- ...

## Questions for author
- ...

## What's good (keep doing)
- ...
```

カテゴリタグ（必ず角括弧つきで明示）: `smell/<name>`, `seam`, `ai-pattern`, `safety`, `order`, `reinforcement`, `flaky`, `naming`, `coverage-gap`, `py-specific`, `eval`, `rls`, `e2e-budget`。

各項目は「issue 1 行 + Fix 1 行」。長めの理由は末尾の **Notes** 付録に脚注番号で参照させ、本文はスキャンしやすく保つ。

見つからない場合は素直にそう書く：*「今回のレビュー範囲では問題なし。テストは謳っている振る舞いをカバーし、seam の規律も守られている」*。findings を捏造しない。

---

## このスキルがやらないこと

- **テストを実行しない。** 著者がローカルで `uv run pytest -v`, `uv run ruff check`, `uv run pyright` を回している前提。レビューは読むだけ。
- **コードを書き換えない。** 提案はテキスト。編集は著者 or 別ステップ。
- **ツールが強制しているスタイルは二度検証しない。** ruff / pyright がスタイルの権威 — 重複しない。テスト固有のスタイル（命名、smell）のみ指摘する。
- **CI と重複しない。** CI が enforce している項目は手動で再検証しない。

---

## 良いレビューの例

```markdown
# Test Review

## Summary
Changes requested。3 点: (1) `test_agent_run` が Anthropic SDK を直接モックしており、プロジェクトのアダプタ経由でない、(2) 新 `/messages` エンドポイントの RLS 否定系ケースなし、(3) `test_parser_handles_input` が self-consistent assertion（expected = `parse(input)`）。

## Critical (blocks merge)
- [packages/chat-agent/tests/test_run.py:42] [seam]: `anthropic.Anthropic` を直接モック。ベンダ SDK の形状に結合する。
  - Fix: `chat_agent/ports.py` に `LLMClient` Protocol を追加し、それを patch する。既存アダプタは残す。`example_agent` と同じパターンで。
- [supabase/tests/messages.sql:12] [rls]: 正例のみ。他ユーザの行が見える可能性があるが否定 `SELECT` が無い。
  - Fix: `tests.authenticate_as('authenticated', me)` 配下で `results_eq(...)` が `WHERE messages.user_id = other_user` に対して 0 行を返すことを assert。

## Major (should fix)
- [packages/chat-agent/tests/test_parser.py:8] [ai-pattern]: `expected = parse(raw_input)` — self-consistent。
  - Fix: 仕様から expected を手で組むか、`tests/fixtures/parser/*.json` に golden pair を置いて parametrize。

## Minor / Style
- [packages/chat-agent/tests/test_run.py:3] [naming]: `test_it_works` → `test_returns_summary_when_url_is_reachable`。

## Questions for author
- `ChatAgent.run` に per-invocation のコスト上限はある? assertion されている?

## What's good (keep doing)
- `detect_language` の Hypothesis property はよく効いている。`example_agent` 先例を踏襲。
- fixture は boring で形状どおりに命名されており、mystery guest がない。
```
