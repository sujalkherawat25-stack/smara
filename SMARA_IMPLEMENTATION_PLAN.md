# Smara Implementation Plan

**Status date:** 2026-08-26
**Repository:** `sujalkherawat25-stack/smara`
**Public target:** `ai.syntarus.com`

## 1. Product vision

Smara is one personal agent with one durable task brain and several ways to
use it:

```text
Memento-quality Web UI at ai.syntarus.com
CLI for fast terminal work
Desktop executor for approved local PC actions
Phone for capture, notifications, and approvals
Telegram / Gmail / Calendar / GitHub / Drive / WhatsApp over time
```

The agent must be able to research, plan, execute, remember, schedule, and ask
for approval before risky or external actions.

The success scenario is:

> Ask Smara to research a topic, produce a cited report, update an approved
> local file, schedule a follow-up, and ask before sending anything externally.

## 2. Final architecture

```text
ai.syntarus.com
  Smara Web (reused Memento UI, adapted to Smara APIs)
        |
        v
Smara repository
  API + agent runtime + task worker + scheduler + integrations
  CLI + desktop executor + phone/notification contracts
        |
        | public Syntarus SDK/API only
        v
MemoryOS repository
  Syntarus Memory API + SDK + memory workers
```

### MemoryOS boundary

MemoryOS remains the Syntarus memory product. Its memory pipeline, Qdrant,
Neo4j, Redis memory state, schemas, and Continuum APIs must not be modified to
make Smara work.

The Memento source remains in MemoryOS for reference and rollback, but Memento
will no longer serve the public agent at `ai.syntarus.com` after cutover.

### UI decision

Do not build a new Smara UI from scratch and do not copy the Memento backend.
Move/reuse the polished Memento frontend components in the Smara repository and
replace their data calls with Smara API calls. The separate Control PWA is a
temporary staging/operator surface, not a second user product.

## 3. Non-negotiable safety rules

1. Smara imports only the public Syntarus SDK/API, never MemoryOS Python
   internals or storage clients.
2. Every task is account-scoped and represented by a durable dependency graph.
3. External writes, local writes, terminal/browser work, and messages require
   an explicit policy and approval where applicable.
4. Workers use leases, idempotency keys, bounded retries, cancellation, and
   durable events. A restart must recover or clearly fail a task.
5. Browser clients never receive gateway, provider, integration, or memory
   secrets.
6. Syntarus metadata filters remain advisory until the hosted API enforces
   them server-side. They must never be described as security isolation.
7. No public cutover occurs without shadow testing, rollback, and account-
   continuity verification.

## 4. What is complete

### Core agent and task system — complete

- Independent Smara repository and Docker Compose services.
- API, Postgres task store, Redis event/cache layer, worker, scheduler, and
  integration worker.
- Task graph with dependencies, leases, retries, cancellation, approvals,
  idempotency, durable events, schedules, and dead-letter handling.
- Provider-neutral agent runtime with configurable OpenAI-compatible providers.
- Live Grok chat and provider failure classification verified on staging.
- Account-scoped signed gateway authentication and browser-authorized CLI login.

### Research and memory — complete for the current safe scope

- Tavily discovery, bounded public retrieval, SSRF protection, source
  verification, evidence ledger, content hashes, citations, and report
  artifacts.
- Syntarus public SDK retrieval and verified write-back.
- Memory continuity across Smara surfaces using account identity.
- MemoryOS source and memory pipeline were not changed.

### CLI — complete beta foundation

- Login, chat, session shell, research, tasks, approvals, task watching,
  tools, device management, and reconnect/error handling.
- CLI is a hosted client of Smara; it has no second memory database or agent
  brain.

### Desktop executor — complete foundation, reliability still open

- Pairing, capability declarations, leases, heartbeat, revocation, file
  read/write contracts, allowlisted terminal/browser contracts, DPAPI token
  protection, pause/resume, and a live staging round trip.

### Integrations and clients — contracts complete

- Approval-gated credential storage and action contracts for Gmail, Calendar,
  Drive, GitHub, and Telegram outbound delivery.
- Transitional Control PWA with tasks, research, evidence, approvals,
  schedules, devices, integrations, memory messaging, capture, and push hooks.
- Phone/PWA notification and capture contracts.

### Verification and operations — repository/staging level complete

- Local test suite, Python compilation, JavaScript syntax checks, health and
  readiness checks.
- Protected staging backups and a disposable Postgres restore drill.
- Account isolation, approval denial, retry/fail-fast, SSRF, research, and
  Syntarus SDK live checks.

## 5. What remains — priority order

### P0 — Make one user-facing Smara Web

This is the most important remaining product task.

1. Move/reuse the Memento frontend shell and visual components in the Smara
   repository. Preserve its navigation, chat feel, message rendering, settings
   style, and responsive behavior.
2. Add a Smara API client behind that UI. Do not copy Memento's agent loop,
   MemoryOS imports, or infrastructure code.
