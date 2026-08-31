# Smara Implementation Plan

**Status:** focused beta + latency architecture plan<br>
**Updated:** 2026-08-30<br>
**Repository:** `sujalkherawat25-stack/smara`<br>
**Public product:** `https://ai.syntarus.com`<br>
**API:** same-origin `https://ai.syntarus.com/smara-api`

This is the single active plan for Smara. Historical implementation notes,
experimental dashboards, and unrelated integration work are intentionally not
part of this document.

## 1. Product vision

Smara is one personal agent with two coordinated surfaces:

1. **Hosted Smara (control plane):** chat, planning, research, task graph,
   approvals, schedules, monitoring, and shared Syntarus memory. Hosted work
   continues when the user's computer is closed.
2. **Smara Desktop (local data plane):** an optional paired Windows app that
   performs approved work on the user's computer: files, terminal, browser,
   and future personal integrations. It communicates outbound to Smara and
   never accepts an unsolicited inbound connection.

The target experience is a dependable agent that can inspect a workspace,
plan work, make reviewable changes, run checks, verify the result, and show the
complete task history. The desktop should feel like a local coding/work agent,
while the hosted service remains the durable coordinator.

## 2. Non-negotiable boundaries

- MemoryOS remains the memory product and core pipeline. Do not change its
  Qdrant, Neo4j, Redis, schemas, Continuum APIs, or existing public endpoints
  to make Smara work.
- Smara accesses long-term memory only through the public Syntarus SDK/API in
  `src/smara/syntarus_adapter.py`.
- The hosted VM may use operator-owned LLM and public-search credentials only.
  User browser sessions, private OAuth/API credentials, files, and terminal
  access stay on the paired desktop.
- Local work requires a declared capability, an approved durable task step,
  and the desktop's local allowlist. An allowlist is eligibility, not approval.
- Side effects must be durable, approval-aware, idempotent, bounded, and
  visible in the task activity and result/artifact record.
- No arbitrary code execution, unrestricted browser automation, or hosted
  sandbox is enabled in this beta.
- The Memento agent source is retained only as a rollback/reference asset in
  the MemoryOS repository. Native Smara auth and Telegram code live here; the
  old agent routes and worker are no longer public or running.

## 3. Architecture today

```text
Smara Web + Smara CLI
          |
          v
Hosted Smara API / Postgres / Redis / worker / scheduler
  - agent runtime and task graph
  - approvals, leases, retries, events, artifacts
  - public research and provider routing
  - Syntarus SDK memory adapter
          |
          | outbound HTTPS, scoped pairing token
          v
Smara Desktop on the user's PC
  - local file, terminal, browser capabilities
  - local encrypted credential vault
  - local model profiles for private direct chat
```

Hosted web, CLI, and hosted tasks share the same account, task graph, and
Syntarus memory boundary. The desktop has no second task database or long-term
memory engine. Local execution results return to the hosted task as bounded
proof/artifacts.

## 4. Implemented and verified

### Hosted control plane

- Provider-neutral hosted runtime with Grok and operator-configured Sarvam
  profiles.
- Deterministic triage, bounded tool reasoning, streamed answers, transient
  retry, stream fallback, tool/output/time budgets, and duplicate-call
  rejection.
- Durable tasks, dependency steps, leases, heartbeats, retries, cancellation,
  idempotency, approvals, event streams, dead letters, schedules, and result
  summaries.
- Tavily/public research with bounded retrieval, SSRF protection, source
  verification, citations, evidence, and Markdown artifacts.
- Account-scoped authentication and task/conversation APIs.
- Syntarus SDK memory retrieval and write-back; MemoryOS internals are not
  imported.
- Hosted attachments: up to 100 MB per file, 150 MB per upload batch, up to 10
  files. Text extraction is bounded; images up to 12 MB may be sent to a
  configured vision model. Attachments are transient context, not automatic
  long-term memory.
- Personal hosted integrations are fail-closed in the current deployment.
- Native Smara session auth (Google GIS, revocable httpOnly cookie, shared
  account ids) and a Smara-owned Telegram poller are deployed. The legacy
  Memento worker is removed and `/v1/memento/*` plus legacy `/v1/auth/*` are
  retired with an explicit 410 response. MemoryOS core APIs remain available.
- **New operator console:** `/admin` is served by the Smara Web bundle and
  protected by a separate `SMARA_OPERATOR_SECRET` session. It presents Smara
  control-plane data and Syntarus context-plane health in separate sections;
  it never joins the databases or exposes keys, OAuth grants, task content, or
  raw memory by default. The old Memento dashboard bookmark redirects here.

### Smara Desktop

- Native Tauri/React Windows shell around the outbound-only Python executor.
- Pairing, capability declaration, pause/resume/stop/revoke, status, bounded
  rotating logs, refresh, and visible task activity.
- `local_file_read` and atomic `local_file_write` inside approved roots;
  read-only tree, text, filename, and Git summary inspection are bounded.
