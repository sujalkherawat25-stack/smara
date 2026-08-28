# Smara Implementation Plan

**Status date:** 2026-08-28
**Repository:** `sujalkherawat25-stack/smara`
**Public target:** `https://ai.syntarus.com`
**Current routing:** Smara is the public root as a reversible beta cutover;
Memento remains available behind the preserved backend/rollback configuration.

## 1. Scope reset

Smara has one focused job: be a dependable personal agent in two forms.

1. **Hosted Smara** — the web app and CLI use one server-side agent, task
   graph, approval system, and Syntarus memory account. Hosted work continues
   when the user's computer is closed. The VM uses only operator-owned
   provider credentials; it does not store or use a user's private account
   tokens, browser sessions, files, or terminal access.
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

### Control-plane/data-plane security boundary

```text
Hosted Smara VM (control plane)          User PC (local data plane)
──────────────────────────────          ────────────────────────────
Task graph, leases, retries              Browser session and browser actions
Approvals, schedules, monitoring         Files and terminal commands
Operator-owned LLM/search keys            Personal OAuth/API credentials
Public web research retrieval             Local integration adapters (future)
Syntarus memory API calls                 Desktop executor + local artifacts
```

The hosted worker is fail-closed for personal integrations by default
(`SMARA_HOSTED_USER_INTEGRATIONS_ENABLED=false`). It does not decrypt, claim,
or execute Gmail, Calendar, Drive, GitHub, Telegram, or other user-account
actions. The existing integration code remains behind an explicit operator
flag for controlled future use, while the local adapter contract is built.
`local_browser` means opening an allowlisted URL on the paired PC; it is never
run by the VM. Public source fetching for research is an HTTP retrieval tool,
not access to a user's browser session.

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
- Approval-first records for external writes and other risky actions; private
  actions are handed to the paired local executor rather than run on the VM.
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
- Browser, file, terminal, and future personal-account actions stay on the
  user's PC; only explicit result/proof data returns to the control plane.

## 4. Explicitly deferred (do not count these as release blockers)

These are backlog items, not part of the focused replacement release:

- Phone/PWA client and phone push/capture.
- Hosted Telegram, WhatsApp, Gmail, Calendar, Drive, and GitHub channels or
  actions. Personal integrations must be implemented as local adapters first;
  user credentials must not be uploaded to the hosted VM.
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
- Windows-native Smara Desktop beta shell (Tauri + React) around that executor:
  hosted chat streaming, live activity, task status, pairing, permissions,
  pause/resume/stop/revoke, reconnect-friendly refresh, and bounded local log
  visibility. The shell keeps the executor and hosted API as the only runtime
  paths; it does not add a second agent or memory store.
- Repeatable Windows packaging now builds a PyInstaller standalone executor,
  embeds it as a Tauri resource, and produces MSI/NSIS installers. The
  artifacts are unsigned beta packages; signing, update trust, and restart
  drills remain release gates.
- Desktop onboarding includes the hosted browser device-approval flow, so a
  user can sign in from the app without first installing or invoking the CLI.
- Hosted personal integrations are disabled by default; the worker and tool
  catalogue fail closed unless an operator explicitly opts in.
- Desktop step leases now refresh immediately before completion on both the
  SQLite development store and the live Postgres store; stale executors cannot
  finalize a lease recovered by another executor.
- Reversible `/smara/` and `/smara-api/` staging mount at `ai.syntarus.com`.
- Local suite (122 Smara backend tests), frontend type-check, VM Docker build,
  live health checks, and disposable account/task/research/approval smoke test.
- Native desktop QA now covers the real Windows WebView shell as well as a
  browser-safe UI preview. Completed hosted tasks expose their durable final
  result in Activity, task state auto-refreshes while visible, expired tokens
  return the user to sign-in, and interrupted SSE streams terminate visibly.
- Desktop hosted connection is split into explicit Web URL and API URL
  settings. Sign-in opens the configured Smara Web origin with the one-time
  device code; it no longer sends users to the API's raw fallback page.
