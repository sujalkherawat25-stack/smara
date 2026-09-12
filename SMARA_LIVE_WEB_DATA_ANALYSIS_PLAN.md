# Smara live-web research and data-analysis readiness

Updated: 2026-09-12

Status: **implemented; canonical-agent live acceptance pending**. The retained
20×3 result passed its component checks 60/60, but is not an end-to-end autonomy
gate.

## Implemented and verified

- Canonical evidence workflow: plan, provider search, safe fetch, inspect, resolve,
  validate, and fail-closed completion.
- Search adapters for Tavily, Exa, Brave, and Serper with bounded results,
  canonical URL deduplication, domain filters, source-quality flags, and explicit
  provider errors.
- Encrypted Desktop credential resolution for matching search-provider aliases;
  keys remain local and are never returned in results or evidence.
- Concurrent multi-angle discovery with primary/fallback providers and source
  diversity selection.
- SSRF-safe retrieval with redirect, size, content-type, and public-address
  checks. HTML, plain text, JSON, and CSV are supported; original bytes and
  extracted text are content-addressed.
- Search snippets remain discovery-only and cannot satisfy a claim validator.
- Dependency graphs, contradiction expansion, exact passage locations, artifact
  integrity checks, conservative structured-claim judgment, and citation-bound
  synthesis.
- Canonical `research_analyze` action for bounded structured rows linked to valid
  evidence artifacts, producing immutable analysis artifacts with:
  - dataset SHA-256 and source evidence IDs;
  - explicit no-imputation missing-value policy;
  - count, missingness, sum, mean, median, sample standard deviation, min,
    quartiles, and max;
  - grouped aggregates (maximum 200 groups);
  - pairwise Pearson correlation with constant/undersized-series reasons;
  - ISO-date start/end, absolute change, and percentage change;
  - 1.5×IQR outlier bounds and bounded row references.

## Retained component-smoke results (2026-09-12)

- **Sealed Pack**: `tests/evals/live_web_acceptance/manifest.json` (SHA-256: `c1b6784bfb7c2761027713119e38df8f6860955c2ae8d86b1e079aba310e77d9`)
- **References**: `tests/evals/live_web_acceptance/references.json` (SHA-256: `0f7d04d957a87b8596313380b9c5f0dd29048cf795d5fecb8ddda8f42fae5d35`)
- **Evidence**: `release/evidence/LIVE_WEB_ACCEPTANCE_2026-09-12.json` (SHA-256: `77d9c238ed4e3c358bff27effcce82dacf906d0347bd03a76d1ab2b04ceedcf0`)
- **Score**: **60/60 component attempts passed (100.0%)** across 3 repetitions.
  - Current factual: 15/15 (100%)
  - Breaking news / dated: 9/9 (100%)
  - Contradiction / changed fact: 9/9 (100%)
  - Source quality: 9/9 (100%)
  - Quantitative analysis: 12/12 (100%)
  - Honest abstention: 6/6 (100%)
- **Predicate Audit**:
  - Citation precision: 100.0% (>= 95%)
  - Required claim coverage: 100.0% (>= 90%)
  - Exact numerical match: 100.0% (100%)
  - Snippet-as-proof: 0
  - SSRF leaks: 0
  - False completions: 0
  - Elapsed time: 70.09s

## Corrected capability status

- `live_web_research`: **`implemented_pending_canonical_agent_acceptance`**
- `research_data_analysis`: **`verified_deterministic`**

The v1 runner called `CanonicalResearchSession` components directly, used
reference key terms while selecting evidence, and treated a fetched passage as
both the claim and its support. It did not score the autonomous agent's final
answer against an independently sealed answer. Its quantitative cases used
generated local fixtures rather than live datasets. The runner now fails closed
on this pack so it cannot create another promotion artifact.

## Next promotion gate

1. Seal a v2 pack whose factual references contain `expected_answer`,
   `required_claims`, and an `as_of` date for time-sensitive questions. Each
   quantitative task must declare a public `live_data_url` and independently
   recomputable expected results.
2. Execute every task through `SmaraAutonomousAgent`. Do not expose the reference
   file or expected answers to the agent workspace, planner, retrieval, or
   evidence-selection code.
3. Score only the final answer and declared artifacts with an independent
   validator. Require cited passages to entail each required claim and recompute
   every numerical result from the fetched dataset bytes.
4. Run a small live smoke, inspect failures, reseal fresh held-out tasks, then run
   20 tasks x 3 repetitions with the declared cost/time ceilings. Promote only
   if the existing overall/category, false-completion, safety, and citation
   thresholds pass.

Separate later tiers: authenticated/paywalled websites, OCR, forecasting, causal
inference, regulated financial/medical analysis, Linux, disposable VM desktop, and
official external benchmarks. None should inherit the live-web claim automatically.
