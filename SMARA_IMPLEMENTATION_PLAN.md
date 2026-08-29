# Smara Implementation Plan

**Status:** focused beta plan<br>
**Updated:** 2026-08-29<br>
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
- The Memento agent and Telegram worker are migration-only rollback assets.
  Native Smara auth/Telegram code now lives in this repository; the old
  runtime must not remain public after the live shadow checks pass.

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
  account ids) and a Smara-owned Telegram poller are implemented locally. The
  live VM still needs the staged deployment and one real link/chat check before
  the legacy Memento worker and `/v1/memento/*` routes are disabled.

### Smara Desktop

- Native Tauri/React Windows shell around the outbound-only Python executor.
- Pairing, capability declaration, pause/resume/stop/revoke, status, bounded
  rotating logs, refresh, and visible task activity.
- `local_file_read` and atomic `local_file_write` inside approved roots;
  read-only tree, text, filename, and Git summary inspection are bounded.
- `local_terminal` with executable allowlist, no shell operators, timeout,
  bounded output, credential aliases, and output redaction.
- `local_browser` with HTTP(S) validation and domain allowlist; it can open a
  URL or inspect bounded text without sharing browser cookies.
- Windows-account DPAPI protection for the desktop bearer and local secrets.
- Hosted Automatic/Grok/Sarvam profiles plus private Sarvam/Grok/custom
  OpenAI-compatible direct-chat profiles. Private model keys remain local.
- Unsigned NSIS package, installer shortcut, auto-start option, and a working
  browser-based sign-in/pairing flow.
- Latest source includes strict permission reload during executor polling;
  the installed app must be rebuilt to receive source changes.

### Verification already green

- 159 Python tests, frontend production/type checks, native Rust tests, and
  Windows packaging checks.
- Live Grok/Tavily/research/task/approval/lease safety smokes.
- Disposable local file read/write, Python codebase test, and browser-opening
  workflow passed with the paired executor.
- Expired uncertain terminal/browser/write leases fail closed and are audited
  instead of being replayed.

## 5. What remains now: active beta gates

These are the only immediate release tracks. Do them before calling the beta
stable.

### P0-A — Refresh the installed Desktop build

**Status: completed on 2026-08-29.** The installed Windows package was rebuilt
from a clean native bundle, reinstalled without removing the paired state, and
its executor status confirms the expected local permissions. Shortcut,
interactive sign-in, and reconnect behavior remain part of the physical drill.

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
dropped stream, sign-out, and account isolation. Keep the Memento rollback
configuration until this run is green. The legacy route is a rollback only and
is not part of the target product.

### P0-D — Edge and provider checks

- Review distributed Cloudflare/edge rate limits using an authenticated browser
  session; the backend Redis limiter is already verified.
- Run one low-cost Sarvam chat/reasoning smoke if the operator key is enabled.
- Record provider failures as actionable user-facing errors without exposing
  upstream secrets or raw error bodies.

## 6. Local agent capability work (next implementation phase)

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
- Still needed: encoding/type classification and richer changed-file hashes.

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
- Still needed: selected DOM inspection, screenshots, bounded downloads, and
  citation/proof presentation.
- Capture screenshots.
- Download bounded files.
- Return citations and proof to the hosted task.

Browser sessions and cookies must remain local. This is separate from hosted
public URL fetching through Tavily/HTTP.

### L5 — Local credential adapters

The encrypted credential vault exists, but an alias currently becomes useful
only when an approved terminal process requests it. Add explicit local adapters
for tools such as Tavily, GitHub, Telegram, Gmail, Calendar, and Drive only
after each adapter has its own scope, approval, redaction, and test contract.
No adapter may upload the user's secret to the VM.

### L6 — Artifact and result workspace

- **Implemented:** Desktop Activity task selection now fetches the durable task
  record and displays the result, steps, recent activity/progress, and artifact
  names. The existing Web artifact endpoint remains the source of truth.
- Still needed: inline file previews, diffs, hashes/downloads, and retention
  state in both Web and Desktop.
- The task result is now read from the durable `result_summary` record, so
  “completed” is distinct from “result produced” when no answer was generated.
- Provide safe retry/reopen/reconcile controls for failed or dead-lettered
  local steps.

## 7. Local agent and task architecture

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
   inspect → plan → edit → run → verify → report.
3. Add durable local intent/queue state for disconnected desktops; never replay
   uncertain side effects automatically.
4. Add workspace locking/concurrency rules so two tasks cannot edit the same
   files unexpectedly.
5. Keep the hosted planner as the default. Add a local fallback planner only
   after the skill protocol, task reconciliation, and local memory policy are
   stable; otherwise two independent agent brains will diverge.

## 8. Memory plan

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

Choose one policy:

- **Recommended for beta:** hosted memory only; local execution keeps only
  bounded logs and task artifacts.
- **Later offline enhancement:** add an encrypted local cache for local
  conversation summaries, workspace facts, pending intents, and explicit
  remember/recall/forget controls. Sync must be opt-in and conflict-aware.

Also require server-enforced workspace/account filters before claiming secure
cross-project isolation; current metadata filters are not sufficient for that
claim.

## 9. Tools, skills, and plugins

### Available hosted tools

- `current_time`
- `calculate`
- `research.web_search`
- `research.fetch_url`
- `desktop.request_action` through an approved durable task

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

## 10. Production hardening (after the beta)

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

## 11. Execution order

1. Complete the physical restart/reconnect drill with the paired Windows PC.
2. Complete interactive authenticated Web shadowing and edge-limit checks.
3. Implement Workspace Inspection and Reviewable Editing. **Workspace
   inspection and L2 editing are now complete; proceed to test/build recipes.**
4. Expand Test/Build execution with named recipes and reviewable artifacts.
   **Named recipes, bounded artifact metadata, changed-file collection, and
   Desktop Activity rendering are now complete.**
5. Implement Controlled Browser inspection, keeping browser state local.
6. Add local credential adapters one at a time, beginning with the highest
   value personal workflow.
7. Improve task decomposition, local intent reconciliation, workspace locks,
   and result/diff UX.
8. Decide whether encrypted offline local memory is needed; do not create a
   second memory system by default.
9. Run the broader agent evaluation corpus: chat, research, codebase changes,
   approvals, failures, attachments, reconnects, cancellations, and duplicate
   prevention.
10. Reopen production hardening gates and promote only after all required
    evidence is recorded green.

## 12. Readiness and definition of done

Current honest estimate:

- Focused hosted + desktop beta: **~84%**.
- Desktop executor foundation: **~91%**; physical restart/reconnect,
  capability depth, signing, and update trust remain.
- Full local-agent experience: **not complete** until Sections 6–8 are
  implemented and tested.
- Full production promotion: **not complete** while Section 10 gates are
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
