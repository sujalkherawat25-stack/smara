# Smara Implementation Plan

**Status date:** 2026-08-26
**Repository:** `sujalkherawat25-stack/smara`
**Public target:** `https://ai.syntarus.com`

## 1. Scope reset

Smara has one focused job: be a dependable personal agent in two forms.

1. **Hosted Smara** — the web app and CLI use one server-side agent, task
   graph, approval system, and Syntarus memory account. Hosted work continues
   when the user's computer is closed.
2. **Smara Desktop Executor** — an optional paired desktop app runs approved
   terminal, browser, and file actions on the user's PC. It does not contain a
   second agent brain or memory database.

The target is a reliable agent, not a collection of loosely connected demos.
Research, citations, scheduling, and integrations are capabilities of this
agent only when they are reliable and fit the two surfaces above.

## 2. Architecture and boundaries

```text
ai.syntarus.com
  Smara Web + hosted agent
  Smara CLI (hosted client)
          |
          v
smara repository
  agent runtime + task graph + approvals + research + workers + API
  desktop pairing/executor contracts
          |
          | public Syntarus SDK/API only
          v
memoryos repository
  Syntarus Memory API/SDK and memory workers
```

MemoryOS remains the memory product. Its Qdrant, Neo4j, Redis memory state,
schemas, Continuum APIs, and core pipeline are protected and are not changed
to make Smara work. Smara uses the public Syntarus SDK/API through one adapter.

The Memento agent implementation remains available in MemoryOS for reference
and rollback. We will **port/adapt its proven agent behavior** (triage,
ReAct/tool selection, streaming, memory context, and approval pauses) into the
Smara runtime. We will not copy Memento's backend imports, storage clients, or
duplicate memory pipeline.

## 3. In-scope release capabilities

### Hosted Smara

- Memento-quality chat and conversation history, implemented against Smara API.
- Provider-neutral LLM configuration (OpenAI-compatible providers, including
  Grok where configured) with clear error reporting.
- Agent loop: inexpensive triage, memory retrieval, bounded tool/reason steps,
  streamed answer, and non-blocking memory write-back.
- Durable tasks represented by a dependency graph with leases, retries,
  idempotency, cancellation, durable events, and dead-letter recovery.
- Approval-first execution for external writes and other risky actions.
- Reliable hosted research: public source retrieval, SSRF protection, source
  verification, evidence ledger, citations, and report artifacts.
- Schedules/reminders only after restart/retry behavior is verified.
- Account authentication and strict account-scoped task and memory access.
- One native UI for chat, work/tasks, research/evidence, approvals, settings,
  and connected desktop devices. No second Control product is required.

### Smara CLI

- Hosted login/device pairing, chat, task creation, task watching, approvals,
  research, tools, and reconnect/error handling.
- Same account and task graph as the web app; no local agent or memory store.

### Smara Desktop Executor

- Pairing and capability declaration.
- Approval-gated terminal, browser, and file operations using explicit
  allowlists.
- Lease/heartbeat, reconnect, cancellation, revoke, retry, and duplicate
  prevention.
- Visible pause/revoke state and bounded rotating logs.
- Local execution is never silently enabled by a hosted request.

## 4. Explicitly deferred (do not count these as release blockers)

These are backlog items, not part of the focused replacement release:

- Phone/PWA client and phone push/capture.
- Telegram, WhatsApp, Gmail, Calendar, Drive, and GitHub channels/actions.
- Billing/subscriptions and a plugin marketplace.
- Graph visualizers, experimental visual/hologram labs, and extra dashboards.
- Broad proactive automation or a separate Control PWA.
- Arbitrary code execution or unrestricted browser automation.
- Secure metadata-filter claims until the hosted Syntarus API enforces them.

Existing experimental code may stay behind feature flags for rollback, but it
must not appear in the main navigation or be used as a release criterion. The
Hologram Lab has already been removed from the Smara UI and source.

## 5. What is already implemented

- Independent Smara repository with API, Postgres, Redis, worker, scheduler,
  integration-worker, Docker deployment, and public health/readiness routes.
- Task graph, durable events, leases, retries, cancellation, approvals,
  idempotency, schedules, and dead-letter handling.
- Provider-neutral hosted runtime and live Grok/provider error checks.
- Tavily-backed research with bounded retrieval, verification, evidence,
  citations, and artifacts.
- Syntarus SDK memory retrieval/write-back adapter; MemoryOS core untouched.
- CLI beta foundation using the hosted API.
- Desktop pairing/executor foundation with allowlists and safety contracts.
- Reversible `/smara/` and `/smara-api/` staging mount at `ai.syntarus.com`.
- Local suite (95 Smara backend tests), frontend type-check, VM Docker build,
  live health checks, and disposable account/task/research/approval smoke test.

## 6. Remaining work, in order

### P0 — Make Smara Web one native product

1. **Mostly done:** adapt the Memento agent-loop behavior into Smara's
   provider-neutral runtime. Deterministic triage, bounded tool reasoning,
   streaming, memory context, and phase events are live. Remaining: exercise
   the real configured model against representative tool prompts and verify
   approval pauses through the hosted API.
2. **In progress:** native Smara calls now cover chat streaming, hosted
   conversation history/recents, durable tasks/events, research evidence,
   approvals, schedules, and executor settings. Remaining: authenticated
   browser shadow tests across refresh, reconnect, and account isolation.
