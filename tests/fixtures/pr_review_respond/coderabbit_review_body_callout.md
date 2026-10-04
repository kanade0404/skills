**Actionable comments posted: 2**

> [!CAUTION]
> Some comments are outside the diff and can’t be posted inline due to GitHub limitations.
> 
> **⚠️ Outside diff range comments (2)**
> 
> <details>
> <summary><em>🟡 Minor</em> · Always lay out the requested page before export. · <code>headless.ts:23-26</code></summary><blockquote>
> 
> `packages/cli/src/headless.ts:23-26`
> _🎯 Functional Correctness_ | _🟡 Minor_ | _⚡ Quick win_
> 
> `populateDocumentPage` skips `computeAllLayouts` when the page has no pending lazy population.
> 
> <!-- cr-comment:v1:aaaa -->
> 
> </blockquote></details>
> <details>
> <summary><em>🟠 Major</em> · <em>🎯 Functional Correctness</em> · <code>impl.pyx:1184</code></summary><blockquote>
> 
> `python/cuda/impl.pyx:1184`
> _🎯 Functional Correctness_ | _🟠 Major_
> 
> suggestion: `locality_domain_count` still raises `ValueError(... "(see stderr)")`.
> 
> </blockquote></details>

---

<details>
<summary>ℹ️ Review info</summary>

<details>
<summary>⚙️ Run configuration</summary>

**Configuration used**: Repository: o/r/.coderabbit.yaml

</details>

<details>
<summary>📒 Files selected for processing (1)</summary>

* `packages/cli/src/headless.ts`

</details>

</details>

<!-- This is an auto-generated comment by CodeRabbit for review status -->