- Desktop model selection supports operator-configured `default`, `grok`, and
  `sarvam` profiles. Only the profile name travels in requests; provider keys
  stay server-side and are never put in the desktop bundle.
- Desktop users can save personal tool credentials locally under their Windows
  account using an encrypted vault. Approved local terminal steps may request
  an environment-name alias; the secret is injected only into that child
  process and is redacted from returned output.

## 6. Remaining work, in order

### P0 — Make Smara Web one native product

1. **Mostly done:** adapt the Memento agent-loop behavior into Smara's
   provider-neutral runtime. Deterministic triage, bounded tool reasoning,
   streaming, memory context, and phase events are live. Live staging smokes
   now cover the configured Grok model, calculator, Tavily discovery/page
   retrieval, cited research, and the Postgres desktop approval path.
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
   Confirm the VM cannot claim a `desktop` step and that local browser/file/
   terminal work runs only on the paired PC.
2. The native Tauri shell and self-contained unsigned MSI/NSIS packages now
   build locally. Remaining: sign the installer and executor, add a trusted
   update channel, test auto-start opt-in, bounded logs, visible pause/revoke,
   and clear pairing recovery on clean Windows machines.
3. Connect only approved terminal/browser/file steps to an isolated sandbox;
   unrestricted arbitrary code is not a production capability.

### P2 — Production readiness and cutover

1. Put only operator-owned provider, signing, database, and sandbox secrets in
   a real VM secret manager; perform a rotation drill. Do not place user
   integration credentials in this manager or in Smara Postgres.
2. Finish encrypted off-host backups and a disposable restore drill.
3. Configure Sentry/structured logs, authenticated distributed rate limits,
   per-task cost/time/output/resource budgets, and artifact retention.
4. Run security, fault-injection, restart, concurrency, isolation, and
   rollback tests.
5. Shadow Smara against Memento for selected beta accounts (no side effects),
   compare answer/tool/task/safety/latency/cost results, and verify that
   personal browser/integration steps are handed to the local executor; then
   route one cohort to Smara and rehearse rollback.
6. **Beta cutover completed:** Smara now serves the public root at
   `ai.syntarus.com`; Memento source/deployment and a dated Caddy configuration
   remain available for immediate rollback. MemoryOS continues as the Syntarus
   memory backend/product.
7. **Pending final promotion:** complete authenticated shadowing, Windows
   restart/reconnect, operator secret-manager rotation, off-host backup
   scheduling, and edge-rate-limit review before calling the cutover fully
   production-ready.

### Auth acceptance runbook (staging)

Run this at `https://ai.syntarus.com/smara/` in a normal browser:

1. Confirm the Google button is visible, choose the intended Google account,
   and confirm the app opens the Smara workspace (not the sign-in screen).
2. Refresh the page. The same account should remain signed in and conversations
   should load from the hosted Smara store.
3. Send a short chat, then open Work and create a harmless read-only task.
   Confirm the task and its events are visible after a refresh.
4. Sign out and reload. The app should return to sign-in; protected Smara
   pages must not show another account's tasks or conversations.
5. If email OTP is enabled for the deployment, repeat with an email address:
   request the code, enter the six digits from the delivery mailbox, and
   verify that it creates the same normal session flow.

The public checks that can be automated without a user session are:
`GET /v1/auth/config` = 200 with a client ID, `GET /v1/auth/me` = 401,
`POST /v1/auth/control-token` = 401, and `/smara-api/v1/conversations` = 401.

## 7. Honest completion estimate

For the **focused hosted + desktop vision** (not the deferred backlog):

| Area | Approx. complete | Main gap |
|---|---:|---|
| Hosted agent/task/research runtime | 80% | Memento behavior parity and edge cases |
| CLI hosted client | 85% | parity polish and authenticated workflow tests |
| Native Smara Web | 70% | authenticated shadow tests and final legacy cleanup |
| Desktop local executor | 86% | restart drills, signing, and update trust |
| Production operations/cutover | 45% | secrets, observability, shadow run, rollback |

