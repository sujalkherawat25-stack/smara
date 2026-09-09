# W1 canonical research gate report — 2026-09-09

## Outcome

W1 passes its deterministic Windows agent-loop gate. Research planning,
retrieval, artifact provenance, claim judgment, contradiction handling,
continuation and completion validation now execute inside the canonical
`SmaraAutonomousAgent` and `SessionEngine` path.

This result does not promote live-web or model-driven research quality. Those
measurements remain part of W5. OCR remains unavailable on this installation
and fails closed without producing evidence.

## Implemented contract

- Numeric-only, wrong-number, negated, wrong-date, wrong-entity and unstated
  causal claims no longer pass through word overlap. Equivalent supported unit
  conversions are handled explicitly.
- Claim judgments are `supported`, `refuted` or `insufficient`; snippets remain
  discovery-only.
- Original response/PDF/image bytes and extracted text are separate immutable
  artifacts. Evidence records link source hash, extraction hash, exact passage
  location and claim judgment.
- Research graph, evidence index, claims and validation are session-scoped and
  content-addressed. Continuation checkpoints retain their research artifact.
- Canonical typed actions cover plan, search, fetch, local evidence ingestion,
  inspection, resolution and validation. The research profile cannot bypass
  these actions with legacy raw web tools or an immediate unsupported answer.
- A fetched page alone no longer resolves a research question. Required claims
  must have current artifact-backed validation; state corruption or change
  after validation prevents completion.

## Versioned acceptance evidence

The manifest `tests/evals/windows_research/manifest.json` declares 17 cases
(`W1-01` through `W1-17`) with stable test node IDs, validators, definition
hashes and fixture hash. It covers the required direct fact, dependency chain,
contradiction, duplicate publication, snippet-only failure, number, negation,
units/dates, real PDF table, unavailable OCR and resume cases, plus bypass and
post-validation corruption regressions.

Commands and results:

```text
PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q \
  tests/test_w1_eval_manifest.py tests/test_eval_inventory.py \
  tests/test_w1_research_agent_integration.py
# 19 passed in 4.36s

PYTHONPATH=src .venv/Scripts/python.exe -m compileall -q src/smara benchmarks
# passed

PYTHONPATH=src .venv/Scripts/python.exe -m pytest -q
# 518 passed, 1 skipped, 2 warnings in 120.70s
```

The warnings are the pre-existing short JWT test-key warnings. The single skip
is reported separately and is not counted as passed.

## Honest boundary

- Scripted model responses make the reliability cases deterministic; they do
  not establish accuracy of a configured live model/provider.
- No live-web accuracy score, provider cost run or W5 acceptance score was
  produced.
- `pytesseract` is absent. The scanned-image case proves explicit unavailable
  handling and non-completion, not OCR extraction quality.
- W2 managed-browser integration, W3 durable process journeys, W4 installed
  capability discovery and W5 24-task acceptance remain subsequent gates.
