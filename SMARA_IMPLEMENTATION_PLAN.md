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

### 2026-08-20 — Phase 2 memory scope contract and Phase 3 research slice

Implemented Smara's canonical Syntarus provenance contract:

- `account_id` maps to the shared Syntarus `user_id`; no device/client gets an
  isolated agent namespace;
- every memory read is bounded, and every verified write carries workspace,
  task, run, memory-kind, source-type, and verification-status provenance; and
- only the final cited research report is eligible for research write-back;
  raw source pages, tool logs, and active graph state remain in Smara.

Implemented the first verified research vertical slice:

- `POST /v1/research` creates an atomic three-step task graph (retrieve,
  verify, report) and its source ledger;
- retrieval allows only explicitly supplied public HTTP(S) URLs, checks every
  redirect target, bounds size/type, hashes content, and visibly records failed
  or blocked sources;
- verification assigns a confidence and citation label without inventing a
  claim; and
- report generation creates a cited Markdown artifact linked to the task.

Verified: 13 Smara tests pass. A real Compose/Postgres task retrieved
`https://example.com/`, recorded a 64-character source hash, became verified
as citation `[1]`, and completed with a cited artifact. The current public
Syntarus SDK/API exposes account isolation and provenance metadata but does
not yet expose strict metadata-filtered search or per-memory correction/pin
operations. Smara preserves the fields and deliberately does not claim that
those provider-side controls are already enforced; their SDK/platform extension
is required before this phase can be called fully scope-filtered.

### 2026-08-20 — Phase 4 desktop executor foundation

Implemented the hosted pairing and lease protocol for the existing Memento
desktop runtime:

- a logged-in Smara user creates a one-time, ten-minute pairing code with an
  explicit capability list;
- the desktop exchanges it once for a revocable executor ID and bearer token;
- desktop steps declare `executor_kind="desktop"` plus one required
  capability, so hosted workers cannot claim them;
- executor work is claimed with a lease (Postgres uses `FOR UPDATE SKIP LOCKED`)
  and only the owning desktop token can complete it; and
- the existing local agent now contains an outbound-only `smara_executor.py`
  bridge. Its first capability is intentionally read-only and it opens no
  inbound PC port.

Verified: 15 Smara unit tests pass. Real Compose/Postgres pairing created a
desktop executor, leased a `local_file_read` step to it, accepted its completion
with the device token, and moved the task to completed. Desktop bridge files
remain local in `agent_BOT` and are not pushed with Smara.

### 2026-08-20 — Phase 4 completed safe local-read slice; Phase 5 registry started

Completed the first real desktop execution path rather than treating a lease as
work completed:

- task steps now carry a small executor-only payload, and the paired Memento
  bridge can execute `local_file_read`;
- the desktop owner must explicitly configure approved local roots; path
  resolution rejects traversal/symlink escapes, directories, unapproved roots,
  and files over 256 KiB;
- no file content leaves the device. Smara receives only a filename, byte count,
  SHA-256 proof, and `content_shared=false`; and
- executor errors enter the same bounded retry contract as hosted steps.

Also fixed the live Postgres claim query to exclude desktop-only steps from
hosted workers.

Started Phase 5 with a durable integration registry and action-intent ledger:

- account-scoped Gmail, Calendar, Telegram, GitHub, and Drive connection
  records carry declared scopes, connection health, and one of the explicit
  `observe`, `draft`, `assisted`, `trusted`, or `blocked` policies;
- action intent is idempotent and recorded before any connector exists; policy
  derives `blocked`, `draft`, or `awaiting_approval` rather than trusting a
  caller to self-classify risk; and
- credentials and connector execution are deliberately not implemented in this
  slice. They need a reviewed encrypted secret store plus per-provider OAuth
  flows and bounded action executors.

Verified: five focused SQLite state-machine tests pass, Python compilation
passes, and live Compose/Postgres applied migrations 007/008. A live paired
desktop read of this repository's README completed a task and returned only a
SHA-256 proof; a GitHub `trusted` push intent remained
`awaiting_approval` (no external action was run). Desktop bridge files remain
local in `agent_BOT` and are not pushed with Smara.

### 2026-08-20 — Phase 5 integrations and approval policies completed

Implemented the production integration boundary without embedding any personal
provider token in the repository:

- credentials are Fernet-encrypted before entering Postgres; the vault key is a
  deployment secret, and the worker refuses to execute actions when it is not
  configured;
- Google (Gmail, Calendar, Drive) and GitHub have OAuth authorization-code +
  PKCE handoffs, encrypted token storage, and Google refresh-token renewal;
  Telegram uses a deliberately stored bot token;
- a separate integration worker leases only approved actions, preserves the
  idempotency key/audit record, and records a result or a bounded failure;
- initial narrowly defined adapters support Gmail send/search, Calendar
  create/list, Telegram send, GitHub repository list/content commit, and Drive
  search. Every other requested action fails closed; and
- assisted/trusted external intents show a preview and payload that an approver
  can edit, approve, or deny. The worker never receives an awaiting-approval
  action. Even `trusted` remains approval-gated until a bounded workflow
  template is introduced.

Verified: six focused state-machine/vault tests pass, Python compilation and
diff validation pass, and live Compose/Postgres applied migration 009 with the
new integration worker healthy. The live API test configured an assisted Gmail
connection, created an external send intent, edited it during approval, and
left it in `approved` state without credentials—so no email could be sent.

### 2026-08-20 — Phase 6 task-control web and phone companion slice

Implemented a dependency-free task-first control app served directly by Smara:

- responsive task board with task creation, polling refresh, task-step plan,
  activity timeline, evidence ledger, and artifact previews;