- Local document production is part of the existing `local_file_write`
  approval scope: DOCX reports, XLSX workbooks, PPTX briefings, PDF reports,
  PDF merge, and selected-page extraction. Outputs stay under approved roots,
  are capped at 8 MB, return a structured preview and local undo id, and
  reject scripts/macros/external links. Unicode remains supported for DOCX,
  XLSX, and PPTX; PDF safely rejects unsupported non-Latin text until the
  packaged Indic-font slice is added.
- `local_terminal` with executable allowlist, no shell operators, timeout,
  bounded output, credential aliases, and output redaction.
- `local_browser` with HTTP(S) validation and domain allowlist; it can open a
  URL, inspect bounded text/DOM, or download a bounded file without sharing
  browser cookies.
- `local_integration` (opt-in at pairing) with local, approval-gated,
  read-only Tavily search and GitHub repository listing. Provider credentials
  stay in the desktop vault and never enter hosted task state.
- Windows-account DPAPI protection for the desktop bearer and local secrets.
- Hosted Automatic/Grok/Sarvam profiles plus private Sarvam/Grok/custom
  OpenAI-compatible direct-chat profiles. Private model keys remain local.
- Unsigned NSIS package, installer shortcut, auto-start option, and a working
  browser-based sign-in/pairing flow.
- Strict permission reload during executor polling is included in the latest
  installed Desktop build; the owner PC has been refreshed and verified.

### Verification already green

- **Audit refresh — 2026-08-30 (commit `af8b18d`, deployed):** 237 Python
  tests passed (only two existing short JWT test-fixture warnings), frontend
  type-check and production build passed, Desktop production build passed, and
  all nine native Rust tests passed.
- The fixed offline routing corpus passed all nine lanes with a measured
  p50 of **0.16 ms** and p95 of **0.34 ms**. This is a deterministic
  in-process benchmark, not a claim about public-provider latency.
- A fresh signed live beta smoke passed health/readiness, task creation and
  cancellation, account isolation, local-only integration policy, direct chat,
  calculator, cited research, and desktop no-replay lease safety. The live
  Redis limiter also returned 120 successes followed by 5 bounded 429s.
- Live Grok/Tavily/research/task/approval/lease safety smokes.
- Disposable local file read/write, Python codebase test, and browser-opening
  workflow passed with the paired executor.
- Expired uncertain terminal/browser/write leases fail closed and are audited
  instead of being replayed.
- Hosted workers no longer recover desktop leases; stale/orphaned desktop lease
  rows are replaced atomically, and deterministic calculator requests emit the
  same visible tool lifecycle events as model-selected tools.
- The hosted planner can now create one bounded, sequential local workflow
  graph (inspect → plan → edit → run → verify → report); each stage remains
  approval-gated and capability-scoped on the paired desktop.
- Ordinary chat now turns a local create/edit/run request into a safe hosted
  planning task instead of claiming the local capability is unavailable. Any
  eventual desktop action is still represented by a separately visible,
  approval-gated child task.
- Local browser DOM/download and Tavily/GitHub adapter contracts are covered by
  desktop tests; live provider calls still require a user's local credential
  and paired, approved task.

### Native cutover verification — 2026-08-29

- `https://ai.syntarus.com/` serves the Smara Web bundle (200).
- `/smara-api/health` reports `native-session` auth and `telegram: true`.
- `/smara-api/v1/auth/config` returns 200; `/smara-api/v1/auth/me` returns 401
  when no session is present (expected).
- `/v1/memento/*`, legacy `/v1/auth/*`, and the old `/smara/` mount return 410.
- MemoryOS core `/v1/memory` remains available behind its existing auth.
- The Smara-owned Telegram worker polls with repeated 200 responses; the
  legacy Telegram container was removed. Live Telegram message/link checks
  still require the owner's signed-in account and bot interaction.

## 5. What remains now: active beta gates

These are the only immediate release tracks. Do them before calling the beta
stable.

### P0-A — Refresh the installed Desktop build

**Status: installed and verified on the owner PC.** The latest NSIS bundle
includes local task journaling, workspace locks, and the explicit skill
protocol. The Desktop shortcut now targets the installed release executable.
The physical drill below must use this installed build and keep the paired
state when restarting.

### P0-B — Physical Windows restart/reconnect drill

**Status: pending owner-run physical drill.** The installed executor and
server lease/cancellation logic are ready; the real laptop network-off,
application restart, Windows restart, reconnect, and revoke sequence still
needs to be exercised with the owner's paired desktop.

With the owner's real paired account:

1. Approve a harmless file read and file write.
2. Run an allowlisted terminal test and a browser test.
3. Disconnect the network while a task is waiting or running.
4. Restart the desktop app and, separately, restart Windows.
5. Reconnect and confirm the task resumes without duplicate execution.
6. Cancel a pending local step and verify cancellation reaches the PC.
7. Revoke the desktop and confirm it stops polling immediately.

### P0-C — Authenticated Smara Web shadow test

