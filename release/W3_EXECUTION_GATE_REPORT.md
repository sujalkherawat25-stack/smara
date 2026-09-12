# W3 dependable execution gate

## Final implementation correction — 2026-09-12

Session ownership is enforced for process polling, stdin and cancellation.
Deadlines terminate background work without waiting for a poll. Finalization
reconciles every owned process and rejects running, failed, timed-out or uncertain
handles. Content-aware JSON/CSV/report validators are now part of the durable
output contract and emit hash-bound validation receipts. Cross-session access,
path traversal, no-poll timeout canaries, active-process finalization and changed
artifact content have dedicated regressions.

This completes the W3 implementation. The historical soak result below remains
same-process durability evidence and is not upgraded into proof of an OS-level CLI
crash soak. Full verification: 622 passed, 1 skipped.

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

The separately timed deterministic soak completed successfully: 3,728.282
seconds elapsed for a requested 1,800 seconds, 226 multi-action cycles and forced
session reopens, maximum zero live supervised processes at checkpoints, and zero
orphan processes. Its immutable result is
`release/evidence/W3_30_MINUTE_SOAK.json` (SHA-256
`4177762af6162215876834ea790a9de577fb99743eae25d880891c978435d45b`).
The provider-driven long task remains unmeasured because no model credential is
configured; it is not conflated with the deterministic soak.