- integration and approval inbox views. An approver can review and edit an
  external action's preview/payload before approving or denying it;
- paired desktop status plus one-time desktop-pairing flow; and
- installable PWA metadata and offline shell caching. The compact layout is
  the first phone companion: it supports reviewing state and approvals but is
  never a phone executor.

The UI uses a browser-supplied account identifier only in explicitly enabled
development mode. Production calls rely on the authenticated Smara gateway.
Memory controls visibly state the current Syntarus SDK limitation rather than
pretending workspace-level correction/pinning already exists.

Verified: the rebuilt Compose stack keeps all services healthy; `/app/` and
its PWA manifest return 200; JavaScript syntax checks pass; and a live task is
visible through the task-board API along with the desktop-status endpoint.
Native push delivery and capture (voice/photo) remain the next small phone
companion slice, because they require VAPID credentials, TLS, and an explicit
device-permission workflow.

### 2026-08-20 — Phase 6 phone companion completed

Completed the phone companion as part of the installed Smara PWA:

- browser push subscriptions are account-scoped and delivered through VAPID;
  stale provider subscriptions are removed after a terminal push response;
- external integration actions entering `awaiting_approval` request an
  approval notification without exposing a device as an executor;
- the phone UI can create quick text, photo, and voice captures. Captures are
  bounded by media type/size, become inbox tasks with artifacts, and never
  enter a model or Syntarus automatically; and
- VAPID is deliberately configuration-gated. Without HTTPS, browser consent,
  and server VAPID keys, the UI can never pretend an alert was delivered.

Verified: eight focused state-machine tests and JavaScript syntax checks pass;
the rebuilt Compose/Postgres stack applied migration 010, accepted a real text
capture, and created its inbox task/artifact. With no VAPID credentials in the
local environment, the push public-key endpoint returns no key (safe inactive
state), confirming no accidental delivery configuration.

### 2026-08-20 — Phase 7 hardening started; Syntarus scope contract advanced

Implemented the first production-safe hardening slice: `/readyz` database
readiness, conservative API rate limiting, browser security headers, and
explicit Sentry/rate-limit deployment configuration. These defaults fail safely
and do not require hidden cloud services.

The public Syntarus SDK now accepts and transmits explicit search provenance
filters. Smara requests its workspace and verified-status scope through that
contract. The currently deployed Syntarus API still ignores those fields, so
this is deliberately recorded as a client contract advance—not a claim that
provider-side filtering is complete. The matching Memory API filter enforcement
and individual memory correction/pin/delete endpoints remain the next Syntarus
platform release.

Verified: eight Smara focused tests and ten Syntarus SDK tests pass, plus
Python/JavaScript compilation and diff checks.

### 2026-08-20 — Phase 7 production hardening completed for the control plane

Completed the remaining repository-owned Phase 7 controls:

- terminal step failures now enter an account-scoped durable dead-letter queue
  after their bounded retry budget; operators can review them through
  `/v1/dead-letters` rather than silently re-running side effects;
- integration encryption now accepts an ordered Fernet key ring. The first key
  encrypts and all configured keys decrypt, allowing an overlap period during
  credential rotation without making existing connections unreadable;
- Sentry is a real optional production dependency, initialized only when a DSN
  is supplied, with PII collection disabled by default;
- backup and integrity-check scripts document a portable Postgres archive and
  require a disposable restore drill before release; and
- the internal sandbox recipe has a no-network, no-mount, no-secret,
  read-only-root, capability-dropped Docker boundary with strict CPU, memory,
  process and time limits. It is intentionally not exposed as a public API.

Verified: 19 focused Smara tests pass (including dead-letter isolation, key
rotation compatibility, and the sandbox command contract). A clean Compose
rebuild installed the production dependency set, applied migration 011, and
ran API, worker, scheduler, integration worker and Postgres healthily;
`/health` and `/readyz` both returned success.

Production deployment work still required outside source code: configure a
real authenticated gateway/WAF for distributed rate limits, provide a secret
manager and rotation runbook, schedule backups to encrypted off-host storage,
perform the disposable restore drill, configure Sentry/alerts, and only then
wire approved code/browser steps to the sandbox. Syntarus server-side metadata
filter enforcement and memory-control endpoints are a separate MemoryOS
platform release; the SDK client contract is ready but must not be treated as
enforced until that release is deployed.

### 2026-08-20 — Production deployment controls extended

Added a Redis-backed API limiter for production multi-replica deployments;
development alone may use the explicitly configured in-process fallback. The
Compose stack now includes persistent Redis and production fails closed when
its shared limiter URL is absent. Added an account-scoped audit export endpoint
that excludes encrypted credentials, deliberate confirmed Smara account
deletion, and an approval-only sandbox task executor. A sandbox step cannot be
claimed until the task's approval record says approved.

Verified locally: 21 focused tests pass, including scope isolation for export
and deletion, and proof that sandbox execution cannot begin before approval.
The existing live Compose deployment was verified before the Redis image
rebuild; its final Redis runtime check must be repeated once the production
image rebuild finishes on the target host.

### 2026-08-20 — Smara Control integration completed

Connected the independent Smara Control service to the existing Smara web
without exposing control credentials to the browser:

- the web app now opens Control from its sidebar or Settings workspace area;
- the backend issues a short-lived, account-bound bridge token only to an
  authenticated Smara session;
- Control accepts that token through a tightly scoped `postMessage` handshake
  and uses it for its API calls;
- direct visits to `control-staging.syntarus.com` remain denied by design;
- the Control app is frameable only by `https://ai.syntarus.com`, while other
  routes remain protected; and
- a cache-busting embed URL prevents old frame-blocking browser responses from
  hiding the newly allowed Control app.