**Status: authenticated shadow green on 2026-08-29; reconnect edge case
pending.** The signed public smoke passed health/readiness, task creation and
cancel, cross-account 404 isolation, local-only integration policy, and the
no-replay desktop lease check. A real signed-in Web session also passed chat,
approval, visible task result, refresh persistence, and sign-out. A deliberate
dropped-stream reconnect still needs to be exercised.

Using a real beta account, verify native Smara sign-in, refresh, chat, task creation, task
result visibility, research evidence/artifacts, approval, reconnect after a
dropped stream, sign-out, and account isolation. The legacy route is disabled;
rollback requires restoring the saved Caddy configuration explicitly.

### P0-D — Edge and provider checks

- Review distributed Cloudflare/edge rate limits using an authenticated browser
  session; the backend Redis limiter is already verified.
- **Sarvam standard chat smoke passed on 2026-08-30.** The configured GLM-5.2
  reasoning profile correctly targets Sarvam `/v2`, but returned HTTP 400: its
  API key has not been granted Sarvam beta access. Keep GLM-5.2 and Gemma 4
  visibly beta-gated until Sarvam whitelists the operator key; this is not a
  fallback-to-Grok condition.
- Record provider failures as actionable user-facing errors without exposing
  upstream secrets or raw error bodies.

### P0-E — Operator console and product separation

**Status: deployed and configured on 2026-08-30.** The retired Memento
dashboard now redirects to the Smara-owned `/admin` console. It uses a separate
operator session, read-only aggregate endpoints under `/v1/admin/*`, clear
Smara/Syntarus data-plane boundaries, bounded account/task metadata, graceful
empty/error states, refresh, and responsive tables. The live session endpoint
reports `configured: true`; the operator secret remains VM-only and is never
shown in this repository or browser URL.

### Audit boundary — 2026-08-30

All repeatable repository, package, route, configuration, and signed live
checks are green at `af8b18d`. The following cannot be honestly automated by
the repository or VM because they require an owner-controlled session or
device state: P0-B's actual Windows/network restart sequence, P0-C's
deliberately interrupted signed-in browser stream, and P0-D's authenticated
Cloudflare dashboard review. GLM-5.2 and Gemma 4 also remain beta-gated by
Sarvam until the operator key is whitelisted; this is an upstream entitlement,
not an implementation failure.

## 6. Performance and architecture upgrade plan (active next phase)

The goal is not to make Smara "fast" by removing memory, tools, verification,
or approvals. The goal is to stop paying the full agent-loop cost for work that
does not need it, reuse warm resources, and wake workers/executors immediately
instead of repeatedly polling.

### 6.1 Current hot-path findings

These findings are from the current Smara source, not assumptions:

| Path | Current avoidable cost | Consequence |
|---|---|---|
| Hosted chat | `_agent_runtime()` creates a provider and Syntarus client for every turn, and closes the memory client afterward. | No warm memory/provider connection across turns. |
| Provider calls | `OpenAICompatibleProvider.complete()` and `stream_complete()` each create a new `httpx.AsyncClient`. The chat route creates a second client for tools. | Repeated DNS/TCP/TLS setup and duplicate pools. |
| Simple chat | Deterministic triage skips the tool planner, but memory retrieval still runs for greetings and self-contained questions. | A trivial turn waits for an unnecessary remote lookup. |
| Tool chat | The JSON planner can call the model once per iteration and then call the model again to stream the final answer. | A one-tool request can use two or three model round trips. |
| API state | `PostgresTaskStore._connect()` opens a new synchronous Psycopg connection for every store operation, including calls made directly inside async FastAPI handlers. Conversation history and summary are separate reads. | Connection overhead and event-loop blocking grow under concurrent chat. |
| Hosted tasks | One worker processes one claimed step at a time and sleeps for up to two seconds when idle. | Small tasks can wait before starting; unrelated tasks cannot use spare capacity. |
| Task SSE | The event stream rereads the full event list and task row every second. | Up to one-second UI lag and unnecessary database work. |
| Desktop execution | The executor keeps one HTTP client, but claims every two seconds and rereads its complete state file on every poll. | Approved local work can wait about two seconds before the PC notices it. |
| Desktop UI | Tauri creates fresh `reqwest::Client` objects for connection checks, task reads, and chat streams; Activity refreshes on an interval. | Avoidable connection setup and delayed state visibility. |

### 6.2 Performance budgets

Measure warm-system performance separately from provider latency. A regression
test must never pretend an external provider is deterministic.

| Measurement | Beta target |
|---|---|
| SSE connection to first Smara status frame | p95 under 200 ms |
| Internal dispatch overhead before the provider request | p95 under 250 ms |
| Exact deterministic tools (`current_time`, safe arithmetic) | p95 under 500 ms end to end, no LLM call when a model adds no value |
| Warm simple chat time to first visible token | p50 under 2.5 s, p95 under 5 s with a healthy configured provider |
| Simple chat model calls | exactly 1 |
| One read-only tool request | at most 2 model calls: choose tool, then answer |
| Task creation to hosted-worker claim | p95 under 500 ms |
| Approved online desktop task to claim | p95 under 750 ms |
| Task event commit to visible Web/Desktop update | p95 under 500 ms |
| Dropped client stream | provider work cancelled or durably detached within 2 s; never duplicated |

