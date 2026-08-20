# Smara: Cross-Platform Agent Implementation Plan

## Product definition

Smara is a durable personal/work agent with one shared brain and three clients:

```text
Web      = command centre for tasks, evidence, settings, and history
Desktop  = private local executor for files, terminal, and local tools
Phone    = capture, notification, and approval companion
```

Syntarus is a separate memory product. Smara uses its public SDK/API only;
Smara must never import MemoryOS internals or talk to Qdrant/Neo4j directly.

## Success definition

The first meaningful release is successful when a user can say:

> Research a topic, prepare a cited report, update an approved local document,
> schedule a follow-up, and ask before sending anything externally.

The task must survive web refreshes, worker restarts, desktop disconnects, and
temporary integration failures. It must show a readable activity history,
evidence, costs, and every approval decision.

## Architecture boundary

```text
Smara API / workers
        |
        | public SDK only
        v
Syntarus Memory API
        |
        v
Syntarus storage and retrieval infrastructure
```

## Agent-runtime design (Hermes lessons, adapted for Smara)

Hermes demonstrates useful agent-runtime patterns: a provider-neutral model
layer, a tool registry/toolsets, bounded tool-call iterations, cancellation,
context management, retries, and asynchronous memory retention. Smara adopts
those patterns but must not copy a single large conversational agent loop.

Smara splits the concern into durable task orchestration and a small runtime for
one claimed task step:

```text
Task engine owns:     task state, leases, retries, approvals, artifacts, events
Agent runtime owns:   one model/tool reasoning loop for one task step
Syntarus owns:        long-term memory retrieval and verified write-back
Executor owns:        the actual hosted, sandboxed, or local action
```

The runtime lifecycle is:

```text
claim task step
  -> retrieve scoped Syntarus context
  -> assemble stable prompt + allowed tools
  -> call selected provider/model
  -> validate model tool request against policy
  -> execute, request approval, or wait for a named executor
  -> emit durable task events
  -> return step result; write verified conclusion to Syntarus asynchronously
```

Every model call must be interruptible. Every loop has explicit maximum
iterations, tool-call count, timeout, token budget, and cost budget. Tool
results are bounded before returning to a model.

## Graph strategy

Use two graphs with different responsibilities.

### Task graph — required early

The task graph is a directed dependency graph of work:

```text
find sources -> verify sources -> compare findings -> write report
                                                     -> approval -> deliver
```

It enables parallel independent work, precise retries, understandable progress,
recovery after failure, and a clear explanation of why work is blocked. Model
it with `task_steps` and dependency edges; do not require a graph database for
the first release.

### Knowledge graph — use through Syntarus

Syntarus already provides entity/relationship and temporal-memory retrieval.
Use it for durable questions about people, projects, decisions, and evidence.
Do not build a second Smara knowledge graph. Smara asks Syntarus for scoped
context and records provenance; later SDK additions can expose richer graph
retrieval when measurements prove it is needed.

```text
Task graph       = what Smara is doing now
Syntarus graph   = what Smara knows from the past
```

## Cost and simplicity rules

1. Start on one VM with Docker Compose. Separate service boundaries and
   credentials matter now; Kubernetes does not.
2. Use Postgres for durable task state and Redis for transient streams/cache.
   Do not introduce Kafka, a workflow platform, or a second graph database in
   the first release.
3. Use a small/cheap model for routing, classification, extraction, and short
   summaries. Escalate to the selected strong model only for planning, difficult
   synthesis, or final artifacts.
4. Cache stable prompt sections, workspace instructions, tool schemas, and
   document/memory retrieval results with a bounded TTL.
5. Stream task events immediately; do not make the user wait silently for the
   final answer.
6. Prefer one high-value vertical slice over many shallow integrations.
7. Use controlled hosted workers for safe read-only work. Create an isolated
   sandbox only for unknown code, repositories, or browser automation.
8. Keep model/tool output limits, wall-clock limits, per-task budgets, and
   explicit cancellation from the first worker implementation.
9. Never send raw local files, secrets, or sensitive memories to a provider
   without a visible user policy/approval.
10. Measure before optimizing: task completion rate, duplicate side effects,
    approval latency, worker recovery rate, retrieval latency, and cost per
    completed task are the first operating metrics.

### Hosted Smara services

```text
api                 authenticated client API, SSE/WebSocket events
task-worker         claims and executes durable task steps
scheduler           turns due schedules into task runs
integration-worker  OAuth refresh, webhooks, delivery retries
artifact service    report/upload/evidence storage
```

Use Postgres for durable task data and Redis only for event fan-out, cache,
presence, and notification delivery. Never make Redis the sole owner of a task.

## Current foundation

The independent `smara/` repository now contains:

- a task API, task event store, and approval gate;
- a separately runnable worker and scheduler process;
- a Syntarus-SDK-only memory adapter;
- Docker service definitions;
- isolated tests for task approval and memory adapter usage.

The foundation intentionally does not yet execute arbitrary commands, send
messages, or call a model. Those powers must be introduced through explicit,
audited executor and integration contracts.