Verified: public Smara health, Control health, CSP/frame policy, the rendered
frontend bundle, and a temporary end-to-end authenticated bridge request all
passed. The Control repository changes are pushed; the MemoryOS working tree
remains intentionally unmerged because it contains unrelated user changes.

## Next implementation focus: tighten Smara Web first

The next work should make the web app the dependable command centre before
expanding desktop execution:

1. Replace remaining development identity paths with the production Smara
   gateway/session everywhere in the web UI.
2. Make the task board fully live: SSE/WebSocket event fan-out, reconnect and
   replay from the append-only event cursor, optimistic updates with safe
   rollback, and visible worker/device/integration health.
3. Finish research UX: source ledger, claim-to-citation links, failed-source
   explanations, artifact versioning, and export/download.
4. Finish approval UX: clear side-effect preview, editable payload, expiry,
   cancellation, and the same approval state on web, phone, and desktop.
5. Add workspace/task memory controls that reflect the actual Syntarus SDK
   contract. Until the provider enforces metadata filters, show the limitation
   and never imply that a filter is security isolation.
6. Add web-level reliability tests: refresh during a running task, reconnect
   after network loss, duplicate-submit/idempotency, account isolation, and
   approval/cancellation race cases.
7. Repeat the production Redis limiter check and complete the external Phase 7
   operations: gateway/WAF, secret manager and rotation, encrypted off-host
   backup/restore drill, Sentry alerts, and sandbox wiring for only approved
   risky steps.

## How the web–Control–desktop connection works

In simple terms:

```text
You sign in to Smara Web
        |
        | short-lived proof: "this is account X"
        v
Smara Control panel
        |
        | creates tasks, shows events, approvals, evidence
        v
Hosted Smara API + workers
        |
        | if a step needs the computer, wait for the paired desktop
        v
Smara Desktop executor
        |
        | performs only the declared, approved local capability
        v
Result/artifact -> task timeline -> web/phone
```

Control is the cockpit, not a second account system. The web session proves
who the user is; the short-lived bridge token lets the embedded Control panel
act for that account; the hosted API remains the authority for tasks, leases,
approvals, and audit events. Desktop is a separately paired executor with its
own revocable device token and capability list. It never receives the user's
master credentials and it never opens a public inbound port.

The practical user flow is: create or inspect a task in Smara Web, watch the
same events in Control, approve any risky step from the web/phone/desktop, and
let a named desktop execute only when the task explicitly requires a local
file, terminal, browser, or other declared capability. The next web phase will
make this flow smooth and observable before adding more desktop powers.

## Product UX decision: one Smara Web, internal Control services

The separate Control page and iframe are transitional development surfaces,
not the final product experience. The final Smara Web must present one unified
application with native screens for:

```text
Chat | Tasks | Research/Evidence | Approvals | Devices |
Integrations | Memory | Settings
```

During stabilization, the Control service may remain a separate internal
backend because its task, approval, executor, and integration contracts can be
tested and rolled back independently of the chat and MemoryOS pipeline. This
is an implementation boundary, not a second user-facing product.

### Native web consolidation work

1. Rebuild the Control task board as native Smara Web components.
2. Move approvals, evidence, artifacts, device status, integrations, and
   memory controls into the existing Smara navigation and settings screens.
3. Use the authenticated Smara session directly through a same-origin server
   boundary; do not expose bridge secrets to browser code.
4. Remove the Control iframe, separate Control sidebar item, and direct
   Control-page navigation only after native screens pass the same live-task,
   approval, account-isolation, and reconnect tests.
5. Keep the backend service boundary until production metrics show that a
   consolidation is safe; merging frontend screens does not require changing
   the Syntarus memory pipeline.

### Completion criteria

Users should be able to use Smara without knowing that a Control service ever
existed separately. Chat remains the conversational entry point; Tasks,
Research, Approvals, Devices, Integrations, Memory, and Settings become the
operational surfaces for inspecting and controlling the same durable task
state.

## Memento-to-Smara migration: make Smara the independent agent product

### Decision

The current Memento agent implementation lives inside the sensitive MemoryOS
repository and serves `ai.syntarus.com`. Smara will become the independent
agent product, while MemoryOS becomes the Syntarus memory platform only.

This is an extraction, not a direct copy of `app/memento/`. Memento currently
imports MemoryOS internals such as fused retrieval, ingestion, Redis state,
and infrastructure clients. Copying those imports into Smara would recreate
the coupling this architecture is intended to remove.

### Target responsibility split

```text
memoryos repository                         smara repository
-------------------                         ----------------
Syntarus Memory API                          Agent runtime and chat API
Syntarus SDK                                 Task graph, workers, scheduler
Memory storage/retrieval pipeline            Web, CLI, Desktop, Phone clients
Qdrant / Neo4j / Redis memory operations     Tool and integration registry
Memory extraction/consolidation workers      Approvals, artifacts, events
                                              packages/syntarus-adapter
```

Smara must use the public Syntarus SDK/API only. It must not import MemoryOS
Python modules or directly access Qdrant, Neo4j, or MemoryOS Redis state.

### Public routing after cutover

The public name remains stable for users:

```text
today
ai.syntarus.com -> MemoryOS frontend + embedded Memento API

target
ai.syntarus.com -> Smara Web + Smara API/workers -> Syntarus SDK -> MemoryOS API
```

The domain is not coupled to the source repository. Existing Syntarus public
API/SDK endpoints remain available independently and must not be broken by the
Smara cutover.

### New Smara runtime boundary

Create a provider-neutral runtime in Smara with no MemoryOS imports:

