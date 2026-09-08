# H3-H7 implementation gate report — 2026-09-07

Implementation base: `17ec93eca8840c8d3945234afd8b6de14fe18935`, foundation
commit `a4837c3`, and the scoped hardening commit recorded in repository history.
Environment: Windows, Python 3.14.0, Playwright 1.62.0.

## Verified locally

- H3 real-loop durability: individual model/tool events, provider usage,
  failed-to-passing revision-bound evidence, mutation deduplication, model
  budget denial, conservative context packing, hash-verified continuation
  lineage, durable progress records and bounded stall recovery.
- H3 fixture inventory: R25-R32 definitions are versioned and carry a checked
  SHA-256 over each ID/fixture/validator definition and executable fixture.
  Manifest SHA-256:
  `06f9f45a1571f04a5b8552b3b7586d7f26861630497121999c5b91f70d30f3b5`.
- H4 core: bounded dependency graph, persisted typed evidence index, discovery-
  only snippet handling, exact offsets/content hashes, OCR uncertainty and
  duplicate-publication retention. A sealed deterministic corpus now covers
  multi-hop dependency ordering, contradictions, duplicates, unavailable
  retrieval, PDF-table coordinates, uncertain OCR, and separate evidence
  precision/coverage. Provider-driven live-web accuracy remains a separate tier.
- H5a verified locally: owned isolated Chromium contexts/tabs, popups, frames,
  dialogs, grounded observed references, stale-reference rejection, forms,
  uploads/downloads, SPA/delayed state, screenshots, scrolling, navigation
  failure, cancellation and restart invalidation. B01-B20 now execute distinct
  state validators from clean contexts. The real Chromium suite passed three
  post-hardening runs, including the successful full-suite run; retained JUnit
  evidence is `artifacts/h5-browser-strengthened-repeat3.xml`.
- H6 policy core: spawned-process worker boundary, explicit workspace, parent
  budget reservation/reconciliation, timeout termination, canonical child
  status/evidence/usage, patch validation, quarantined skill candidates, and
  filtered worker environments. Root cancellation terminates/reconciles a real
  spawned child, and the legacy swarm can no longer bypass the parent ledger.
  Delegation remains opt-in until the predeclared matched-budget ablation is positive.
- H7 core: authenticated/scoped run, inspect, resume and cancel operations,
  idempotent command IDs, versioned event replay cursors and explicit event-gap
  rejection over a loopback-only HTTP server. CLI prompt/ask/JSON/resume/GAIA
  paths have semantic-event parity, and the desktop Rust goal bridge calls the
  canonical app adapter. The final wheel installed into a disposable Windows
  virtualenv and completed an engine task in a Unicode/space path.

## Commands and results

```text
PYTHONPATH=src .venv/Scripts/python.exe -m compileall -q src/smara benchmarks
# passed

PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q
# 495 passed, 1 skipped, 2 warnings in 100.62s

# strengthened tests/test_h5_managed_browser.py against installed Chrome
# 25 passed in 60.33s; 25 passed in successful full gate; 25 passed in 63.25s

CARGO_TARGET_DIR=artifacts/cargo-h7-check cargo check --locked
# passed in 1m38s

.venv/Scripts/python.exe -m pip wheel . --no-deps --wheel-dir artifacts/final-wheels
# smara-0.1.0-py3-none-any.whl
# SHA-256 ee3c79ec6b45317e4174d04dd250adb0a36cc917fd062426fd28db43f3f659cd

artifacts/h7-smoke-venv/Scripts/smara.exe --workspace "artifacts/Unicode Workspace Ω" doctor --json
# ok=true; workspace writable; SQLite WAL and engine checks pass

artifacts/h7-smoke-venv/Scripts/python.exe -c <installed SessionEngine smoke>
# completed
```

The two warnings are the existing short test JWT key warning. The one skip is
reported by pytest and is not counted as passed.

## Blocked or deliberately unpromoted

- H5b real D01-D20 state validation is **blocked**: there is no attested
  disposable VM transport/reset snapshot on this host. Only the fail-closed VM
  contract and coordinate/reset tests ran; no host desktop actions were used.
- H7 Linux install/task smoke is **blocked**: WSL is present without a Python
  runtime. Windows wheel evidence must not be relabelled Linux evidence.
- Official GAIA, SWE-bench Verified, OSWorld/WebArena and paid/model-driven
  long-run scores are **unmeasured**.
- Delegation stays `experimental_opt_in`; no positive matched-model,
  matched-budget latency/cost/quality ablation was fabricated.
- Research remains experimental pending provider/model-driven accuracy runs;
  its deterministic sealed-corpus provenance gate now exists. Browser is
  promoted only as verified on the deterministic local Chromium tier, not as
  WebArena or live-web performance.
