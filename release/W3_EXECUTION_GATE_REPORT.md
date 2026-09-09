# W3 dependable execution gate

Date: 2026-09-09. Scope: Windows terminal, files and durable continuation.

The canonical autonomous schema now exposes session-owned process start, bounded
cursor polling, stdin and cancellation. Process metadata and logs live below the
durable session. A new runtime can inspect durable logs but does not claim it can
reattach to an in-memory supervisor: the state is explicitly
`interrupted_uncertain` and `reconnectable=false`. Mutation hash guards prevent
overwriting a concurrent user edit. JSON, CSV and report validators check expected
content instead of existence or syntax alone.

The eight journeys in `tests/evals/windows_execution/manifest.json` cover repair,
failure, edit conflict, Unicode/space paths, interactive stdin, cancellation,
separate-runtime restart and three compactions with recoverable evidence. Focused
gate:

```text
.venv\Scripts\python.exe -m pytest -q tests/test_w3_dependable_execution.py tests/test_h2_execution_broker.py tests/test_h3_context_and_continuation.py
# 29 passed in 9.24s

.venv\Scripts\python.exe -m pytest -q
# 541 passed, 1 skipped, 2 existing JWT-key warnings in 145.47s
```

The separately timed 30-minute soak and provider-driven long task are release
measurements and are not represented by this deterministic focused result.