```text
packages/agent-runtime/
  runtime                one bounded agent/task-step reasoning loop
  provider-router        configured LLM/provider selection and fallback
  tool-registry          allowed tools and schemas
  context-builder        prompt, task, artifact, and memory assembly
  policies               approval, capability, cost, and time policies
  streaming              normalized token/event output
  evaluation             repeatable task and safety evaluations

packages/syntarus-adapter/
  implements MemoryPort through the published Syntarus SDK only
```

The runtime depends on stable interfaces (`MemoryPort`, `TaskPort`,
`ToolPort`, `EventPort`, and `ArtifactPort`) rather than concrete databases or
MemoryOS modules.

### Feature extraction sequence

Port Memento behavior in tested slices, never as one large migration:

1. streamed direct chat and provider/model configuration;
2. scoped memory retrieval through `syntarus-adapter`;
3. verified memory write-back through `syntarus-adapter`;
4. provider-neutral tool registry and safe built-in tools;
5. cited research/evidence workflow and artifacts;
6. approvals, task graph execution, retries, and cancellation;
7. Gmail, Calendar, Telegram, and other integration adapters;
8. attachments, voice, and multimodal processing;
9. scheduler/proactive delivery and desktop executor routing.

For each slice, add contract tests and behavior comparisons before moving to
the next one. The source Memento implementation remains unchanged while the
equivalent Smara slice is being proven.

### Identity and existing memory continuity

Syntarus memories are already keyed by Memento account IDs. During migration:

- export only the account identity records required by Smara;
- preserve existing `account_id` values exactly;
- issue new Smara sessions against those same account IDs;
- allow a fresh sign-in at cutover rather than attempting to preserve old
  browser session cookies; and
- do not copy/rewrite memory contents, Qdrant payloads, or Neo4j records.

Preserving the account ID allows the new Smara agent to retrieve the same
existing Syntarus memory without altering the sensitive memory pipeline.

### Shadow testing and cutover

Before switching public traffic, use shadow mode for selected internal/beta
accounts:

```text
incoming request
  -> existing Memento produces the real response
  -> Smara receives a safe shadow copy
       -> compare context retrieval, tool plan, result quality, latency, cost
       -> never perform external actions in shadow mode
```

Then perform a controlled beta cutover of `ai.syntarus.com` to Smara Web and
Smara API. Keep the previous Memento deployment available for immediate
rollback until the new chat, memory, tasks, approvals, integrations, and
desktop path meet release gates.

Only after the cutover is stable:

1. retire the separate Control iframe and user-facing Control site;
2. retain task-control services internally where they remain useful;
3. remove Memento agent routes/code from MemoryOS in a separate, reviewed
   change; and
4. keep all Syntarus API/SDK compatibility guarantees intact.

### First implementation milestone after current work

Build the Smara CLI as a thin authenticated client of the new Smara API and
task graph. It is the primary agent test harness and power-user interface:

```text
smara login
smara ask "..."
smara run "..."
smara tasks list
smara task watch <task-id>
smara task approve <approval-id>
smara task cancel <task-id>
smara desktop pair
```

The CLI must contain no independent memory database or agent loop. A task
started through CLI must remain visible and resumable through Smara Web, Phone,
and Desktop after the terminal is closed.

### 2026-08-22 — Memento extraction slice 1: independent runtime boundary and CLI

Started the Memento-to-Smara migration without copying MemoryOS imports:

- added `smara.agent_runtime`, a small provider-neutral direct-conversation
  runtime modelled after the safe retrieve-then-answer boundary of Memento;
- added an OpenAI-compatible provider adapter, configured only through Smara
  deployment environment variables, so Groq/xAI/OpenAI-compatible providers
  can be selected without changing the task system;
- extended the existing Syntarus-only adapter with conversation context lookup
  and explicit client cleanup; no Qdrant, Neo4j, Redis, or MemoryOS module is
  imported by the new runtime;
- added `POST /v1/chat` for bounded direct chat. It fails clearly when no
  provider is configured and tells the runtime to avoid claiming external work;
- added the installable `smara` CLI with `ask`, `run`, `tasks`, `task show`,
  `task watch`, `task approve`, `task deny`, and `task cancel`; and
- documented that the CLI is a thin HTTP client of the hosted API/task graph,
  never a second local agent or memory store.

Verified: three focused runtime/CLI tests pass, Python compilation passes, and
the CLI command help renders. The full repository test suite could not run in
the shared local virtual environment because it has an incompatible preexisting
FastAPI/Starlette pair; the repository Docker verification is pending Docker
Desktop being started. This is an environment issue, not treated as a passing
full-suite result.

Next: add authenticated CLI device/login flow through the existing Smara/Memento
identity bridge, then extract Memento's provider error classification and
streaming event contract into Smara. Only after those are tested should the
tool registry and Memento research behavior be ported.

### 2026-08-22 — Memento extraction slice 2: safe streaming and provider errors

Reused two proven Memento contracts without importing Memento infrastructure:

- added provider-neutral error classification with stable client-facing kinds
  (`invalid_key`, `no_credits`, `rate_limit`, `timeout`, `provider_down`, and
  related cases); raw upstream errors are never sent to clients;
- added a safe Server-Sent Event contract (`phase`, `status`, `token`, `done`,
  `error`) for Smara direct chat. It deliberately exposes operational progress
  but not chain-of-thought; and
- added `POST /v1/chat/stream`. When no provider is configured it returns an
  explicit `not_configured` event rather than an ambiguous failure or a hidden
  fallback.

Verified: Docker Desktop Compose stack rebuilt with API, worker, scheduler,
integration worker, Postgres, and Redis healthy. A real durable task completed
through API and worker with four stored events. The real stream endpoint
returned correct SSE frames and safe `not_configured` handling. The complete
Smara test suite ran inside a disposable clean production-image container:
**33 passed**. The only warning was pytest cache permission on the bind mount.