## Phase 0 — Freeze contracts and deployment boundaries

### Build

1. Create a dedicated remote repository for this `smara/` repository.
2. Define separate VM configuration for Smara and Syntarus:
   - separate environment files and secrets;
   - separate Postgres database/user;
   - separate service/container names;
   - Smara gets only a server-side Syntarus project key.
3. Replace development header identity with a verified Smara session/JWT
   gateway. The API must never trust a browser-provided account identifier.
4. Replace the local SQLite task store with Postgres migrations.
5. Add structured logs, health/readiness checks, Sentry, and trace IDs.

### Done when

- Smara can deploy without importing or changing MemoryOS.
- A production request has a verified Smara account ID.
- A VM restart does not lose a created task.

## Phase 1 — Durable task control plane

### Build

Create these durable entities:

```text
workspaces
tasks
task_runs
task_steps
task_events
approval_requests
artifacts
executor_leases
integration_connections
```

Implement the task state machine:

```text
draft -> queued -> running -> waiting_approval -> running
      -> completed | failed | cancelled
```

Rules:

- Every external action has an idempotency key.
- Workers claim a step using a Postgres lease/row lock.
- A lost lease makes a task recoverable, never silently duplicated.
- Events are append-only and replayable by web, desktop, and phone.
- Cancellation means `stop before next step`; it never pretends an already
  started external operation was undone.

### Done when

- A task has an editable plan, live status, steps, artifacts, retries, and a
  final report.
- Restarting a worker during a task results in recovery or an explicit failure.
- Two workers cannot execute the same external action twice.

## Phase 2 — Syntarus memory adapter

### Build

Define Smara's canonical mapping:

```text
Smara account_id   -> Syntarus user_id
Smara workspace_id -> indexed Syntarus workspace scope
Smara task/run     -> run_id and task provenance
```

Memory rules:

- Shared user memory is used by web, desktop, and phone.
- `agent_id` is only for an isolated specialist, never for a client/device.
- Store durable conclusions, preferences, verified findings, and decisions.
- Keep raw documents, tool logs, and active task state outside long-term
  memory; store source links, hashes, and summaries instead.
- Write back only verified task outcomes or explicit user decisions.

Required Syntarus API/SDK extension:

```text
search filters: workspace_id, task_id, memory_kind, source_type,
document_id, document_version, sensitivity, status, expires_at
```

### Done when

- Before planning, Smara retrieves only relevant shared/project memory.
- After a verified result, Smara records provenance through the SDK.
- A user can inspect, correct, pin, and delete retained memory.

## Phase 3 — First vertical slice: verified research report

### Build

This is the first end-to-end task, not a generic autonomous agent:

```text
User creates research task
  -> worker builds plan
  -> retrieves sources
  -> verifies retrieval and records evidence
  -> creates cited report artifact
  -> waits for approval before any external delivery
  -> records final outcome in Syntarus
```

Add an evidence ledger with URL, title, retrieval time, content hash, excerpt,
claim, confidence, and report citation links.

### Done when

- The user can review every source behind a report claim.
- A failed/blocked source is visibly marked, not invented.
- A report is an artifact attached to one task run.

## Phase 4 — Executor protocol and Smara Desktop

### Build

Desktop becomes a registered executor, not a separate memory brain.

```text
Desktop capability examples:
- local_file_read
- local_file_write
- local_terminal
- local_browser
- local_model
```

Each task step declares:

```text
required capability
execution location (hosted / named desktop / approved sandbox)
approval policy
idempotency key
resource limits
```

Use an authenticated device pairing flow and a persistent outbound connection
from desktop to hosted Smara. Do not expose the desktop service to the public
internet.

### Done when

- A web-created task can wait for a named desktop, execute a permitted local
  action, stream progress, and return an artifact/result.
- Desktop refuses undeclared capability requests.
- The same task can be approved from web, phone, or desktop.

## Phase 5 — Integrations and approval policies

### Build

Create an integration registry for Gmail, Calendar, Telegram, GitHub, Drive,
and future connectors. Every integration has:

- encrypted credentials/refresh tokens;
- granted scopes and connection health;
- tool/action definitions;
- idempotency and audit records;
- policy: allowed, ask every time, blocked.

Approval levels:

```text
Observe     read/search only
Draft       create local drafts/artifacts only
Assisted    execute safe work; approval for external actions
Trusted     execute explicitly pre-approved bounded workflows
```

### Done when

- Sending email, calendar changes, code push, deletion, and spending actions
  always follow the configured policy.
- Approval previews are clear and can be edited/denied.

## Phase 6 — Web and phone clients

### Web

Build a task-first interface:

- active task board;
- task plan and live event timeline;
- source/evidence panel;
- artifacts panel;
- approvals inbox;
- executor/device status;
- workspace memory viewer and controls.

### Phone

Build only the high-value companion first:

- push notifications;
- approve/deny/edit;
- quick text/voice/photo capture;
- task status and final report viewing.

Do not build a full phone executor until the hosted task API is stable.