Every final SSE `done` frame must include safe timing fields for `auth`,
`conversation_load`, `memory`, `provider_ttft`, `tools`, `persist`, and `total`.
Do not put prompts, tokens, credentials, raw memory, or attachment contents in
logs/metrics.

### 6.3 Target request lanes

Add one deterministic router in `src/smara/agent_routing.py`. It returns a
typed `RouteDecision`; it does not call an LLM and it never grants authority.

1. **Lane A — deterministic:** exact time and arithmetic requests. Execute the
   existing safe registered tool directly. Render its bounded result without a
   model unless the user also asks for explanation/comparison.
2. **Lane B — direct answer:** greetings and self-contained conversational
   questions. One streamed provider call. Skip Syntarus only when the route
   explicitly marks `memory_needed=False`.
3. **Lane C — memory answer:** personal/history/reference questions. One
   bounded Syntarus retrieval followed by one streamed provider call.
4. **Lane D — read-only tool:** search/fetch/connected read operations. Use the
   bounded planner and registered tools, then one final streamed answer.
5. **Lane E — durable task:** long, multi-step, side-effecting, local, scheduled,
   or approval-required work. Create/continue the task graph; do not execute it
   inside chat.

Escalation is one-way within a turn: A/B/C may escalate to D or E when the
request is ambiguous, but a fast lane may never bypass tool registration,
approval, account/workspace scope, local permissions, or task durability.
If routing confidence is low, choose the more capable lane. Attachments,
explicit citations, current information, and action verbs are never treated as
plain chitchat.

### 6.4 Luna-ready implementation packages

The packages below are intentionally small enough for GPT-5.6 Luna to implement
one at a time. Each package ends with tests and one clean commit. Luna must read
this file and `AGENTS.md` first, preserve unrelated working-tree changes, and
must not push or deploy without explicit authorization.

#### PERF-0 — Baseline and safe timing contract

**Files:** `src/smara/agent_events.py`, `src/smara/api.py`,
`src/smara/observability.py`, new `src/smara/performance.py`, new
`scripts/benchmark_smara.py`, new `tests/test_performance_contract.py`.

- Add a request-scoped monotonic `TimingTrace` with named spans and a generated
  request id. Accept a valid incoming `X-Request-ID`, otherwise generate one;
  return it on HTTP/SSE responses and propagate it to task events.
- Extend `done` with the safe timing map while preserving all existing fields.
- Add an offline benchmark mode using fake memory/provider/tool adapters and an
  opt-in live mode that never prints keys or response bodies.
- Record provider call count, memory call count, tool call count, time to first
  status, and time to first token.
- Add a fixed corpus: greeting, self-contained answer, memory recall,
  calculator, current time, one search, multi-tool research, durable desktop
  request, and cancellation.

**Done when:** existing clients still parse the stream; fake benchmarks are
stable in CI; live results can be saved as a redacted JSON artifact; no user
content appears in logs.

#### PERF-1 — Warm resource lifecycle

**Files:** new `src/smara/runtime_resources.py`, `src/smara/api.py`,
`src/smara/agent_runtime.py`, `src/smara/tool_registry.py`,
`src/smara/syntarus_adapter.py`, `src/smara/worker.py`,
`src/smara/research.py`, tests in `tests/test_agent_runtime.py` and new
`tests/test_runtime_resources.py`.

- Create FastAPI lifespan-owned resources: one bounded shared `httpx.AsyncClient`
  for providers/tools, one long-lived Syntarus SDK client, and an immutable
  parsed model-profile catalogue.
- Inject the shared client into `OpenAICompatibleProvider`; provider methods
  must not create or close their own client. Keep a separately owned client
  only for isolated tests/CLI processes.
- Give the worker one shared provider/tool HTTP client and one Syntarus client
  for its process lifetime.
- Configure explicit connect/read/write/pool timeouts, keep-alive limits, and a
  small connection cap. Retry only before the first streamed token.
- Close resources exactly once on shutdown and cancel in-flight work cleanly.

**Done when:** two sequential warm chats reuse the same HTTP/Syntarus clients;
no request closes a shared client; stream fallback and partial-stream
no-duplicate tests remain green.

#### PERF-2 — Pooled, non-blocking state access

**Files:** `pyproject.toml`, `src/smara/store.py`, new
`src/smara/store_async.py`, `src/smara/api.py`, `src/smara/auth.py`,
`src/smara/admin.py`, `migrations/`, and store/API tests.

- Add `psycopg_pool.ConnectionPool` to `PostgresTaskStore` and reuse it instead
  of opening a TCP connection for each method. SQLite remains test/dev only.
- Add a bounded async facade that runs the existing synchronous state machine
  off the FastAPI event loop. Do not rewrite the proven lease state machine in
  the same package.
- Combine conversation owner/history/summary loading into one store call and
  one pooled connection. Persist the user/assistant exchange in one transaction.
- Add/verify indexes for ready task steps, account task lists, task events,
  conversation turns, active executor leases, and approved integration work.