**Focused beta:** roughly 77% complete.
**Public replacement:** Smara is now live as a reversible beta root, but the
full production-readiness gates are not yet all green.

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

1. Run the authenticated Smara Web shadow checklist with a real beta account:
   sign-in, refresh, chat, task result, research evidence, approval, reconnect,
   sign-out, and account isolation.
2. Run the Windows executor failure drill: pair, approve a harmless local step,
   restart the PC/app, disconnect/reconnect, cancel, revoke, expire a lease,
   and confirm no duplicate or hosted-side local execution.
3. Add the Sarvam key when desired and run one low-cost provider smoke; keep
   Grok as the current hosted default until that smoke passes.
4. Close the remaining production gates: signed installer/update trust,
   operator secret-manager rotation, encrypted off-host backup schedule,
   edge-limit review, structured alerting, sandbox deployment, shadow metrics,
   and rollback rehearsal.
5. Promote Smara from reversible beta to the permanent public root only after
   all four checks above are recorded green. Keep MemoryOS as the unchanged
   Syntarus memory service and retain Memento only as rollback/reference.

## 10. Historical implementation log

The detailed historical entries below are retained for traceability; they are
not additional scope. New entries should record only work that advances the
focused hosted/desktop release.

### 2026-08-28 — Desktop hosted routing, provider profiles, and local secrets

- Fixed desktop device sign-in to open the Smara Web origin while keeping the
  API origin separately configurable. The same correction is applied to the
  CLI hosted-login URL when the API is mounted at `/smara-api`.
- Added operator profiles for Grok and Sarvam through the existing
  OpenAI-compatible runtime. Grok uses the configured VM key; Sarvam becomes
  selectable after the operator adds `SMARA_SARVAM_KEY` (no user key is sent
  to the VM).
- Added a Windows-account-encrypted local credential vault with masked list,
  remove, process-only injection, and output redaction. Credential values are
  not stored in Smara task payloads, logs, or API responses.
- Verification: 122 backend tests, frontend production build, Rust checks,
  and a fresh NSIS package passed. Hosted services and public health remain
  healthy; the unsigned installer and Windows restart/reconnect drill are
  still release gates.

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
  testing and the final root cutover remain gates. The VM rebuild and public
  auth configuration smoke check for this commit are complete.

### 2026-08-26 — Runtime authentication configuration

- Smara sign-in now reads the public Google OAuth client ID from the existing
  `/v1/auth/config` endpoint at runtime, with a build-time value retained only
  as a development fallback.
- This keeps the Google button available on the hosted Smara mount when the
  backend is configured and avoids rebuilding the UI for a public client-ID
  change. Email OTP remains available if Google is disabled.
- Local verification: 95 Smara backend tests and frontend type-check pass.
  The remaining auth gate is a real beta-account sign-in and authenticated
  browser shadow run in staging.

### 2026-08-26 — Hosted bridge 500 hotfix

- Diagnosed the authenticated chat failure as a stale root auth image: the
  running service had `SMARA_CONTROL_BRIDGE_SECRET` in its environment, but
  its Settings class did not yet expose the field used by the control-token
  route. This caused an `AttributeError` and HTTP 500 before Smara could start
  a stream.
- Added the missing bridge-secret and token-TTL settings to the running auth
  service and restarted only that backend. The bridge setting loads, startup
  logs are clean, and the Smara API remains healthy. MemoryOS storage and
  retrieval code were not changed.
- The remaining verification is user-session based: refresh the Smara page,
  send a short chat, and confirm the stream starts. The unauthenticated guard
  continues to return 401 as intended.

### 2026-08-26 — Research source-quality hardening

- Search URLs are now canonicalized (tracking parameters and fragments are
  removed) and duplicate results are discarded before they reach the agent.
- Search hits expose advisory source tiers (`primary`, `secondary`,
  `unclassified`, or `discovery_only`). First-party and reputable reporting
  are ranked ahead of directories, videos, and other discovery-only leads.