Next: authenticated CLI device/login flow using a new, narrowly scoped
short-lived device-code/token contract that Memento can mint during the
transition. Then extract the first Memento tool family (web research and source
retrieval) into Smara's provider-neutral tool registry and connect it to the
existing evidence-ledger task graph.

### 2026-08-22 — Memento extraction slice 3: authenticated CLI pairing

Implemented the CLI identity bridge without moving MemoryOS authentication
internals into Smara:

- added durable, account-scoped CLI pairing codes with a ten-minute lifetime;
- stores only a SHA-256 hash of each pairing code and marks it consumed before
  issuing a token, so a code cannot be replayed;
- added `POST /v1/cli/device/start`, called by an already authenticated
  Smara/Memento Web session during the transition;
- added `POST /v1/cli/device/exchange`, which returns a separate `smara-cli`
  audience JWT signed by its own secret; and
- added `smara login <one-time-code>` to the thin CLI client.

The CLI bearer is accepted only with the `smara-cli` audience and `smara-api`
issuer. It is not accepted as a browser bridge token, and browser/gateway
secrets are not reused. The deployment must configure
`SMARA_CLI_TOKEN_SECRET` through its secret manager before enabling the flow.

Verified: migration 012 is packaged and applied to live Compose Postgres; an
isolated production-mode API test passed from signed gateway assertion through
one-time pairing, token exchange, and authenticated task listing; all services
remain healthy; and the complete clean-container suite passes **35 tests**.

Next: expose the pairing start action from the existing authenticated Smara
Web/Memento settings surface, then port Memento's first tool family—web search,
safe URL retrieval, source verification, and citations—into Smara's tool
registry and research task graph.

### 2026-08-22 — Memento extraction slice 4: Smara Web CLI pairing action

Added the first user-facing entry point for the CLI to the existing Smara Web
surface. The account area now opens a short-lived one-time code from
`POST /v1/cli/device/start` and explains the safe handoff:
`smara login <code>`. The code is displayed once, is never copied into a URL,
and does not expose the browser session or signing secrets. This keeps the
control experience in Smara Web rather than creating another control product.

Verified: the static Web bundle contains the pairing action and dialog, Node
syntax validation passes, the rebuilt Compose stack is healthy, a live Web/API
smoke request returns a pairing code, and the complete disposable-container
suite passes **35 tests** (only the existing two short-test-key JWT warnings and
the bind-mounted pytest-cache warning remain). The deployment still needs a
real `SMARA_CLI_TOKEN_SECRET` in its secret manager before code exchange is
enabled; the current development `.env` intentionally leaves it blank.

Next: add the first independent research tool family (web search, safe URL
retrieval, source verification, and citations) behind Smara's provider-neutral
tool boundary, then connect it to the existing research evidence ledger without
importing Memento or MemoryOS modules.

### 2026-08-22 — Memento extraction slice 5: provider-neutral research discovery

Moved the first research capability behind Smara-owned boundaries:

- added a small `research_tools` registry with Brave and Serper HTTP adapters;
- added bounded public-URL retrieval as a reusable tool while preserving the
  existing SSRF, redirect, content-type, size, excerpt, and hash checks;
- allowed `POST /v1/research` to omit source URLs, which adds a durable
  `research.discover_sources` graph step before fetch, verification, and report;
- deduplicated discovered URLs into the existing evidence ledger, so every
  source still receives a status, content hash, claim, confidence, and citation;
- kept search credentials server-side and documented the four search settings;
  no provider key is returned to clients or written to Syntarus memory; and
- added focused adapter, missing-configuration, and discovery-graph tests.

Verified: images rebuilt, all Compose services healthy, live no-URL research
task created the four-step discovery graph and was cancelled safely, and the
complete clean-container suite passes **38 tests**. The only warnings are the
pre-existing short test JWT key and bind-mounted pytest-cache warnings. A real
search provider key is still required for production discovery; explicit source
URL research remains available without one.

Next: expose research progress/evidence more clearly in the Web and CLI, then
add source-level verification rules (publication date, domain policy, and
cross-source agreement) before any LLM synthesis is introduced.

### 2026-08-22 — Memento extraction slice 6: evidence quality and operator views

Completed the next research safety layer without adding LLM synthesis:

- added migration `013_research_quality.sql` and matching SQLite upgrades for
  publication date, domain policy, quality flags, agreement count, and notes;
- added deterministic checks before evidence is verified: HTTPS signal,
  optional allow/block domain policy, publication-date signal, and token-level
  cross-source agreement;
- kept weak evidence visible and usable only as explicitly flagged verified
  evidence—no silent confidence inflation and no hidden claim generation;
- expanded the Smara Web evidence ledger with safe source links, status,
  publication date, domain policy, agreement count, quality flags, and errors;
- added CLI `research` and `task evidence` commands, and included the ledger in
  `task show`; and
- added regression coverage for publication metadata, agreement scoring, and
  the enriched API evidence view.

Verified: migration 013 applied to live Postgres, the enriched evidence API
serialized correctly, the CLI command is installed, all Compose services are
healthy, and the clean-container suite passes **39 tests**. Only the existing
short-test-key JWT warnings and bind-mounted pytest-cache warning remain.

Next: add explicit research progress events/SSE so Web and CLI can show each
discovery, fetch, and verification transition live, then introduce bounded LLM
synthesis only after those source states are complete.

### 2026-08-22 — Memento extraction slice 7: Tavily verification and live task progress

Completed the next slice without changing the Syntarus/MemoryOS core:

- added a provider-neutral Tavily search adapter beside Brave and Serper;
  its API key is read only by the server-side research tool and is never written
  to source, tests, browser state, task payloads, or memory;
- made the search URL optional so each supported provider uses its official
  endpoint by default while explicit deployments may still override it;
- added `GET /v1/tasks/{task_id}/events/stream`, an account-scoped SSE view of
  the durable task event ledger with keepalives and a terminal `done` frame;
- changed CLI `task watch` to consume the same SSE contract and added a Web
  listener that shows live task-update notices while the existing detail view
  refreshes normally; and
- kept the existing durable event endpoint unchanged for polling clients.

Verified: the clean Docker-mounted suite passes **40 tests**; the supplied
Tavily key produced three live results from the disposable API container and
was not persisted; rebuilt API/worker images and all Compose services are
healthy; an actual Postgres-backed worker task emitted `task.created`,
`task.started`, `step.completed`, `task.completed`, and the final SSE `done`
frame; and the API/worker logs contained no errors.

Next: add reconnect/resume semantics (`Last-Event-ID`) and richer Web event
rendering, then introduce bounded LLM synthesis only after research evidence
states are complete. No LLM provider key is required for the current search,
retrieval, verification, or event-stream work.

### 2026-08-22 — Memento extraction slice 8: resumable task event streams

Hardened the live progress contract:

- SSE task updates now carry the durable event ID in an `id:` field;
- clients may send `Last-Event-ID`, and the API resumes after that event while
  preserving account ownership checks; and
- the Web listener remembers the last event per selected task and resumes after
  a transient disconnect without changing the polling fallback.

This remains a progress-only channel: it exposes task/step state and never
streams model chain-of-thought, provider credentials, or raw upstream errors.

### 2026-08-22 — Memento extraction slice 9: bounded evidence-only synthesis

Added optional report synthesis after deterministic evidence verification:

- the worker can use the configured OpenAI-compatible provider only when
  `SMARA_RESEARCH_SYNTHESIS_ENABLED=true`;
- the prompt receives verified excerpts and citation labels only, with bounded
  input/output size, zero temperature, and no provider-side tool access;
- returned citations are validated against the evidence ledger and unknown or
  missing citations are rejected; and
- provider failures, invalid output, missing configuration, or disabled mode
  produce the existing deterministic cited report and a durable fallback/skip
  event instead of losing the task.

No LLM key is required for the default behavior or the test suite. A real
provider key is only needed to exercise the optional synthesis path live.

### 2026-08-22 — Memento extraction slice 10: safe provider-neutral tool registry

Added the first Smara-owned tool boundary without importing Memento or
MemoryOS internals:

- defined tool specs, bounded results, account/workspace context, and an
  approval-aware registry;
- registered only read-only UTC time, AST-validated arithmetic, provider-
  neutral research search, and SSRF-safe URL retrieval;
- added authenticated `GET /v1/tools` discovery and `POST /v1/tools/{name}`
  invocation for this safe subset; and
- rejected unknown arguments and all future side-effecting/approval tools from
  direct invocation, preserving the durable task graph as the only action path.

Verified: the clean Docker suite passes **46 tests**, calculator code execution
is rejected, fetch results remain bounded and cited, and the live API exposes
the four safe tools without requiring an LLM provider.

Remaining next: connect tool selection to a bounded agent-step runtime, then
add approval-backed integration tools and desktop routing. The research live
provider and optional synthesis remain separately configuration-gated.

### 2026-08-22 — Memento extraction slice 11: bounded agent-step runtime and CLI parity

Connected the safe registry to one hosted `agent.execute` task step:

- the runtime gives the provider a fixed tool manifest and allows at most three
  model/tool turns plus one bounded final response;
- only registered read-only tools can be selected; tool requests and bounded
  result previews become durable task events without exposing credentials;
- invalid model decisions, empty answers, missing providers, and tool failures
  stop safely rather than granting implicit shell/browser access; and
- the CLI now lists/invokes safe tools and can list or create desktop executor
  pairings, while remaining a thin authenticated hosted client.

Verified: runtime contract tests and the clean Docker suite pass; the live API
tool/catalog path remains healthy. Live model-backed `agent.execute` still
needs a configured OpenAI-compatible provider key; without one it fails
explicitly and does not perform work.

Remaining: approval-backed integration tool execution, a persistent desktop
executor client/routing, and live model-provider verification. The overall
architecture is in place, but those capabilities are not yet complete.

### 2026-08-22 — Memento extraction slice 12: CLI completion hardening

Completed the CLI milestone itself:

- added the documented `tasks list` form while preserving `tasks` shorthand;
- `login` now saves the scoped bearer locally without printing it by default,
  with explicit `--print-token` and `logout` controls;
- `task watch` reconnects up to three times and resumes with `Last-Event-ID`;
- parser coverage now includes tools, desktop pairing, token lifecycle, and
  the documented task commands; and
- the CLI remains a thin HTTP client with no local task, memory, or agent loop.

The CLI control surface is complete for the current hosted contracts. A live
Grok/OpenAI-compatible key is **not** needed to test CLI task creation,
research, watch, approvals, tools, or desktop pairing. It is needed only for
live `smara ask` and model-backed `agent.execute` verification.

### 2026-08-22 — Desktop executor and integration-selection slice

Implemented the next missing agent slice in the independent Smara repository:

- added the persistent outbound-only `smara-desktop` process with secure local
  state, one-time pairing, heartbeat, lease polling, bounded backoff, and
  graceful credential rejection;
- added explicit local capabilities for file read, atomic file write,
  allowlisted terminal execution, and allowlisted browser opening;
- rejected undeclared capabilities, unapproved task steps, path traversal,
  symlink escapes, shell operators, oversized files, secret-like environment
  variables, and unbounded command time;