3. Use one same-origin authenticated gateway. The gateway maps the existing
   account to a Smara account assertion without exposing signing secrets.
4. Migrate these screens in order:
   - chat and conversation history;
   - tasks, plans, live events, and cancellation;
   - research, evidence, citations, and artifacts;
   - approvals;
   - schedules;
   - integrations;
   - desktop/CLI devices;
   - memory controls and settings.
5. Remove the Control iframe and direct Control navigation only after the
   native flows pass refresh, reconnect, account-isolation, and approval tests.

### P1 — Finish local execution

1. Run a longer Windows test covering PC restart, disconnect/reconnect, lease
   expiry, cancellation, duplicate prevention, capability denial, and revoke.
2. Package a signed desktop installer with auto-start, bounded rotating logs,
   safe update, visible pause, and visible revoke.
3. Connect only approved terminal/browser/code steps to a real isolated sandbox.
   Arbitrary code execution is not yet a production capability.

### P2 — Activate channels and integrations

1. Put integration keys, OAuth secrets, VAPID private keys, and signing keys in
   a real VM/cloud secret manager.
2. Perform a key rotation drill before storing production grants.
3. Verify disposable Gmail, Calendar, Drive, GitHub, and Telegram accounts.
   Every external write must remain approval-gated and idempotent.
4. Add Telegram inbound conversation support if Telegram is intended to be a
   full chat door. Current Smara Telegram support is outbound approved delivery.
5. Add WhatsApp only after its business account, templates, webhooks, and
   messaging-window rules are deliberately designed.
6. Verify real phone push subscription, capture, approval, and scheduled-task
   delivery.

### P3 — Finish memory controls safely

1. Keep account-scoped retrieval and verified write-back enabled.
2. Keep workspace/status metadata filters marked advisory.
3. Do not modify MemoryOS core to simulate secure filters.
4. Add inspect, correct, pin, supersede, and delete controls only when the
   public Syntarus API/SDK supports those operations with server enforcement.

### P4 — Production hardening

1. Move backups to encrypted off-host storage and test retention plus restore.
2. Configure Sentry alerts and verify a test event.
3. Complete Cloudflare/WAF distributed rate-limit review for authenticated API
   traffic.
4. Add per-task token, cost, wall-clock, output-size, and resource budgets.
5. Add production artifact storage, versioning, signed downloads, retention,
   and large-file limits.
6. Run security, fault-injection, restart, concurrency, isolation, and
   rollback tests.

### P5 — Reversible production cutover

1. Preserve existing account IDs and Syntarus memories; issue fresh Smara
   sessions when needed.
2. Run Memento-versus-Smara shadow evaluation for selected beta accounts.
   Shadow mode must never perform external actions.
3. Compare retrieval quality, answer quality, tool plans, task completion,
   latency, cost, and safety decisions.
4. Route one beta cohort from `ai.syntarus.com` to Smara and rehearse rollback.
5. Expand traffic only after chat, tasks, research, approvals, schedules,
   integrations, desktop, notifications, and memory continuity pass.
6. Make Smara the public agent. Keep Memento source and deployment available
   for rollback, but stop serving it publicly.

## 6. Completion estimate

These are product-completion estimates, not lines-of-code measurements:

| Area | Status | Remaining |
|---|---:|---:|
| Core agent/task/research runtime | 90% | 10% |
| CLI and cloud execution | 85% | 15% |
| Desktop/local execution | 75% | 25% |
| Integrations and channels | 50% | 50% |
| One Memento-quality Smara Web | 35% | 65% |
| Memory boundary and controls | 70% | 30% |
| Production operations and cutover | 35% | 65% |

Weighted overall estimate:

- **Agent implementation:** approximately **75% complete**.
- **Full production vision at `ai.syntarus.com`: approximately 60%
  complete; approximately 40% remains.**

The remaining work is primarily UI/API consolidation, provider operations,
desktop hardening, sandboxing, production controls, and cutover—not a rewrite
of the task graph or MemoryOS memory engine.

## 7. Definition of done

Smara is ready to replace the public Memento agent when all of these are true:

- A user can use the reused Memento UI without seeing a second Control app.
- A chat request can become a durable task with visible plan, events,
  approvals, evidence, artifacts, and cancellation.
- Research produces verified sources and cited output.
- The CLI, web, desktop, phone, and channels show the same account-scoped work.
- Approved local actions survive disconnects and restarts without duplication.
- External actions are approval-gated, auditable, retry-safe, and reversible
  where the provider allows it.
- Syntarus memory continuity is proven without changing MemoryOS internals.
- Production secrets, backups, alerts, rate limits, budgets, and sandbox gates
  are operationally verified.
- Shadow results pass, one beta cohort passes, rollback is rehearsed, and only
  then is public traffic switched to Smara.

## 8. Immediate next action