- Expose pool saturation and query-duration aggregates only in the operator
  console; never expose SQL or parameter values.

**Done when:** an artificial slow database call does not stall an unrelated
health/chat coroutine; concurrent claims still use `SKIP LOCKED`; account
isolation and lease tests stay green.

#### PERF-3 — Capability-preserving fast lanes

**Files:** new `src/smara/agent_routing.py`, `src/smara/agent_runtime.py`,
`src/smara/agent_step.py`, `src/smara/tool_registry.py`,
`src/smara/agent_events.py`, tests in `tests/test_agent_runtime.py`,
`tests/test_agent_step.py`, and new `tests/test_agent_routing.py`.

- Implement the five lanes in Section 6.3 with an explicit reason, confidence,
  `memory_needed`, `tools_allowed`, and `durable_required` result.
- Add direct dispatch only for exact registered, side-effect-free deterministic
  tools. Never match arbitrary natural-language expressions to terminal or
  integration actions.
- For Lane B, issue one streaming call. For Lane C, retrieve then issue one
  streaming call. For Lane D, cap a one-tool request at planner + final.
- Remove the extra final model pass when the planner already returned a final
  answer and no tool observation needs synthesis. Stream that answer as a
  single safe token/frame or use a provider-native stream on the deciding pass.
- Keep the existing full bounded loop for multi-tool/ambiguous requests and
  emit `route.escalated` when a fast lane promotes itself.
- Keep tool schemas stable and grouped by route so the model sees only relevant
  tools, not the entire registry.

**Done when:** call-count tests prove A=0, B=1, C=1, one-tool D<=2; attempts to
route writes/local work through A-D become a durable task; quality/evaluation
cases match the existing answers or improve them.

#### PERF-4 — Selective memory and bounded context

**Files:** `src/smara/agent_routing.py`, `src/smara/agent_runtime.py`,
`src/smara/syntarus_adapter.py`, `src/smara/store.py`, tests in
`tests/test_conversations.py` and `tests/test_agent_runtime.py`.

- Skip memory only for confidently self-contained Lane A/B turns. Always use
  memory for recall language, named prior facts, pronouns referring to history,
  and explicit Knowledge/Memory mode.
- Apply a short hard timeout to memory lookup; on timeout, answer honestly and
  emit `memory.unavailable` without failing ordinary chat.
- Build one context-budget allocator for attachments, current user message,
  conversation summary, recent turns, memory, and tool observations. Never let
  old history truncate the current message or explicit attachment content.
- Stop copying the same attachment text into both system and user prompts.
- Cache only immutable configuration and parsed summaries. Do not cache raw
  per-user memory across accounts; any optional result cache must be
  account/workspace/query keyed, very short lived, and invalidated on memory
  write.

**Done when:** greetings make zero Syntarus calls, recall cases still make one,
attachments appear once in the provider payload, and workspace/account
isolation tests cover every cache key.

#### PERF-5 — Event-driven hosted tasks

**Files:** new `src/smara/work_signals.py`, `src/smara/store.py`,
`src/smara/worker.py`, `src/smara/scheduler.py`,
`src/smara/integration_worker.py`, `src/smara/api.py`, `docker-compose.yml`,
`migrations/`, and task/schedule/integration tests.

- Keep PostgreSQL as the only task source of truth. Use Redis pub/sub only as
  an advisory wake-up channel; a lost signal must be repaired by a low-frequency
  database poll.
- Publish a work signal after create, approve, retry, schedule fire, lease
  recovery, and integration approval. Workers subscribe and claim immediately;
  retain a 5-second fallback poll.
- Add bounded hosted-worker concurrency (`SMARA_WORKER_CONCURRENCY`, default 4)
  with one task per coroutine and existing database leases/idempotency intact.
  Reserve separate semaphores for LLM, research fetch, and integration calls so
  one slow class cannot starve all work.
- Add `events_after(task_id, event_id)` and wake task SSE from the signal rather
  than rereading the full event list every second.
- Add admission/backpressure: when all provider slots are full, work remains
  queued visibly; it is not accepted into unbounded in-process tasks.

**Done when:** create-to-claim and event-to-UI budgets pass locally; killing a
worker requeues by lease; lost Redis signals recover through polling; four
independent read-only tasks run concurrently without duplicate claims.

#### PERF-6 — Low-latency Desktop channel

**Files:** `src/smara/api.py`, `src/smara/store.py`,
`src/smara/desktop_executor.py`, `src/smara/local_agent.py`,
`apps/desktop/src-tauri/src/main.rs`, `apps/desktop/src/api.ts`,
`apps/desktop/src/App.tsx`, and Desktop/API tests.

- Add bounded long polling to executor claim (`wait_seconds` max 25). Perform
  an immediate database claim, wait on the advisory account/executor signal,
  then claim again. Heartbeats and lease ownership remain unchanged.
- Replace unconditional two-second desktop claim polling with long polling plus
  jittered reconnect backoff. Cancellation must interrupt terminal/browser/
  integration work through the existing checkpoint contract.