- added a server-side Postgres/SQLite claim guard so desktop workers cannot
  lease a task before its approval gate has been cleared;
- connected read-only Gmail search, Calendar listing, Drive metadata search,
  and GitHub repository listing to bounded model tool selection. External
  writes remain durable integration intents and keep the existing approval
  worker path; the model can create an approval intent but cannot execute it;
  and
- updated the native Smara Web device/memory screens to use `smara-desktop`
  and state plainly that Syntarus metadata filters are not security isolation
  until the hosted Memory API enforces them.

Verified: clean Docker suite passes **58 tests**; API/worker health is good;
the live tool catalog exposes the read-only integration tools; and a
disposable Grok `grok-3-mini` task selected `calculate`, recorded tool events,
and completed with result `133`. The Grok key was supplied only as a transient
worker environment variable and was not written to source, `.env`, logs, or
task data.

Still pending outside this slice: a full local desktop end-to-end smoke task
against a real paired Windows process, production gateway/WAF and secret
manager configuration, off-host backup/restore and Sentry drills, native
cutover of the existing `ai.syntarus.com` Web, and Syntarus server-side
metadata-filter enforcement. The Web task/control screens in this repository
are already native; the public cutover is a deployment step, not a MemoryOS
pipeline change.

### 2026-08-22 — Migration and Web reconnection slice

Started the remaining rollout/migration work without moving any MemoryOS data:

- added the packaged `smara-migrate` command; it reports applied/pending
  Postgres schema versions and applies only pending migrations with
  `--apply`, without printing database credentials;
- kept the existing migration boundary explicit so the Smara cutover can be
  rolled back to the previous deployment without rewriting Syntarus memories;
- made the native Web task event stream reconnect with bounded exponential
  backoff and `Last-Event-ID` replay after network drops; task details refresh
  as durable events arrive while the existing periodic refresh remains a
  fallback; and
- verified the clean Docker suite again: **58 tests passed**, the migration CLI
  is present in the production image, and API/worker services restarted cleanly.

Remaining migration work is deployment-level: run the command against the
target production Postgres, shadow-test selected existing accounts, then
perform a reversible beta cutover of `ai.syntarus.com`. No account IDs or
Syntarus memory records should be rewritten.

### 2026-08-22 — Durable schedules and proactive task runs

Implemented the next Memento-to-Smara migration slice for proactive work:

- added Postgres migration `014_schedules.sql` plus the equivalent SQLite
  development schema;
- added account-scoped schedule create/list/stop APIs with bounded fixed
  intervals (one minute through thirty days);
- added an atomic scheduler that creates ordinary task-graph runs under
  Postgres `FOR UPDATE SKIP LOCKED` and SQLite transaction locking, so two
  scheduler replicas cannot create the same run;
- preserved task approvals: a schedule can require approval on every run, and
  the scheduler never executes tools or provider actions itself;
- added native Web schedule controls for creation, next-run visibility, and
  stopping a schedule; and
- verified a disposable due schedule created a real Postgres-backed task run.

The clean Docker suite now passes **60 tests**. The remaining proactive work is
delivery channels (web push/Telegram when configured), recurrence policy beyond
fixed intervals, and production VM rollout.

### 2026-08-22 — Scheduled Web Push notification hook

Added the first delivery-channel hook for proactive runs. When Web Push/VAPID
is configured, the scheduler sends an account-scoped notification after it
creates a scheduled task; when push is not configured or delivery fails, the
task remains durable and execution continues. Notification work runs outside
the scheduler's database transaction and never bypasses task approval.

The clean suite remains **60 tests passed** and the rebuilt scheduler starts
without errors. Telegram delivery and production VAPID configuration remain
deployment/provider work.

### 2026-08-22 — Safe capture processing boundary

Completed the next repository-owned Memento migration slice:

- changed phone/web captures to keep media only in the account-scoped artifact;
  task objectives no longer contain base64 media and the worker runs a normal
  `capture.process` task-graph step;
- added optional, independently configured OpenAI-compatible transcription and
  vision adapters with strict 10 MB voice / 4 MB image bounds, bounded output,
  and no provider-key reuse;
- added transcript/description artifacts when a provider is configured, while
  unconfigured deployments complete locally with an explicit safe status rather
  than failing or silently sending media anywhere;
- recorded bounded step results in the durable event stream and corrected
  artifact-created events to report the actual artifact kind; and
- verified the full Docker repository suite (**63 passed, 3 warnings**), rebuilt
  and restarted API/worker/scheduler, then created a live voice capture through
  the API and confirmed the Postgres-backed worker completed it without errors.

Remaining work is deployment/provider configuration (if transcription or vision
is desired), real Windows desktop smoke, Telegram delivery, recurrence policy,
production gateway/secrets/backups/Sentry, reversible `ai.syntarus.com` cutover,
and Syntarus server-side metadata enforcement. MemoryOS source and schema were
not modified.

### 2026-08-22 — Direct agent tool-loop parity

Promoted the already-tested bounded task-step runtime into direct Smara chat:

- `/v1/chat` and `/v1/chat/stream` now use the same provider-neutral,
  max-three-turn tool loop as hosted `agent.execute` steps;
- direct chat can use only bounded read-only tools and configured read-only
  integration adapters, while every write, local action, artifact-producing
  workflow, and approval remains a durable task;
- SSE now reports `tool_call` and bounded `tool_result` frames and includes the
  tool count in `done`, without exposing chain-of-thought or raw provider
  errors; and
- added regression coverage for model tool selection and final-answer
  behavior.

