# Smara Implementation Plan

**Status date:** 2026-08-28
**Repository:** `sujalkherawat25-stack/smara`
**Public target:** `https://ai.syntarus.com`
**Current routing:** Smara owns the canonical `https://ai.syntarus.com/` root.
The API is same-origin at `/smara-api`; the old `/smara/` UI mount and
`control-staging.syntarus.com` service are retired (`/smara/` returns `410`
and the staging hostname has no DNS record). Memento remains available only
behind the preserved backend/rollback configuration.

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

### Owner-approved deferrals (2026-08-28)

The following hardening items are deliberately postponed for the current beta
and are not part of the active implementation sprint:

- Sentry project/DSN and alert delivery.
- Authenticode signing and a trusted auto-update channel for Windows packages.
- Migration from the current protected VM environment to an external secret
  manager and a live rotation drill.
- Encrypted off-host backup scheduling and recurring restore drills.
- A hosted sandbox service. `SMARA_SANDBOX_ENABLED` stays `false`; private
  browser, file, and terminal work remains on the paired desktop.

These are deferred, not complete. They remain release gates for a later
production promotion, while the current beta keeps fail-closed behavior.

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
  embeds it as a Tauri resource, and produces the supported NSIS installer. The
  artifacts are unsigned beta packages; signing, update trust, and restart
  drills remain release gates.
- Desktop onboarding includes the hosted browser device-approval flow, so a
  user can sign in from the app without first installing or invoking the CLI.
- Hosted personal integrations are disabled by default; the worker and tool
  catalogue fail closed unless an operator explicitly opts in.
- Desktop step leases now refresh immediately before completion on both the
  SQLite development store and the live Postgres store; stale executors cannot
  finalize a lease recovered by another executor.
- Canonical root UI at `ai.syntarus.com` with the same-origin `/smara-api`
  backend route; no second public Smara UI is required.
- Local suite (139 Smara backend tests), frontend type-check/production build,
  eight native Rust tests, full Windows executor packaging, VM Docker build,
  live health checks, and disposable account/task/research/approval smoke test.
- Native desktop QA now covers the real Windows WebView shell as well as a
  browser-safe UI preview. Completed hosted tasks expose their durable final
  result in Activity, task state auto-refreshes while visible, expired tokens
  return the user to sign-in, and interrupted SSE streams terminate visibly.
- Desktop hosted connection is split into explicit Web URL and API URL
  settings. Sign-in opens the configured Smara Web origin with the one-time
  device code; it no longer sends users to the API's raw fallback page.
- Desktop model settings now support two safe modes: hosted Automatic/Grok/
  Sarvam profiles (only a profile name crosses to the VM), and private
  Sarvam/Grok/custom OpenAI-compatible profiles. Private keys are encrypted
  in the Windows-account vault, never uploaded, and can be used for direct
  desktop chat even when hosted sign-in is unavailable. Saving a private
  profile selects and persists it immediately; hosted task planning remains
  clearly separate.
- Desktop model selection supports operator-configured `default`, `grok`, and
  `sarvam` profiles. Only the profile name travels in requests; provider keys
  stay server-side and are never put in the desktop bundle.
- Desktop users can save personal tool credentials locally under their Windows
  account using an encrypted vault. Approved local terminal steps may request
  an environment-name alias; the secret is injected only into that child
  process and is redacted from returned output.
- A repeatable `scripts/live_beta_smoke.py` now verifies public health,
  signed-account task access, cross-account isolation, cancellation, the
  local-only hosted integration policy, and the bounded rate limiter without
  printing deployment secrets or retaining test work.
- Desktop UX now makes sign-in explicit from Chat, disables unauthenticated
  starter actions, distinguishes reachable from authenticated hosted status,
  avoids native event registration in browser previews, and renders pairing,
  settings, and credential failures inline. The Windows package script now
  fails loudly and builds the supported NSIS beta installer.
- Hosted agent calls now have a hard wall-clock budget, per-tool timeout, and
  total tool-call budget. Identical tool requests are not executed twice;
  transient non-stream provider calls retry once, while a stream failure before
  its first token falls back to the normal completion endpoint.
- Desktop lease recovery distinguishes safe reads from uncertain side effects.
  An expired `local_file_read` can be reclaimed, but terminal, browser, and
  file-write steps fail closed into the account dead-letter queue instead of
  being replayed after an ambiguous disconnect.
- The live beta smoke can now exercise the real Grok/Tavily agent contract and
  the production Postgres desktop lease-safety rule using disposable accounts.

## 6. Remaining work, in order