- Cache parsed desktop settings by file modification time; reload and revalidate
  atomically when the file changes. Missing/corrupt/revoked state still stops
  the executor immediately.
- Store one application-wide `reqwest::Client` in Tauri managed state for
  health, task, chat, and sign-in traffic. Add an abort handle per active chat.
- Replace Activity's interval-only refresh with a hosted task/event subscription
  plus a low-frequency repair refresh.
- Preserve one-writer-per-workspace locking; allow bounded parallel read-only
  local inspections only after distinct lease/idempotency tests exist.

**Done when:** an already-online desktop normally claims approved work in under
750 ms; network loss/reconnect does not duplicate a write; revocation ends the
long poll; app close cancels chat without leaving a hidden duplicate response.

#### PERF-7 — UX latency and progressive results

**Files:** `frontend/src/stores/chatStore.ts`,
`frontend/src/components/SmaraWorkPanel.tsx`, `frontend/src/lib/smaraWork.ts`,
Desktop chat/activity components, and frontend tests.

- Render the user message and a route-specific status immediately; do not show
  fake reasoning text.
- Coalesce token rendering to animation frames so long streams do not trigger a
  React render per token.
- Keep partial output visibly marked if a stream drops; provide one explicit
  Retry action with the same conversation id and a new request id.
- Show queue, claim, provider, tool, local execution, verification, and final
  result as stable activity states. Collapse noisy heartbeat/progress events.
- Subscribe only while the relevant chat/task panel is visible; reconnect with
  the last durable event id and run one repair fetch after reconnect.

**Done when:** long answers remain scrollable/responsive, no duplicate tokens
appear after reconnect, and task/result visibility reaches Web and Desktop
within the Section 6.2 budget.

#### PERF-8 — Evaluation, shadow rollout, and rollback

**Files:** new `tests/evals/`, `scripts/benchmark_smara.py`,
`PRODUCTION_GATE_STATUS.md`, `PRODUCTION_RUNBOOK.md`, `.env.example`.

- Add feature flags for fast routing, pooled resources, work signals, worker
  concurrency, and desktop long polling. Defaults stay conservative until each
  package passes.
- Run the fixed corpus against old and new routing. Compare answer quality,
  citations, tool correctness, provider-call counts, TTFT, total latency, and
  cost. Fast routing fails promotion if safety or task success regresses.
- Shadow only the route decision first; log the lane and timing without changing
  behavior. Then enable owner account, beta cohort, and finally all beta users.
- Record exact rollback toggles. A rollback disables the optimization but keeps
  tasks, approvals, events, and account data intact.

**Done when:** the owner corpus and authenticated account-isolation suite are
green, p95 targets are met on the live VM, and every optimization has a tested
configuration rollback.

**Implementation status (2026-08-30): PERF-0 through PERF-8 are implemented
and locally verified.** The package ledger, exact rollback switches, and the
remaining live promotion evidence are tracked in
[`SMARA_PERFORMANCE_STATUS.md`](./SMARA_PERFORMANCE_STATUS.md).

### 6.5 Architecture after the performance phase

```text
Web / CLI / Desktop chat
        |
        v
Auth + request id + timing trace
        |
        v
Deterministic lane router
   |       |        |             |
   A       B/C      D             E
 exact   one-pass  bounded      durable task graph
 tool    answer    read tools   + approval
   |       |        |             |
   +-------+--------+-------------+
                   |
          shared warm resource layer
       HTTP pools / Syntarus / DB pool
                   |
     PostgreSQL source of truth + Redis wakeups
                   |
       hosted workers / paired local executor
```

The hosted planner remains the single coordinator. Desktop is still an
outbound-only executor, not a second cloud controller. Syntarus remains a
separate memory product accessed only through its SDK/API.

## 7. Local agent capability work (next implementation phase)

The desktop currently exposes safe primitives, not a complete local agent. Build
the following skills on top of the existing capability and approval contracts.

### L1 — Workspace inspection (read-only)

- **Implemented:** bounded directory-tree listing and literal text search
  inside an approved root (depth, file, match, and output limits; binary files
  and symlinks skipped).
- **Implemented:** read bounded files with hashes.
- **Implemented:** bounded filename search and Git branch/status/diff-stat/
  recent-commit summaries (Git inspection requires `git` in the local
  executable allowlist).
- **Implemented in this change (2026-08-30):** file reads and tree listings
  expose bounded media type/kind/encoding metadata without sharing content;
  Git-backed terminal results and workspace summaries include bounded
  SHA-256/size proofs for changed files (large or missing files are marked
  unavailable rather than read into memory).

### L2 — Reviewable workspace editing

**Status: implemented and unit-tested on 2026-08-29.** The paired executor now
supports bounded `preview_only` planning plus write/append/patch/replace,
rename/move, delete, and guarded undo operations inside approved roots.
Mutations compute a bounded unified diff/preview before applying an atomic
replacement, and return the preview, resulting hash, and a local-only undo id.
Delete snapshots and prior file contents stay in the desktop app-data undo
ledger; undo refuses to overwrite a file that changed after the original edit.
Traversal, symlink escapes, unapproved roots, oversized content, destination
collisions, non-UTF-8 patches, and ambiguous replacements are rejected. The
256 KB desktop file-operation limit remains unchanged pending a larger bounded
streaming design.

