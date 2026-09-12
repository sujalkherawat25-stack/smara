# Smara live-web research and data-analysis readiness

Updated: 2026-09-12

Status: production capability implemented; live-provider acceptance remains.

## Implemented now

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
- New canonical `research_analyze` action. It accepts bounded structured rows only
  when linked to valid evidence artifacts and emits an immutable analysis artifact
  containing:
  - dataset SHA-256 and source evidence IDs;
  - explicit no-imputation missing-value policy;
  - count, missingness, sum, mean, median, sample standard deviation, min,
    quartiles, and max;
  - grouped aggregates (maximum 200 groups);
  - pairwise Pearson correlation with constant/undersized-series reasons;
  - ISO-date start/end, absolute change, and percentage change;
  - 1.5×IQR outlier bounds and bounded row references.
- Analysis is capped at 10,000 rows, 100 columns, 20 numeric fields, and 100
  retained outliers per metric. Non-finite values are excluded, never silently
  imputed.

Verification at implementation commit:

- focused research/data suite: **50 passed**;
- complete source regression: **802 passed, 1 skipped, 2 existing JWT warnings**;
- no provider key is required for deterministic adapter and analysis verification.

## Required configuration before live use

Save one supported provider key in Desktop Settings and select the same provider:

| Provider | Credential alias | Provider setting |
|---|---|---|
| Tavily | `TAVILY_API_KEY` | `tavily` |
| Exa | `EXA_API_KEY` | `exa` |
| Brave Search | `BRAVE_SEARCH_API_KEY` | `brave` |
| Serper | `SERPER_API_KEY` | `serper` |

For high-quality general research, use Tavily advanced or Exa as primary and a
different provider as fallback. Provider credentials must be supplied by the user;
the Sarvam model key is not a web-search subscription.

## Live acceptance gate

Create a sealed, time-stamped pack whose answers are validated after retrieval and
whose exact references are unavailable to the agent. Use at least 20 tasks:

1. Five current factual questions with authoritative primary sources and explicit
   as-of timestamps.
2. Three breaking-news questions requiring publication time and event time to be
   distinguished.
3. Three multi-source contradiction/changed-fact questions.
4. Three primary-versus-secondary source-quality tasks.
5. Four JSON/CSV quantitative tasks covering grouped metrics, missing values,
   correlations, trends, and outliers.
6. Two blocked/failed retrieval tasks requiring honest abstention.

Run three clean repetitions (60 attempts), with provider/model/search budgets and
cost ceilings declared before execution. Each task must record queries, selected
and rejected sources, redirects, retrieval timestamps, publication dates when
available, content hashes, passage locations, analysis artifact hashes, claim
judgments, citations, latency, and provider/model usage.

Promotion thresholds:

- at least 90% task success overall and 80% in every category;
- at least 95% citation precision and 90% required-claim evidence coverage;
- 100% numerical recomputation match for quantitative claims;
- zero snippet-as-proof cases, fabricated citations, private-network fetches,
  source-artifact mismatches, false completions, or unlabelled stale/current data;
- correct abstention for every deliberately insufficient or blocked task;
- all attempts retained in the denominator, with no failure-only reruns.

## What remains next

1. User configures a Tavily, Exa, Brave, or Serper search credential.
2. Run a bounded three-task smoke: current primary-source fact, conflicting
   sources, and JSON/CSV analysis.
3. Fix only development fixtures if smoke exposes defects; replace contaminated
   scored cases.
4. Obtain explicit user approval for live search/model cost and time ceilings.
5. Run the sealed 20×3 gate and publish immutable evidence.
6. Promote `live_web_research` from `implemented_pending_live_acceptance` to
   `verified_live_web` only when every threshold passes.

Separate later tiers: authenticated/paywalled websites, OCR, forecasting, causal
inference, regulated financial/medical analysis, Linux, disposable VM desktop, and
official external benchmarks. None should inherit the live-web claim automatically.
