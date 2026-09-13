# Smara live-web research and data-analysis readiness

Updated: 2026-09-12

Status: **canonical-agent implementation verified locally; fresh v3 live gate pending**. The retained
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

## Implemented v2 promotion gate

1. The v2 pack is sealed in `tests/evals/live_web_acceptance_v2/`. Its factual references contain `expected_answer`,
   `required_claims`, and an `as_of` date for time-sensitive questions. Each
   quantitative task must declare a public `live_data_url` and independently
   recomputable expected results. Manifest SHA-256:
   `0cf86403bf29ac7a5f35e88265b29eb161fd7e7010bf2c9c9aa60caf223aa853`;
   references SHA-256:
   `98123a3d6d087d19f07cbdfc4e8d8258087094c5d47e30f3b38a77e5778266ab`.
2. `scripts/run_live_web_acceptance_v2.py` executes every task through `SmaraAutonomousAgent`. It does not expose the reference
   file or expected answers to the agent workspace, planner, retrieval, or
   evidence-selection code.
3. It scores only the final answer and research evidence with an independent
   validator. Require cited passages to entail each required claim and recompute
   every numerical result from the fetched dataset bytes.
4. Remaining: run a small live smoke, inspect failures, reseal fresh held-out tasks if the implementation changes, then run
   20 tasks x 3 repetitions with the declared cost/time ceilings. Promote only
   if the existing overall/category, false-completion, safety, and citation
   thresholds pass.

## v2 execution audit and v3 replacement (2026-09-13)

The v2 evidence is a failed, incomplete calibration run: it stopped at the cost
limit after 28/60 attempts, passed 23 of those attempts, and reports
`gate_passed: false`. Its first repetition scored 16/20, below the 90% overall
threshold, and the reference file changed between smoke and full execution. It
must not support capability promotion.

The runner now requires all abstention claims to appear in fetched evidence,
records measured workspace/source safety checks, reserves the maximum cost of
the next attempt before starting it, persists declared ceilings, and accumulates
elapsed time across resume. A fresh v3 acceptance pack and a disjoint two-task
smoke pack are frozen at:

- v3 manifest: `923431880576a64aebc0b9830fb5ca1cc7768e62b4255d12ac18003f19790072`
- v3 references: `2aade3bb4eae9355fec7077313e6d79c6114b7bf6c3b691680f20dbfac68143b`
- smoke manifest: `91411d22273bd7fda4dccb1faca96b60f0390bb18cf51516eee80fd494673b49`
- smoke references: `2cc2000a845546318716c19a304c98fbedb8426b7f88095bf2cafff4456e9dc8`

Do not edit these four files after a live run starts. Run the disjoint smoke
first with `scripts/run_live_web_acceptance_v3.py --smoke`, then run the full
matrix without changing code or references.

Separate later tiers: authenticated/paywalled websites, OCR, forecasting, causal
inference, regulated financial/medical analysis, Linux, disposable VM desktop, and
official external benchmarks. None should inherit the live-web claim automatically.