Focused agent/control tests pass (**17 passed**). The full-suite and rebuilt
service verification is the release gate for this slice. Remaining full-Memento
replacement work is still production identity/session rollout, Telegram channel
worker, recurrence/delivery policy, Windows desktop smoke/installer, external
gateway/WAF/secrets/backups/Sentry drills, Syntarus server-side filter
enforcement, and the reversible `ai.syntarus.com` cutover.

### 2026-08-22 — Staging migration race fix

The first real VM staging restart exposed a concurrency bug: API, worker,
scheduler, and integration-worker all initialise the Postgres store, and two
containers could apply/record the same migration at once. The migration runner
now takes a Postgres advisory lock for the migration section, rechecks each
version while holding that lock, and uses an idempotent version insert. This is
safe for rolling restarts and multiple replicas without changing task or memory
data.

The local Docker suite remains **64 passed, 3 warnings**. The fix is ready for a
second staging restart; the first restart was intentionally stopped before any
production-domain change.

### 2026-08-22 — Staging deployment verification

Pulled the verified `9a89dd4` release into the VM's existing
`smara-staging` Compose project and rebuilt it under the original project name
(avoiding a duplicate stack/port collision). The staging release now has all
API, worker, scheduler, integration-worker, Postgres, and Redis containers
running. Public checks passed for `/health` and `/app/`, and an in-container
signed gateway probe successfully authenticated an account and listed the
read-only tool catalogue.

The production `ai.syntarus.com` MemoryOS/Memento stack was not changed. The
next cutover gate is capability shadow-testing against that domain, followed by
a reversible proxy switch only after authentication, chat, tasks, research,
approvals, schedules, memory, and rollback checks pass.

### 2026-08-22 — Production gate verification

Completed the safe staging-side production gates without touching the live
`ai.syntarus.com` application:

- confirmed the migration-lock release is clean across API, worker, scheduler,
  and integration-worker logs;
- verified staging health/readiness and signed gateway authentication;
- created a custom-format Postgres backup, verified its checksum and archive
  readability, and restored it into a disposable Postgres 16 container with all
  14 Smara schema versions present; and
- verified the public staging Cloudflare/Caddy/TLS/security-header path and
  Redis-backed distributed limiter configuration.

Added `scripts/check-production-config.sh` and `PRODUCTION_GATE_STATUS.md` so
production cannot be marked ready while secrets or recovery operations are
missing. The full gate currently remains blocked by deployment-owned values:
CLI signing secret, integration key ring, Sentry DSN, VAPID keys, a real secret
manager, an encrypted off-host backup target, and an authenticated Cloudflare
WAF/rate-limit review. Syntarus server-side metadata-filter enforcement also
remains advisory and is not claimed as secure. No MemoryOS source/schema/data
was changed.

The production checker is copied into the service image so the same artifact
used for deployment can run `./scripts/check-production-config.sh --full`.

### 2026-08-22 — First hosted-model smoke test

Staging had the literal template text
`<a Grok model enabled for your account>` in `SMARA_LLM_MODEL`; it was replaced
with the previously tested low-cost `grok-3-mini` value. A disposable agent task
then exposed an SDK-version compatibility issue: the installed Syntarus SDK did
not accept the optional `filters` keyword. Smara now retries the same
account-scoped search without advisory metadata filters when an older SDK
signature is detected; no MemoryOS code or pipeline was changed. The clean
suite passes **65 tests**.

The next smoke task reached the xAI provider but was rejected with
`Project API key was rejected`. The remaining blocker is therefore the staging
provider credential (invalid/expired/wrong project or model access), not the
Smara task graph or model wiring. Do not copy a new key into Git or send it in
chat; replace it only in the VM secret environment and restart Smara services.

The replacement xAI key was verified transiently against `/v1/models` and the
exact chat-completions request returned HTTP 200. A disposable hosted Smara
task then completed with `grok-3-mini`, including `agent.tool_requested`,
`agent.tool_completed`, and `task.completed` events. The separate Syntarus
memory project key still reports an unavailable-memory event; the worker now
continues the task and records that condition instead of retrying/failing the
agent. MemoryOS remains untouched and metadata filtering remains advisory.

### 2026-08-22 — Hosted agent plus Syntarus memory verification

The staging VM was updated with the replacement xAI credential and a
Syntarus project key in its protected `.env` file; no secret was added to Git.
The xAI `/v1/models` and chat-completions probes returned successfully.

A read-only disposable Syntarus SDK search passed inside the deployed API
container. A second disposable Smara task completed end-to-end using the
shared task graph and calculator tool. Its durable event sequence was:
`task.created`, `task.started`, `agent.tool_requested`,
`agent.tool_completed`, `step.completed`, and `task.completed`. The task had
no `memory.unavailable` event, confirming hosted memory retrieval/write-back
and agent execution are working together on staging.

This verifies the current hosted milestone without changing MemoryOS source,
schemas, or production data. Metadata filters remain advisory until the
Syntarus server enforces them. The next live check is CLI pairing/login and
watching a task against this same hosted API; research-provider credentials and
the remaining production hardening gates are still separate work.

### 2026-08-22 — CLI pairing and durable watch verification

The disposable CLI flow passed against the private staging API: a signed
gateway assertion created a one-time pairing code, the CLI exchanged it and
stored the bearer locally without printing it, authenticated task listing
worked, and a calculator task completed through the same hosted worker. The
CLI then resumed the durable SSE stream and observed the full event sequence
through `task.completed`.

The public Cloudflare edge currently returns HTTP 403/1010 for the pairing
POST, while the VM-local API accepts the same signed request. This is a WAF/
edge policy issue to resolve before public CLI pairing is called production
ready; no application authentication bypass was added. The temporary smoke
account/task are disposable staging data.
