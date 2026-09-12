# Smara live-web research and data-analysis readiness

Updated: 2026-09-12

Status: **verified_live_web** (sealed 20×3 acceptance gate passed 60/60).

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

## Live acceptance gate results (2026-09-12)

- **Sealed Pack**: `tests/evals/live_web_acceptance/manifest.json` (SHA-256: `c1b6784bfb7c2761027713119e38df8f6860955c2ae8d86b1e079aba310e77d9`)
- **References**: `tests/evals/live_web_acceptance/references.json` (SHA-256: `0f7d04d957a87b8596313380b9c5f0dd29048cf795d5fecb8ddda8f42fae5d35`)
- **Evidence**: `release/evidence/LIVE_WEB_ACCEPTANCE_2026-09-12.json` (SHA-256: `77d9c238ed4e3c358bff27effcce82dacf906d0347bd03a76d1ab2b04ceedcf0`)
- **Score**: **60/60 passed (100.0%)** across 3 repetitions.
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

## Promoted Capability Status

- `live_web_research`: **`verified_live_web`**
- `research_data_analysis`: **`verified_live_web_and_deterministic`**

Separate later tiers: authenticated/paywalled websites, OCR, forecasting, causal
inference, regulated financial/medical analysis, Linux, disposable VM desktop, and
official external benchmarks. None should inherit the live-web claim automatically.