### L3 — Test/build skill

**Status: implemented and unit-tested on 2026-08-29.** In addition to the
lease-scoped progress/cancellation path, the executor now exposes deterministic
named recipes (`python.test`, `python.compile`, `node.test`, `node.build`,
`rust.test`, `rust.check`, and `git.diff-check`). Recipes still require the
same explicit executable allowlist and never accept shell text or extra flags.
Each run returns a bounded exit code/output record, before/after Git changed
files when Git is allowlisted, and optional explicitly requested artifact
metadata (relative path, byte count, SHA-256; artifact contents stay local).
Desktop Activity now renders these structured results, previews/diffs, output,
and artifact hashes instead of only showing a generic completion row.

### L4 — Controlled browser skill

Upgrade the current URL launcher to an explicitly local, permissioned browser
adapter that can, when enabled:

- **Implemented:** `inspect_text` fetches a page from an approved domain with
  no browser cookies/session, no redirects, a 1 MB fetch limit, and bounded
  extracted text. `open` remains an explicit system-browser action.
- **Implemented:** `inspect_dom` returns a bounded semantic DOM summary, or a
  simple tag/`#id`/`.class` selection, with safe attributes and resolved links.
  It never executes page scripts or shares a browser session.
- **Implemented:** `download` streams an approved URL to an approved local
  folder, rejects redirects and symlink destinations, supports explicit
  overwrite only, cleans partial files on cancellation, and caps downloads at
  50 MB. Inspection and downloads include source URL, byte count, and a
  SHA-256 proof hash for the hosted task ledger.
- Screenshots remain deferred until the desktop has a separately reviewed
  local capture adapter; the current executor deliberately has no cookie or
  interactive-browser automation surface.

Browser sessions and cookies must remain local. This is separate from hosted
public URL fetching through Tavily/HTTP.

### L5 — Local credential adapters

**Status: first slice implemented and unit-tested on 2026-08-29.** The
encrypted credential vault now powers an explicit `local_integration`
capability for approval-gated, read-only Tavily search and GitHub repository
listing. Provider aliases are fixed (`TAVILY_API_KEY` and `GITHUB_TOKEN`),
responses are bounded and secret-free, provider failures are classified without
returning upstream bodies, and the key is sent directly from the desktop to
the provider. No adapter uploads a user's secret to the VM.

Telegram send, Gmail, Calendar, and Drive remain separate adapters: each needs
its own local consent/token flow, scope limits, redaction contract, and tests
before it is enabled.

### L6 — Artifact and result workspace

- **Implemented:** Desktop Activity task selection now fetches the durable task
  record and displays the result, steps, recent activity/progress, and artifact
  names. The existing Web artifact endpoint remains the source of truth.
- **Implemented in this change:** Web now shows structured local operation
  previews/diffs, changed files, output, and SHA-256 values inline; failed
  tasks expose a safe Retry action. Desktop already renders the same structured
  artifact fields and now receives the artifact hash in its typed task detail.
  Retention remains server-controlled and is not a download surface yet.
- The task result is now read from the durable `result_summary` record, so
  “completed” is distinct from “result produced” when no answer was generated.
- **Implemented:** failed tasks can be retried through the durable task API;
  uncertain local steps reconcile against the authenticated hosted step-status
  endpoint before reconnect claiming, while failed/dead-lettered work remains
  visible for explicit operator action.

## 8. Local agent and task architecture

### Current state

- The hosted runtime is the default planner and coordinator.
- Direct private desktop model profiles provide local chat, but not a durable
  local task planner/tool loop.
- Hosted tasks survive a closed laptop. Local steps wait for the executor to
  reconnect.
- The task graph already handles leases, retries, cancellation, approvals,
  idempotency, events, and dead letters.

### Required upgrades

1. Add a clear local skill protocol: capability, input schema, approval level,
   time/output/byte limits, idempotency key, result schema, artifact list,
   redaction rules, and failure state.
2. Add richer task decomposition and verification so a request can become
   inspect → plan → edit → run → verify → report. **Implemented as a bounded
   first slice (2026-08-30):** the hosted planner can request one explicit,
   sequential local workflow graph with these stage labels; the paired desktop
   executes only after the normal approval gate and per-capability allowlists.
   Adaptive verification/planning remains a later enhancement.
3. Add durable local intent/queue state for disconnected desktops; never replay
   uncertain side effects automatically.
4. **Implemented in this change:** local skill contracts, a bounded task
   journal, fail-closed uncertain-side-effect handling, per-workspace
   cross-process locks, and an authenticated step-status reconciliation pass
   prevent duplicate replay and stale uncertain entries after reconnect.
5. Keep the hosted planner as the default. Add a local fallback planner only
   after the skill protocol, task reconciliation, and local memory policy are
   stable; otherwise two independent agent brains will diverge.

