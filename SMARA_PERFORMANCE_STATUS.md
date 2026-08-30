# Smara performance rollout status

This is the execution ledger for Section 6.4 of
[`SMARA_IMPLEMENTATION_PLAN.md`](./SMARA_IMPLEMENTATION_PLAN.md). Each package
is independently reversible and keeps PostgreSQL/task events, approvals, and
account data intact.

## Completed packages

| Package | Result | Verification |
| --- | --- | --- |
| PERF-0 | Request IDs, redacted timing spans, durable `done` timings, offline benchmark | Python suite + benchmark |
| PERF-1 | Lifespan-owned provider/memory HTTP resources and bounded pools | Runtime resource tests |
| PERF-2 | Pooled PostgreSQL access, async state facade, indexes | Store/API concurrency tests |
| PERF-3 | Deterministic safe lanes with durable-work escalation | Routing and call-shape tests |
| PERF-4 | Selective memory, timeout, bounded context, one-time attachment context | Agent/runtime tests |
| PERF-5 | Redis advisory work signals, durable cursors, worker wake-up and repair poll | Signal/task tests |
| PERF-6 | Executor long-poll API, shared Desktop HTTP client, atomic settings cache | Python + Rust Desktop tests |
| PERF-7 | Durable SSE cursor reconnect, event dedupe, progressive result repair, token render coalescing, collapsed noise, Desktop token coalescing | Frontend type-check/build + Python suite |
| PERF-8 | Environment rollback switches, health exposure, fixed owner routing corpus, redacted benchmark artifact output | `tests/evals`, Python suite, offline benchmark |

## Rollout switches

Set these in the Smara API/worker environment. They are read at process start;
restart the affected service after changing one.

```dotenv
SMARA_FAST_ROUTING_ENABLED=true
SMARA_POOLED_RESOURCES_ENABLED=true
SMARA_WORK_SIGNALS_ENABLED=true
SMARA_DESKTOP_LONG_POLL_ENABLED=true
SMARA_SHADOW_ROUTING_ENABLED=false
SMARA_WORKER_CONCURRENCY=4
```

Rollback one optimization by setting its switch to `false`. For worker load,
set `SMARA_WORKER_CONCURRENCY=1`. A rollback returns to bounded polling or
per-request resources; it does not delete or rewrite durable work.

## Verification performed

- Python: **209 passed**, two existing JWT key-length warnings only.
- Python bytecode compilation: passed.
- Frontend type-check: passed.
- Frontend production build: passed (4218 modules transformed).
- Desktop production build: passed.
- Offline fixed corpus: 9 cases, including greeting, memory recall, exact
  tools, search, multi-step research, durable desktop work, and cancellation.

## Remaining promotion evidence

The code packages are complete. Promotion still requires deployment evidence,
not more feature code:

1. Run the authenticated owner Web shadow/account-isolation suite on the live
   VM and capture p50/p95 from the timing fields.
2. Run the physical Windows offline → restart → reconnect → cancel/revoke drill
   against the installed Desktop package and confirm one execution per task.
3. Review the live health payload's `rollout` object and keep the rollback
   switches documented with the deployment record before opening the beta gate.
