**Actionable comments posted: 1**

> [!CAUTION]
> Some comments are outside the diff and can’t be posted inline due to platform limitations.

<details>
<summary>⚠️ Outside diff range comments (3)</summary><blockquote>

<details>
<summary>src/payment/retry.ts (2)</summary><blockquote>

`88-96`: **Retry loop never backs off on 429**

The `retry` helper ignores `Retry-After`, so a rate-limited upstream is hammered.

<details>
<summary>🤖 Prompt for AI Agents (1)</summary>

```
In src/payment/retry.ts around lines 88 to 96, honor the Retry-After header.
```

</details>

---

`120`: **Swallowed error hides timeout**

`catch {}` drops the timeout error; rethrow or log it.

</blockquote></details>
<details>
<summary>README.md (1)</summary><blockquote>

`12-14`: **Setup steps reference removed script**

`./bootstrap.sh` no longer exists.

</blockquote></details>

</blockquote></details>
<details>
<summary>🧹 Nitpick comments (1)</summary><blockquote>

<details>
<summary>src/payment/client.ts (1)</summary><blockquote>

`5-5`: **Unused import**

`lodash` is imported but never used.

</blockquote></details>

</blockquote></details>

<details>
<summary>📜 Review details</summary>

**Configuration used**: CodeRabbit UI

<details>
<summary>📥 Commits (1)</summary>

Reviewing files that changed from the base of the PR and between abc123 and def456.

</details>
<details>
<summary>📒 Files selected for processing (2)</summary>

* `src/payment/client.ts` (1 hunks)
* `src/payment/handler.ts` (2 hunks)

</details>

</details>