For the current reversible beta, active implementation is intentionally limited
to four verification tracks: authenticated Smara Web shadowing, Windows
executor reconnect testing, edge-limit review, and an optional low-cost Sarvam
smoke. The broader P1/P2 items below remain documented future gates; they are
not new work to start during this beta sprint.

### P0 — Make Smara Web one native product

1. **Done for the focused beta:** adapt the Memento agent-loop behavior into Smara's
   provider-neutral runtime. Deterministic triage, bounded tool reasoning,
   streaming, memory context, and phase events are live. Live staging smokes
   now cover the configured Grok model, calculator, Tavily discovery/page
   retrieval, cited research, and the Postgres desktop approval path. The
   runtime now also enforces wall-clock/tool budgets, duplicate-tool rejection,
   a bounded transient retry, and safe stream-before-first-token fallback.
2. **In progress:** native Smara calls now cover chat streaming, hosted
   conversation history/recents, durable tasks/events, research evidence,
   approvals, schedules, and executor settings. Remaining: authenticated
   browser shadow tests across refresh, reconnect, and account isolation.
3. **Done for the focused shell:** the temporary Control iframe and duplicate
   navigation are no longer mounted in Smara mode. Legacy components remain in
   source only as a rollback path until the shadow run is accepted.
4. **Pending:** run the authenticated browser shadow suite with a real beta
   account. Keep the preserved Memento/Caddy rollback configuration until it
   passes; Smara already owns the reversible-beta public root.

### P1 — Make the desktop executor dependable

1. Automated coverage now passes for lease expiry, cancellation,
   capability denial, revoke, retries, and duplicate prevention on Windows.
   The production Postgres smoke proves an ambiguous terminal lease is blocked
   and audited rather than replayed. Remaining: a physical PC/app/network
   restart and reconnect drill with the owner's paired account.
2. The native Tauri shell and self-contained unsigned NSIS package now
   build locally. Remaining: sign the installer and executor, add a trusted
   update channel, test auto-start opt-in, bounded logs, visible pause/revoke,
   and clear pairing recovery on clean Windows machines.
3. Hosted sandbox execution remains owner-deferred and disabled. Local
   terminal/browser/file steps stay approval-gated on the paired PC;
   unrestricted arbitrary code is not a production capability.

### P2 — Production readiness and cutover

1. Put only operator-owned provider, signing, database, and sandbox secrets in
   a real VM secret manager; perform a rotation drill. Do not place user
   integration credentials in this manager or in Smara Postgres.
2. Finish encrypted off-host backups and a disposable restore drill.
3. Structured safe provider-failure logs, authenticated backend rate limits,
   and hosted agent time/tool/output budgets are implemented. Remaining for a
   later production promotion: Sentry delivery, edge-wide rate-limit policy,
   cost accounting/resource budgets, and formal artifact retention.
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

Run this at `https://ai.syntarus.com/` in a normal browser:

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
| Hosted agent/task/research runtime | 88% | broader eval corpus, cost accounting, and rare provider edges |
| CLI hosted client | 86% | authenticated usability polish and long-session soak |
| Native Smara Web | 72% | authenticated shadow tests and final legacy cleanup |
| Desktop local executor | 89% | physical restart drill, signing, and update trust |
| Production operations/cutover | 48% | deferred hardening gates and authenticated shadow run |

**Focused beta:** roughly 82% complete.
**Public replacement:** Smara is live as a reversible beta root. The owner has
explicitly deferred five production-hardening gates, so this is not a claim of
full production readiness.

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
2. Run the remaining physical Windows executor drill: pair, approve a harmless
   local step, restart the PC/app, disconnect/reconnect, cancel, and revoke.
   Automated and production-database lease-expiry/no-replay checks are green.
3. Run the authenticated edge-rate-limit review using the real browser session;
   the backend Redis limiter and bounded synthetic burst are already green.
4. Keep the five owner-deferred production gates disabled and documented.
   Reopen them only when the required signing certificate, Sentry account,
   secret-manager access, off-host backup destination, or isolated sandbox
   decision is explicitly supplied.
5. Promote Smara from reversible beta to a production label only after the
   active checks above are recorded green. Keep MemoryOS as the unchanged
   Syntarus memory service and retain Memento only as rollback/reference.

## 10. Historical implementation log

The detailed historical entries below are retained for traceability; they are
not additional scope. New entries should record only work that advances the
focused hosted/desktop release.

### 2026-08-28 — Agent and executor reliability closure

- Added a 90-second hosted agent deadline, 20-second per-tool timeout, and
  three-call tool budget. Repeated identical tool requests are rejected before
  invocation, which prevents an accidental duplicate side effect inside one
  reasoning run.