- Search results are explicitly treated as discovery leads, not verified
  citations. The agent is instructed to fetch a source before making factual
  claims; fetched evidence carries publication date, source tier, and quality
  flags into the ledger and final report.
- Tavily uses `advanced` depth by default for stronger research retrieval;
  operators may set `SMARA_SEARCH_DEPTH=basic` when latency/cost is the higher
  priority. No tools or agent actions were removed.
- Verification: the full Smara suite passes (97 tests). Live deployment and
  public health smoke checks remain part of the staging rollout below.

### 2026-08-26 — Workflow and tool reliability hardening

- New task and schedule defaults now enter the real `agent.execute` path;
  legacy `execute_task` rows remain supported during the transition.
- Desktop and sandbox steps require an explicit capability and approval. Direct
  bypasses are rejected, executor heartbeats cannot add capabilities, expired
  leases honor cancellation, and uncertain terminal/browser/write failures go
  to dead-letter instead of being replayed.
- Agent-created desktop child tasks become visible as waiting for approval
  immediately. The worker now passes the desktop requester through its tool
  context, so approved local work can be handed to the paired executor.
- Direct chat now uses the same account-scoped integration read adapter as
  hosted tasks, including encrypted credential lookup and Google token refresh;
  external writes remain approval-gated durable intents.
- Hardened provider stream fallback, research profile selection, account task
  cleanup, browser allowlists, and Gmail/Calendar/Telegram input bounds.
- Verification: 109 backend tests, Python compile check, frontend type-check,
  and production frontend build all pass. The build reports only an existing
  non-blocking large-chunk optimization warning.

### 2026-08-26 — Live desktop approval workflow fix

- A disposable Postgres smoke test found that the hosted worker skipped queued
  desktop steps completely, leaving them invisible until approval. The worker
  now advances only the approval transition; after approval, the paired
  desktop executor alone can lease and complete the local step.
- Local regression coverage remains green (109 backend tests). The staging
  service must be redeployed with this fix before repeating the live desktop
  smoke test.

### 2026-08-27 — Desktop lease reliability

- Added an authenticated desktop step-heartbeat endpoint backed by the same
  lease state machine in SQLite and Postgres. The paired executor refreshes its
  lease before reporting completion; cancellation is observed without
  extending the lease, and a stale/recovered executor receives a conflict.
- Increased the desktop claim lease default to three minutes while keeping
  explicit shorter leases available for tests and recovery drills.
- Added regression coverage for refresh, cancellation, and stale-executor
  rejection, including the runner's request ordering. The local suite now
  passes 114 tests.

### 2026-08-27 — Live provider and executor verification

- Rebuilt the staging API/worker/scheduler/integration-worker from the pushed
  lease-heartbeat commit. Public health/readiness, the configured Grok model,
  Tavily discovery and bounded page retrieval, calculator tool selection, and
  a two-tool cited research turn all passed without exposing credentials.
- A disposable Postgres account/task verified the hosted desktop approval,
  lease claim, heartbeat refresh, and completion contract in the deployed
  image. The existing root Memento route remains unchanged.
- The validated Smara Caddy file is now persistent across Caddy restarts, with
  a dated rollback copy retained on the VM. Public `/smara/`, `/smara/work`,
  `/smara-api/health`, `/smara-api/readyz`, root Memento, and auth-config routes
  all returned HTTP 200 after reload; a full Caddy restart also kept `/readyz`
  healthy.
- A public authenticated burst smoke returned 120 HTTP 200 responses followed
  by 5 HTTP 429 responses, confirming the Redis-backed Smara limiter through the
  deployed edge without exposing a real account credential.

### 2026-08-27 — Work result visibility fix

- Fixed the native Work panel to consume the actual Smara task-event contract
  (`type`, JSON `payload`, and `created_at`). Completed tasks now show their
  final textual result, activity entries show event names and useful details,
  and research evidence/artifacts can be expanded in place.
- Frontend type-check and production build passed. Only the frontend was
  rebuilt on staging; the Smara API, workers, databases, and existing root
  Memento deployment were not changed.