## Phase 7 — Sandbox and production hardening

For unknown code, repository tests, and browser automation, run a per-task
sandbox with bounded CPU, memory, disk, lifetime, network policy, and no
production secrets. A sandbox is for risky task execution; Docker is the
normal packaging/runtime mechanism for services.

Before broad autonomy, add:

- secret manager and credential rotation;
- resource/cost budgets;
- rate limits and abuse controls;
- task cancellation and dead-letter handling;
- backups and restore drills;
- end-to-end fault-injection tests;
- audit export and account deletion tests.

## Implementation order: start here

1. **Phase 0:** production auth + Postgres migration for Smara.
2. **Phase 1:** task runs, steps, events, leases, and recovery worker.
3. **Phase 2:** formal Syntarus identity/scope adapter and scoped retrieval.
4. **Phase 3:** build the verified research-report vertical slice.
5. **Phase 4:** connect desktop as the first real executor.

Do not start mobile, broad integrations, or arbitrary sandbox execution before
the research vertical slice has passed restart, approval, and evidence tests.

## Release gates

A phase cannot ship until its tests demonstrate:

- correct account/workspace isolation;
- no duplicated side effect under retry;
- no task loss during service restart;
- explicit approval before risky action;
- memory retrieval has traceable provenance;
- UI shows the same task state on every connected client.

## Implementation log

### 2026-08-20 — Phase 0 started

Implemented in the independent Smara repository:

- a production gateway-assertion boundary: development may use the test header,
  while production requires a recent HMAC-signed account assertion supplied by
  an authenticated Smara gateway;
- a versioned Postgres schema migration for workspaces, tasks, task runs,
  task steps/dependencies, events, approvals, and artifacts;
- a migration runner; and
- Docker Compose Postgres service wiring.

Verified: Smara unit/API tests and the Syntarus SDK suite pass. A live Postgres
deployment migration remains a deployment-environment verification step because
no production database URL or secrets are stored in this repository.

### 2026-08-20 — Phase 1 task graph started

Implemented a first durable task graph in the local development store:

- every task creates a task run and one or more ordered task steps;
- steps can depend on earlier steps;
- only dependency-ready steps can be claimed;
- worker leases prevent concurrent execution; and
- an expired lease requeues the step and records recovery before another worker
  may claim it.

Verified: approval, memory adapter, worker, lease recovery, and dependency
unlock/completion tests pass. The next Phase 1 slice is Postgres-backed runtime
use of the same graph contract plus explicit retry/cancellation policies.

### 2026-08-20 — Phase 1 bounded retries

Implemented bounded retry state for task steps. A failed worker operation now
records an event and error, releases its lease, waits until its retry time, and
requeues only while its configured attempt budget remains. Exhaustion marks the
step, run, and task failed rather than looping forever.

Verified: the retry test exercises three consecutive failures and proves the
third failure is terminal. The next reliability slice is cancellation semantics
and implementing the same worker graph contract against Postgres at runtime.

### 2026-08-20 — Phase 1 safe cancellation

Implemented cancellation as `stop before the next step`. Cancelling immediately
prevents queued/dependent steps from running. A currently leased step is allowed
to finish at its safe boundary; after it reports completion, Smara records the
task as cancelled and does not unlock later work. This never claims to undo an
external action already in progress.

Verified: a two-step task cancellation test proves the second dependent step is
never claimed after cancellation.

### 2026-08-20 — Phase 1 live Postgres task runtime

Implemented the production task-store path shared by the API and worker:

- `SMARA_DATABASE_URL` now selects `PostgresTaskStore`; SQLite is retained only
  when no database URL is configured for local development and isolated tests;
- Postgres migrations now include all graph-contract state used by the worker:
  task ownership on steps, retry metadata, cancellation state, task-run creation
  time, and task-level approval decisions;
- the production claim path uses transactional `FOR UPDATE SKIP LOCKED` row
  leases, so competing workers cannot claim the same ready step; and
- the Postgres store inherits the existing graph/retry/cancellation methods so
  local and deployed behavior remain one contract rather than two implementations.

Verified: Smara's task/API tests (10) and Syntarus SDK tests (10) pass, and
Python compilation plus diff validation pass. Live Compose/Postgres execution
could not be started on this machine because Docker Desktop's Linux engine is
not running; that final integration check must be rerun after Docker Desktop is
started with a real local `.env` file.

### 2026-08-20 — Phase 1 live Compose/Postgres verification

Ran the real Docker Compose stack after Docker Desktop became available. The
test uncovered and fixed three production-only compatibility issues:

- migration SQL was not copied into the installed service image;
- SQLite's integer Boolean values were invalid for Postgres Boolean columns;
- Postgres returns timestamp objects while the local SQLite store returns ISO
  strings.

Verified live against the Compose Postgres database: all four migrations
applied; API, worker, scheduler, and Postgres stayed running; an approval-gated
two-step graph moved from queued to waiting approval to completed with both
steps completed and all expected events; and a second two-step graph cancelled
before approval left both steps cancelled. Local tests now pass with 11 tests.