- Added one bounded retry for transient non-stream provider failures and a
  normal-completion fallback when a provider stream dies before its first
  token. Partial streams are never answered a second time.
- Changed desktop lease recovery to replay only `local_file_read`. An expired
  terminal, browser, or file-write lease has an uncertain outcome, so Smara now
  fails it closed, emits `executor.lease_expired_uncertain`, and records an
  account-scoped dead letter for explicit human review.
- Safe chat failure logging records only the provider name, classified error
  kind, and exception type; it never logs prompts, tokens, credentials, or raw
  provider bodies.
- Verification passed: 139 Python tests, eight native Rust tests, both Web
  production builds, PyInstaller executor packaging, and a fresh unsigned NSIS
  installer. The installed Windows WebView completed a real hosted chat turn.
- Deployed commits `e72f466` and `89f8a05`. The live disposable workflow passed
  direct Grok chat, calculator, Tavily discovery, official-page fetch, cited
  research, task/cancel/isolation checks, and production Postgres no-replay
  recovery (`desktop_lease=blocked-and-audited`). All seven Compose services
  remained healthy; MemoryOS source and data were not changed.

### 2026-08-28 — Beta verification and desktop UX pass

- Signed staging shadow smoke passed for health/readiness, hosted tool access,
  disposable task create/cancel, cross-account isolation, and the local-only
  integration policy. The same harness recorded 120 successful requests and
  5 expected 429 responses in a bounded limiter review.
- Desktop Chat now makes sign-in discoverable, avoids dead starter actions,
  and distinguishes a reachable hosted service from an authenticated session.
  Settings, pairing, credential, log, and browser-preview failure paths are
  non-blocking and visible in the app.
- The Windows package script now checks every native build step and produces
  the supported unsigned NSIS beta installer. A release process stop/start
  check and 26 focused desktop/auth/provider tests passed. A real paired
  disconnect/reconnect task remains an owner-session check.

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

### 2026-08-28 — Desktop settings and provider UX pass

- Reworked the native desktop settings surface around three explicit decisions:
  hosted model profile, local tool credentials, and local execution boundaries.
  Provider cards now make Automatic, Grok, and Sarvam selection discoverable;
  the app sends only the profile name and keeps provider keys on the hosted
  service.
- Added visible local credential status for Tavily, GitHub, and custom tools,
  with a clear local-only boundary and per-process injection explanation.
  Added live counts for approved folders, terminal executables, and browser
  domains so users can see what is enabled before saving.
- Hardened desktop connection feedback: failed connection reads clear the
  remote status instead of leaving stale green state, and failures to attach
  the native chat event stream are shown in the app.
- Frontend production build, 122 backend tests, and Rust bridge checks passed.
  The unsigned NSIS beta installer was rebuilt and the release desktop was
  restarted successfully. MemoryOS and the hosted API were not changed.
- The NSIS installer now creates a `Smara Desktop.lnk` desktop shortcut and
  removes it on uninstall. A verified shortcut was also created for the current
  Windows release executable.

### 2026-08-28 — Private model profile persistence and Sarvam smoke

- Fixed a beta regression where saving ordinary connection/permission settings
  rewrote `desktop-ui.json` without preserving `local_model_profiles`. The
  encrypted key survived, but the UI then reported that the selected profile no
  longer existed. General saves now merge and retain private provider metadata.
- Added recovery for the built-in Sarvam/Grok profile metadata when an older
  install has the selected profile and its encrypted credential but no profile
  record. The repaired metadata is persisted; custom endpoints remain opt-in
  because they cannot be reconstructed safely.
- Verified the existing encrypted Sarvam key against the real
  `https://api.sarvam.ai/v1/chat/completions` endpoint without printing the key,
  then sent a real `Reply with the single word OK.` turn through the rebuilt
  native desktop UI and received `OK`.
- Rebuilt/restarted the Windows release package. The full Smara suite (122
  tests), four native Rust tests, frontend type-check/build, and the native UI
  persistence/chat smoke all pass.

### 2026-08-28 — Desktop UI audit and scroll/branding fixes

The native Chat screen was audited against the reported Windows-window issues.
The concrete problems were:

1. The outer flex chain had no `min-height: 0`/bounded overflow contract, so a
   long transcript could grow the page instead of receiving its own scrollbar.
2. The transcript had no bottom anchor or follow-latest behavior, making a
   streamed answer appear to run below the visible window.
3. The hero and sidebar used a generic purple star/orb that did not match the
   Smara product mark.
4. The live activity rail had no explicit inner scroll boundary and its event
   list could become difficult to inspect after several turns.
5. There was no small way to clear stale activity without leaving Chat.