- User verification: hard-refresh `https://ai.syntarus.com/smara/`, open
  **Work**, select a completed task, and read the **Result** section. Research
  tasks expose **Evidence** and **Artifacts** below it.

### 2026-08-27 — Durable completion result contract

- Terminal `task.completed` events now carry the actual final step result in
  both SQLite development storage and the inherited Postgres store contract.
  The old `{"result":"recorded"}` bookkeeping marker no longer hides the
  answer from the Web UI, CLI, or future clients.
- Added a regression test; the Smara suite now passes 114 tests. API and worker
  images were rebuilt on staging and `/health` plus `/readyz` returned 200.

### 2026-08-27 — Durable result API and legacy backfill

- Task completion now persists the final textual result on the task row and
  exposes it as `result` from every task API response. This removes the need
  for clients to infer the answer from event ordering.
- Added Postgres migration `018_task_result_summary.sql` plus a local-store
  compatibility backfill. Historical lifecycle markers such as `recorded`
  are ignored while useful `step.completed` results are recovered.
- The native and legacy Work panels now prefer the durable result and filter
  bookkeeping-only markers. Backend regression tests (115), frontend type
  check, and production build pass.

### 2026-08-27 — Production smoke and restore verification

- Rechecked the deployed VM: all Smara services are running, `/health` and
  `/readyz` return 200, and Postgres reports 18 applied migrations with none
  pending. Recent API/worker/scheduler logs contain no fatal startup or
  migration errors.
- Verified the public UI route (200), the signed account gateway (200), and
  the unauthenticated task guard (401). A fresh live Postgres backup was
  checksum-verified and restored into a disposable Postgres 16 container;
  the disposable resources were removed after the drill.
- The remaining operational gates are still external: authenticated browser
  shadowing, a real secret manager and rotation, encrypted off-host backup
  scheduling, Cloudflare distributed-limit review, Windows restart/reconnect,
  and any separate sandbox deployment. Sentry remains intentionally skipped.

### 2026-08-27 — Local-only private execution boundary

- Made the hosted control plane fail closed for personal integrations by
  default. `SMARA_HOSTED_USER_INTEGRATIONS_ENABLED=false` hides Gmail,
  Calendar, Drive, GitHub, Telegram, and credential/OAuth tools from the hosted
  catalogue and rejects hosted credential/action writes.
- The integration worker now stays idle without claiming or decrypting old
  approved rows. The hosted worker can still create approval-gated desktop
  requests; the paired PC is the only place where browser sessions, files,
  terminal commands, and future personal-account adapters may run.
- Updated the production checker and operator docs so the integration key ring
  is required only for a deliberate legacy hosted-mode opt-in. Operator-owned
  LLM/search/Syntarus keys remain server-side as planned.
- Added regression coverage for the local-only tool/plugin catalogue and
  fail-closed integration worker; the Smara suite now passes 119 tests.
- Deployed commit `0775f71` to staging. Live checks confirm all seven services
  are running, 18 migrations are applied, public routes remain healthy, the
  signed tool/plugin catalogues omit personal integrations, local-only status
  is returned for integration reads, and a credential-write probe is rejected
  before persistence.

### 2026-08-27 — Reversible public root cutover

- Rebuilt the Smara frontend with a root base path and switched Caddy so
  `https://ai.syntarus.com/` serves Smara. The `/smara/` path remains available
  as a compatibility route, while `/smara-api/*` continues to reach the
  independent Smara API.
- Kept `/v1/*` on the existing Memento/MemoryOS backend for the authenticated
  session bridge and legacy routes. A dated Caddy copy is retained at
  `/etc/caddy/Caddyfile.pre-smara-root-cutover-20260827` for immediate rollback;
  the Memento service and MemoryOS data were not stopped or modified.
- Public root/UI, auth configuration, unauthenticated guards, Smara readiness,
  asset delivery, and a Caddy restart all passed after the switch. This is a
  reversible beta cutover, not a claim that the remaining operational gates
  are complete.