Finish the P0 native-screen migration and shadow test the `/smara/` mount.
The route is now persistent and the shell/gateway are source-complete for
chat, tasks, research, and account-scoped routing. Remaining P0 work is to
replace the legacy Memento calls in the copied panels, remove the temporary
Control iframe, and run live refresh, task-event, approval, and rollback
tests. Keep the current Memento production deployment unchanged until those
tests pass.

## 9. Implementation log

### 2026-08-26 — P0 slice 1 started

- Added `smara/frontend/`, a UI-only copy of the existing Memento frontend
  shell. No MemoryOS backend, agent loop, storage client, or provider secret
  was copied.
- Added `smara/frontend/src/lib/smaraGateway.ts`. In opt-in bridge mode it
  obtains a short-lived control token from the existing authenticated web
  session and sends Smara API requests with that token. The browser never
  signs requests and never receives the gateway secret.
- Adapted the copied chat stream to Smara's `/v1/chat/stream` and the task
  helper to Smara's `/v1/tasks`/`cancel` contract. Existing Memento routes
  remain the default when `VITE_SMARA_MODE=false`.
- Added a native `Work` panel for Smara task selection, live activity,
  dependency-step status, approval/denial, cancellation, research evidence,
  and artifacts. The old Control iframe remains available only outside bridge
  mode, preserving rollback while the remaining screens migrate.
- Switched active-task updates from repeated polling to Smara's durable
  `/v1/tasks/{id}/events/stream`, retaining a slow refresh as reconnect repair.
- Added a native Research form in the Work view. It creates a durable
  `/v1/research` task with optional validated source URLs; the same selected
  task then exposes verified evidence, citations, and report artifacts.
- Added an explicit JavaScript Vite config for the migrated app. The
  production bundle now completes successfully (5,147 modules transformed),
  and a local browser smoke check reaches the expected sign-in screen without
  runtime UI errors when the API is stopped.
- Added the missing authenticated bridge contract to the existing Memento auth
  service: `POST /v1/auth/control-token` mints a bounded, Smara-scoped JWT from
  the signed-in account session. The signing secret is separate from session
  and worker secrets and is shared only through deployment secret managers.
- Matched the frontend bridge cache to the API's `expires_in_seconds` response
  (while accepting the old alias during rollout), preventing stale or
  needlessly long-lived browser tokens.
- Made the gateway route-aware: migrated Smara API families use the short-lived
  bridge token, while unreplaced Memento/MemoryOS memory, graph, stats, upload,
  and feedback routes stay on the existing authenticated origin. Enabling
  bridge mode therefore cannot break the remaining legacy panels.
- Verified the staged gateway contract against the VM: the rebuilt auth route
  is present on `ai.syntarus.com`, unauthenticated calls return `401`, and a
  disposable account-scoped bridge assertion is accepted by the Smara API.
- Added a reversible production UI mount: a small nginx frontend image builds
  the migrated shell under `/smara/`, while `/smara-api/` is stripped and
  proxied to the Smara API. The existing root Memento app remains untouched
  until browser shadow tests pass.
- Built and started the frontend on the staging VM at `127.0.0.1:8081`, then
  loaded the route through Caddy's admin API for a live, reversible check:
  `/smara/` serves the new bundle, `/smara-api/health` returns `200`, hashed
  assets and client-side routes return `200`, and the root app still serves as
  before. Persisted the root-owned Caddyfile and reloaded Caddy; the root
  Memento frontend is explicitly kept on its live Docker port `8080` after a
  regression check. No public root-path cutover has been made.
- Added a frontend migration README and environment template. This is a
  reversible source-level slice; it is not a public cutover.
- Verified the copied UI with TypeScript strict compilation and the Smara
  backend suite (93 tests). Vite's production bundle is currently blocked by
  the Windows sandbox's esbuild path-resolution error; the original frontend
  reproduces the same toolchain error. Next P0 slice: run live account,
  refresh, task-event, research, and approval tests through the staging origin,
  then finish the remaining native-panel migration before cutover.

### 2026-08-26 — Persistent mount verification

- Persisted `/smara/` and `/smara-api/` in `/etc/caddy/Caddyfile` and reloaded
  Caddy successfully.
- Corrected the root catch-all to the actual Memento frontend listener
  `127.0.0.1:8080`; a regression had briefly produced `502` at `/` after the
  first persisted snapshot. Rechecked and restored `200` at the root.
- Final VM checks: `/smara/` `200`, `/smara/work` `200`,
  `/smara-api/health` `200`, unauthenticated `/smara-api/v1/tasks` `401`,
  root `ai.syntarus.com/` `200`, Caddy active. This is still a reversible
  staging mount, not a public replacement.

### 2026-08-26 — Remove non-product visual lab

- Removed the experimental visual-lab source folder and every route, menu entry, and
  unauthenticated launch button from the Smara frontend. The product now opens
  directly into chat/work flows without the experimental 3D lab or its camera
  and hand-tracking code.
- TypeScript strict compilation still passes. The Windows Vite build remains
  blocked by the known sandbox/esbuild path-resolution restriction; the
  production image must be built in Docker on the VM.
