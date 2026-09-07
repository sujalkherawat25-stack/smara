# H3-H7 implementation gate report — 2026-09-07

Implementation base: `17ec93eca8840c8d3945234afd8b6de14fe18935` plus tracked diff hash
`42d0034d38cdebc69ffc69a932f0c4ab049b49b8` and the new files listed by
`git status`. Environment: Windows, Python 3.14.0, Playwright 1.62.0.

## Verified locally

- H3 real-loop durability: individual model/tool events, provider usage,
  failed-to-passing revision-bound evidence, mutation deduplication, model
  budget denial, conservative context packing, hash-verified continuation
  lineage, durable progress records and bounded stall recovery.
- H3 fixture inventory: R25-R32 definitions are versioned and carry a checked
  SHA-256 over each ID/fixture/validator definition. Manifest SHA-256:
  `f8e7121046d166157e229482f636c6b2389c1ac9cd349aed31b4756f986b0d26`.
- H4 core: bounded dependency graph, persisted typed evidence index, discovery-
  only snippet handling, exact offsets/content hashes, OCR uncertainty and
  duplicate-publication retention. The live-web and sealed multimodal corpus
  scorecards remain separate gates.
- H5a implementation: owned isolated Chromium contexts/tabs, popups, frames,
  dialogs, grounded observed references, stale-reference rejection, forms,
  uploads/downloads, SPA/delayed state, screenshots, scrolling, navigation
  failure, cancellation and restart invalidation. The real Chromium suite ran
  three times: `25 passed` in 46.06s, 46.42s and 47.05s.
- H6 policy core: spawned-process worker boundary, explicit workspace, parent
  budget reservation/reconciliation, timeout termination, canonical child
  status/evidence/usage, patch validation, quarantined skill candidates. It
  remains opt-in until the predeclared matched-budget ablation is positive.
- H7 core: authenticated/scoped run, inspect, resume and cancel operations,
  idempotent command IDs, versioned event replay cursors and explicit event-gap
  rejection. The final wheel installed into a disposable Windows virtualenv
  and completed an engine task in a Unicode/space path.

## Commands and results

```text
PYTHONPATH=src .venv/Scripts/python.exe -m compileall -q src/smara benchmarks
# passed

PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q
# 474 passed, 1 skipped, 2 warnings in 90.05s

# tests/test_h5_managed_browser.py repeated three times against installed Chrome
# 25 passed in 46.06s; 25 passed in 46.42s; 25 passed in 47.05s

.venv/Scripts/python.exe -m pip wheel . --no-deps --wheel-dir artifacts/final-wheels
# smara-0.1.0-py3-none-any.whl
# SHA-256 9072f2a7cf12d541128483b36bea39018e342a7a8a4ae5e9f6059c75955f45b9

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
- Research and browser remain experimental in the release manifest pending the
  sealed-corpus and complete named-fixture scorecards required for promotion.