3. **Done for the focused shell:** the temporary Control iframe and duplicate
   navigation are no longer mounted in Smara mode. Legacy components remain in
   source only as a rollback path until the shadow run is accepted.
4. **Pending:** run the authenticated browser shadow suite with a real beta
   account. Keep the current root Memento route unchanged until it passes.

### P1 — Make the desktop executor dependable

1. Test PC restart, disconnect/reconnect, lease expiry, cancellation,
   capability denial, revoke, retries, and duplicate prevention on Windows.
2. Package a signed installer with safe update, auto-start opt-in, bounded
   logs, visible pause/revoke, and clear pairing recovery.
3. Connect only approved terminal/browser/file steps to an isolated sandbox;
   unrestricted arbitrary code is not a production capability.

### P2 — Production readiness and cutover

1. Put provider, signing, database, and sandbox secrets in a real VM secret
   manager; perform a rotation drill.
2. Finish encrypted off-host backups and a disposable restore drill.
3. Configure Sentry/structured logs, authenticated distributed rate limits,
   per-task cost/time/output/resource budgets, and artifact retention.
4. Run security, fault-injection, restart, concurrency, isolation, and
   rollback tests.
5. Shadow Smara against Memento for selected beta accounts (no side effects),
   compare answer/tool/task/safety/latency/cost results, then route one cohort
   to Smara and rehearse rollback.
6. After the cohort is stable, make Smara the public agent at
   `ai.syntarus.com`. Keep Memento source/deployment available for rollback,
   but stop serving it publicly. MemoryOS continues as the Syntarus memory
   backend/product.

## 7. Honest completion estimate

For the **focused hosted + desktop vision** (not the deferred backlog):

| Area | Approx. complete | Main gap |
|---|---:|---|
| Hosted agent/task/research runtime | 80% | Memento behavior parity and edge cases |
| CLI hosted client | 85% | parity polish and authenticated workflow tests |
| Native Smara Web | 70% | authenticated shadow tests and final legacy cleanup |
| Desktop local executor | 70% | Windows reliability and packaging |
| Production operations/cutover | 45% | secrets, observability, shadow run, rollback |

**Focused beta:** roughly 70% complete.
**Public replacement ready:** roughly 55–60% complete until P0–P2 pass.

These are readiness estimates, not lines-of-code measurements. The main risk
is product consolidation and verification, not rewriting MemoryOS.

## 8. Definition of done

Smara can replace the public Memento agent when:

- Web and CLI use the same hosted agent, account, memory boundary, and task
  graph.
- Chat can become a visible task with plan, live events, approvals, evidence,
  artifacts, cancellation, and retry-safe recovery.
- Research produces verified sources and cited output.
- The paired desktop can execute only approved local actions and survives
  disconnects/restarts without duplicates.
- No user-facing screen depends on the temporary Control iframe or an
  unadapted legacy Memento endpoint.
- Authentication, secrets, backups, alerts, rate limits, budgets, sandbox
  gates, shadow results, and rollback are verified in staging.
- MemoryOS core behavior is unchanged; Syntarus memory continuity is proven
  through the public SDK/API.

## 9. Next implementation sequence

1. Freeze this focused scope and hide deferred navigation/features.
2. Port/adapt Memento agent-loop behavior into Smara and add parity tests.
3. Finish native Smara Web panels and remove the Control iframe.
4. Run authenticated staging workflows for chat, task, research, approval,
   reconnect, and account isolation.
5. Harden/package the Windows desktop executor and run failure tests.
6. Complete production gates, shadow evaluation, beta cutover, and rollback
   rehearsal.

## 10. Historical implementation log

The detailed historical entries below are retained for traceability; they are
not additional scope. New entries should record only work that advances the
focused hosted/desktop release.

### 2026-08-26 — Staging mount and UI cleanup

- Added the UI-only Memento shell adaptation under `smara/frontend/` and the
  Smara gateway client; no MemoryOS backend or storage code was copied.
- Added native Smara Work flows for tasks, events, approvals, cancellation,
  research evidence, citations, and artifacts.
- Persisted `/smara/` and `/smara-api/` in Caddy while preserving the existing
  root Memento route on its live port. Rechecked root and Smara health routes.
- Removed the experimental Hologram/visual lab from source, routes, and menus.
- Verified frontend type-check, Smara backend tests, VM Docker build, public
  health/readiness, and disposable account-scoped task/research workflows.
- Authenticated browser shadow tests and final public cutover remain pending.

### 2026-08-26 — Hosted bridge and conversation continuity

- Fixed the browser-to-Smara session bridge to use the backend's authenticated
  `POST /v1/auth/control-token` contract; added one safe token refresh when a
  deploy or rotation invalidates a cached assertion.
- Smara-mode API calls now consistently use the bridge client, including
  executor settings and durable work actions. A successful 204 is handled
  without attempting to parse an empty JSON body.
- Reconciled the native chat recents with Smara's hosted conversation index,
  loaded remote turns when a conversation is opened, and removed conversations
  from the hosted store when the user deletes them. Local drafts remain safe
  during a temporary outage.
- Added deterministic Memento-style triage/phase events to the Smara runtime
  while keeping reasoning bounded and not exposing chain-of-thought.
- Local verification remains green (95 backend tests, frontend type-check).
  Public health and route smoke checks pass; authenticated browser shadow
  testing, VM rebuild of this commit, and the final root cutover remain gates.