## 9. Memory plan

### Working today

- Hosted Web, CLI, and hosted tasks use the same account-scoped Syntarus memory
  adapter.
- Task state, events, artifacts, and conversation history are hosted and
  durable.
- Desktop execution results can be attached to the hosted task and later
  included in hosted memory flows.

### Deliberate gap

There is no separate encrypted local long-term memory database. Private local
chat is not automatically synchronized into hosted memory. This is currently a
privacy boundary, not an accidental second implementation.

### Decision required before offline-agent work

**Decision for the beta:**

- **Adopted:** hosted memory only; local execution keeps only a bounded,
  redacted journal, logs, undo snapshots, and task artifacts. No local
  long-term memory is uploaded or silently synchronized.
- **Later offline enhancement:** add an encrypted local cache for local
  conversation summaries, workspace facts, pending intents, and explicit
  remember/recall/forget controls. Sync must be opt-in and conflict-aware.

Also require server-enforced workspace/account filters before claiming secure
cross-project isolation; current metadata filters are not sufficient for that
claim.

## 10. Tools, skills, and plugins

### Available hosted tools

- `current_time`
- `calculate`
- `research.deep` (deterministic multi-angle search, fetch, deduplication,
  source diversity, and citation-labelled evidence for explicit research
  requests)
- `research.web_search`
- `research.fetch_url`
- `desktop.request_action` through an approved durable task
- `desktop.request_workflow` through one approved, sequential durable task graph

Personal integration tools are intentionally disabled in the hosted release.
The plugin catalogue is declarative; external MCP/plugin code is not yet an
executable runtime.

### Rules for every new tool/skill

- Explicit JSON schema with `additionalProperties: false`.
- Declared read-only/side-effecting status and approval requirement.
- Account/workspace scope and local/hosted placement.
- Hard timeout, byte/output limit, retry and idempotency behavior.
- Secret redaction and safe error classification.
- Durable activity event and structured result/artifact.
- Unit, failure, concurrency, restart, and account-isolation tests.

## 11. Production hardening (after the beta)

These are deferred by owner decision and must remain disabled/documented:

1. Sentry DSN, alert routing, and a real event-delivery test.
2. Authenticode signing, trusted update channel, and clean-machine install
   testing.
3. External VM secret manager with operator-secret rotation drill.
4. Encrypted off-host backups with a schedule, retention, and recurring restore
   test.
5. Distributed edge rate-limit policy and server-enforced metadata isolation.
6. Formal cost/resource budgets and artifact retention.
7. Security, fault-injection, concurrency, rollback, and multi-device tests.
8. A separately isolated hosted sandbox only if a future product decision
   requires remote code execution. `SMARA_SANDBOX_ENABLED` stays false for the
   current privacy model.

## 12. Execution order

1. Complete the physical restart/reconnect/cancel/revoke drill with the paired
   Windows PC (P0-B).
2. Deliberately drop a real signed-in Web stream and confirm cursor-based
   reconnect without duplicated output or work (P0-C).
3. Complete the authenticated Cloudflare edge-limit review, one low-cost
   Sarvam chat/reasoning smoke, and a provider-failure UX check (P0-D). The
   hosted research path now uses `research.deep` for detailed/citation-led
   prompts; configure a primary search key and optional independent fallback
   before judging research quality.
4. Capture live redacted p50/p95 timing evidence for the enabled fast-path
   flags and retain the documented rollback values.
5. Run the broader agent evaluation corpus: chat, research, codebase changes,
   approvals, failures, attachments, reconnects, cancellations, and duplicate
   prevention.
6. Decide whether the later offline local-memory enhancement is needed; do not
   create a second memory system by default.
7. Only after the beta gates are green, reopen the deferred production
   hardening gates in Section 11.

## 13. Readiness and definition of done

Current honest estimate (updated 2026-08-30):

- Focused hosted + desktop beta code: **implemented**. The release gate remains
  open only for P0-B through P0-D and live timing evidence.
- Desktop executor foundation: **~94%**; the physical restart/reconnect drill,
  richer adaptive planning, signing, and update trust remain.
- Full local-agent experience: **not complete** until adaptive verification,
  additional consented adapters, optional offline memory, and the deliberately
  deferred browser capture surface are implemented and tested.
- Full production promotion: **not complete** while Section 11 gates are
  deferred.

Smara is ready to replace Memento as the primary product when:

- Web, CLI, and hosted tasks share one authenticated agent, task graph, and
  Syntarus memory boundary.
- A request can become a visible plan with progress, approvals, evidence,
  artifacts, cancellation, retry, and a clear final result.
- Desktop local work follows inspect → approve → execute → verify, survives
  restart/reconnect without duplicate side effects, and keeps secrets local.
- Workspace editing, testing, browser inspection, and artifact handling are
  reviewable and bounded.
- Authentication, isolation, backups, alerts, rate limits, budgets, rollback,
  and the required release gates are verified.
- MemoryOS remains unchanged and Memento is retained only as rollback/reference.