The fix is now in the desktop shell: the main panel, chat column, transcript,
activity rail, settings pages, and provider dialog each have an explicit
bounded scroll owner; streamed messages follow a bottom anchor; the existing
green Smara mark is bundled and reused for the sidebar, hero, and assistant
avatars; activity has its own scroll area and a Clear action. These are UI-only
changes; the executor, hosted API, and MemoryOS pipeline are unchanged.

### 2026-08-28 — Hosted sign-in and cloud-chat connection repair

- Normalized legacy public URLs so `https://ai.syntarus.com/` and
  `/smara/` are repaired to the API endpoint `/smara-api` before sign-in,
  pairing, health checks, task loading, and hosted chat. Custom API origins are
  left unchanged.
- Made the desktop health check require Smara JSON with `ok=true`; a web page
  returning HTTP 200 can no longer appear as a connected API. Sign-in now uses
  the same token-file resolver as hosted chat, preventing a successful browser
  approval from being written where the chat path cannot read it. The top-bar
  Sign in action starts the browser approval directly and prevents duplicate
  sign-in attempts.
- Hardened hosted model-profile parsing for dotenv/Compose deployments that
  preserve escaped JSON quote markers. Normal JSON remains the primary format;
  the compatibility path does not alter the provider schema or expose secrets.
- Verified locally with 123 Smara tests, five provider-routing tests, six Rust
  bridge tests, a passing frontend build, and a rebuilt/restarted Windows
  release. The released app reports `Hosted connected`, and a native hosted
  chat turn returns `OK`.
- Deployed commit `e7a2c38` to staging. Live health, authenticated task loading,
  Grok profile resolution, and `/v1/chat/stream` all pass. No MemoryOS files or
  data were changed.

### 2026-08-28 — Sarvam hosted profiles, vision, and OCR routing

- Added provider-native authentication so profiles can use either
  `Authorization: Bearer` or Sarvam's `api-subscription-key` without exposing
  credentials to clients. Research synthesis and worker execution use the
  same profile boundary.
- Added hosted profile slots for `sarvam-105b` (`/v1`), `glm5.2` (`/v2`), and
  `gemma4` (`/v2`). The desktop picker now labels the reasoning and vision
  profiles; Grok remains the safe default until a Sarvam smoke passes.
- Added bounded Sarvam Document AI OCR for PDF/image captures. It submits a
  job, polls completion, downloads the result archive, and stores only a
  capped text artifact. OCR is deliberately not a chat profile.
- The Smara Web quick-capture dialog now accepts PDFs and lets the user choose
  automatic processing, image description, or OCR; the selected mode is sent
  to the same task-backed capture worker.
- Added regression coverage for Sarvam headers, profile capabilities, and a
  complete mocked OCR round trip. MemoryOS remains untouched. Live Sarvam
  verification is pending the operator placing a fresh key in the VM and
  confirming any GLM/Gemma beta entitlement.

### 2026-08-28 — Canonical public route cleanup

- Confirmed the live root at `https://ai.syntarus.com/` is served by the
  independent Smara frontend on port 8081. The `/smara/` URL was only a
  compatibility alias and is now redirected to the root; it is not a second
  application.
- Kept `/smara-api/*` because it is the backend API used by the web app, CLI,
  and desktop. Removing this path without replacing it would make authenticated
  chat, task control, and pairing fail.
- Retired the public `control-staging.syntarus.com` service; stale links are
  redirected to the canonical root while its Cloudflare DNS record is removed.
- Updated CLI and desktop browser approval URLs to open the root, and updated
  the embedded control fallback to use the same-origin Smara API route.
- Verification: 128 Python tests, frontend production build, and 6 native Rust
  tests pass locally. The live Sarvam 105B endpoint returned HTTP 200 from the
  running API container.

### 2026-08-28 — Duplicate control-surface removal

- Retired the API-hosted static control app (`/app/`) and removed its unused
  frontend iframe component. The canonical React Smara shell at
  `https://ai.syntarus.com/` is now the only public UI; `/smara-api/` is API
  only and `/smara/` is an explicit `410 Gone` compatibility response.
- Removed the obsolete staging Caddy site and deployment mount template.
  `control-staging.syntarus.com` is DNS-retired; the Compose project remains
  internal and continues to run the API, worker, scheduler, and data stores.
- Updated CLI, desktop, push-notification, and scheduler links to open the
  canonical root. No MemoryOS source, schema, or data was changed.
- Final verification for this cleanup: run the full Smara Python suite,
  frontend production build, native desktop tests, Caddy validation, and live
  root/API/retired-route checks before treating the root as the only supported
  entry point.
