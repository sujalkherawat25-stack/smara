# Smara Autonomy Execution Plan

> **Purpose:** the forward roadmap for turning Smara into a deeply capable,
> local-first work agent for India. This document contains **future work only**.
> It intentionally does not repeat completed release, reliability, UI, or
> production-gate work.

## 1. Product direction

Smara should not copy a shared cloud-computer model. Its advantage is a safer
split:

- **Hosted Smara** coordinates, researches, plans, schedules, monitors, and
  maintains durable task state.
- **Smara Desktop / Node** owns private files, browser sessions, credentials,
  and execution on the user's behalf.
- Every meaningful action remains explainable through an approval, task graph,
  artifact, diff, test result, and durable audit record.

The objective is not unrestricted autonomy. It is **high-trust autonomy**:
Smara should complete more real work with less supervision while making its
scope, evidence, cost, and side effects easy to inspect.

## 2. Scope boundaries

### In scope

- Deep local workspace and coding workflows.
- Local-first OAuth/MCP adapters and personal productivity tools.
- Versioned skills, schedules, event triggers, and visible agent teams.
- India-first language, voice, document, and research workflows.
- Optional always-on, user-owned execution node.
- Evaluation, safety, recovery, and cost controls required by each feature.

### Out of scope

- Moving private browser sessions, local files, or user credentials to hosted
  Smara.
- Unrestricted shell access, arbitrary plugin code, hidden side effects, or
  automatic financial/legal/production changes.
- Replacing the existing beta hardening or physical-device release gates. They
  remain tracked in `SMARA_IMPLEMENTATION_PLAN.md` and
  `PRODUCTION_GATE_STATUS.md`.

## 3. Planning principles

1. **Local capability is not blanket permission.** Every action requires both
   an eligible local capability and the relevant approval policy.
2. **Plan before mutation.** Read, inspect, and explain before editing,
   sending, publishing, deleting, or spending.
3. **Proof before completion.** A task is complete only with bounded output,
   changed-file proof/diff where relevant, verification output, and a useful
   final result.
4. **One durable source of truth.** Hosted task state coordinates work; Desktop
   journals local execution only for recovery and reconciliation.
5. **Parallelize reads, serialize writes.** Research/review can branch; writes
   to a workspace, browser account, or external system require a single
   accountable executor and conflict checks.
6. **India-native by default.** Language, document, latency, affordability,
   privacy, and low-connectivity constraints are first-class product inputs.

## 4. Priority sequence

| Priority | Initiative | Outcome | Dependencies |
| --- | --- | --- | --- |
| A1 | Local Workspace Agent v2 | Smara completes bounded coding/workspace jobs with reviewable proof | Existing local executor and task graph |
| A2 | Local Connector Runtime | Useful personal work through local OAuth/MCP adapters without sending secrets to the VM | A1 policy/artifact model |
| A3 | Skills and Routines | Users can save, test, version, schedule, and pause reliable workflows | A1 and A2 |
| A4 | Visible Agent Teams | Researcher, builder, reviewer, and operator cooperate through one auditable graph | A1 artifacts and skill contracts |
| A5 | India-first Intelligence | Indic language, document, and source-native workflows become best-in-class | Sarvam capability matrix and A2 |
| A6 | Optional Smara Node | Approved local work may continue on a user-owned always-on machine | A1-A3 recovery protocol |

Build each initiative fully through its acceptance tests before opening the
next one. A feature that needs a new external authority, provider agreement, or
regulated integration must stop at an explicit design decision.

### Local-first implementation slice (2026-09-01)

The first local-first slice is now implemented in the Desktop build:

- Runtime mode is explicit and persisted as `local` or `cloud`. New installs
  default to `local`; existing paired installs retain the legacy cloud mode
  until the owner changes it in Settings.
- Local mode does not require a hosted sign-in, hosted task history, or a
  running executor. Private model profiles continue to stream directly from
  the PC, and the UI reports the hosted bridge as optional rather than failed.
- Local task/session records have a bounded, atomic, private store beside the
  Desktop state file. Lists and details omit payloads; approval transitions and
  cancellation are recorded as local events. The shared Desktop task contract
  marks these records as Desktop-owned, so approval is never silently routed
  to Smara Web.
- Cloud mode remains available for durable hosted planning, research,
  scheduling, and paired Desktop leases. Switching modes is an explicit user
  action and does not copy local credentials or private task payloads.

The Desktop-owned A1 runner slice is now complete in software:

- Private-model chat may request a typed local action instead of pretending it
  executed one. The task is created in the private Desktop store and shown in
  Activity.
- `Ask before acting` keeps the task at `waiting_approval`; `Approve for me`
  queues only actions inside the declared local capabilities. Desktop remains
  the approval authority in both cases.
- A single local runner claims queued work and dispatches it through the same
  hardened file, document, terminal, browser, and local-connector skills used
  by the paired executor.
- Progress, steps, artifacts, bounded result proof, cancellation requests, and
  errors are journaled locally. A restart moves an uncertain running mutation
  to `review_required`; it is never replayed automatically.
- Fresh unpaired installations now persist a permission-only runtime state, so
  local work does not depend on a hosted account or pairing record. Connector
  capability changes synchronize immediately after a credential is saved,
  removed, or revoked.
- **2026-09-04 reliability slice:** the Desktop now advertises and executes
  bounded in-process `local_calculate` and `local_python` helpers plus the
  read-only `local_graph`/CPG inspector without a hosted round trip. In Auto
  mode these pure helpers and read-only inspections can run immediately;
  terminal commands and file mutations remain Desktop-confirmed. The bundled
  Windows executor emits UTF-8 JSON, so emoji/Indic task results cannot crash
  the result loader on a legacy cp1252 console.

Automated acceptance covers ask/approve, execution, proof, cancellation,
interrupted-run recovery, no replay, unpaired operation, path/shell/domain
boundaries, and connector secret redaction. The remaining Windows/network
restart drill is an owner-operated release check, not missing implementation.

---

## A1. Local Workspace Agent v2

### Implementation status (2026-09-01)

- **A1.1 shipped in the control plane:** local steps now carry the versioned
  `smara.workspace.v1` job envelope. The desktop validates workspace-relative
  scope, allowed capabilities, approval policy, budgets, idempotency, and
  credential-alias-only inputs before dispatch. Results include bounded job
  metadata for the run console, while stage outputs, artifacts, hashes, and
  test records use the `smara.workspace.stage.v1` contract.
- Hosted-created desktop actions and workflows receive a deterministic,
  secret-free envelope automatically. Custom envelopes are revalidated at the
  tool boundary and again by the desktop before any local operation.
- **A1.2–A1.4 implemented in the control plane:** the desktop can prepare an
  idempotent bounded copy or detached Git worktree, records a metadata-only
  workspace snapshot/base revision, and returns the versioned stage-proof
  contract. Workspace jobs enforce their own repair budget (instead of
  inheriting an unbounded generic retry limit), while hosted APIs redact raw
  executor payloads. Web and Desktop run consoles now show scope, isolation,
  repair budget, stage proof, artifacts, and acceptance-check status.
- **A1 private Desktop runner shipped:** local tasks have legal state
  transitions, a cross-process mutation lock, one active runner, progress and
  proof journals, cooperative cancellation, and explicit recovery review.
  Private model tool requests create real Desktop tasks; results return to the
  Desktop chat instead of being hidden in hosted Work.
- **A1 result-path hardening verified:** local task lists/details tolerate
  Unicode output, missing paths explain the `find_files`/`list_tree` recovery
  path, unsupported screenshot requests explain the supported browser
  alternatives, and unknown hosted task names fail visibly instead of being
  recorded as false successes.
- **A1 remaining release gate:** run the physical Windows restart/network-loss
  drill and a disposable multi-stage repository drill on the installed package.
  These are owner-device validation activities, not unfinished code paths.

### Goal

Enable a trusted end-to-end workspace job: inspect → plan → edit → run → verify
→ report. It must be able to recover safely, show exactly what changed, and
never silently repeat a possibly completed mutation.

### Deliverables

1. **Workspace run contract**
   - Introduce a versioned job specification with workspace root, objective,
     acceptance checks, allowed capabilities, idempotency key, time/cost
     budget, and owner approval policy.
   - Keep all secret values out of task payloads; reference local credential
     aliases only.
   - Require structured stage output: summary, files inspected, files changed,
     commands run, tests, artifacts, warnings, and next action.

2. **Isolated coding workspaces** ✅
   - Use a Git worktree or copied workspace for code-change jobs when the
     repository supports it.
   - Capture a base revision, changed-file hashes, diff summary, and test
     output.
   - Require explicit user confirmation before merging, pushing, deploying,
     deleting, or overwriting work outside the task workspace.

3. **Adaptive but bounded replanning** ✅
   - Permit a limited number of repair loops after a failed check.
   - Every repair loop must explain the failure, proposed change, and new
     verification command.
   - Stop and ask the user when the budget, permission, confidence threshold,
     or original scope is exceeded.

4. **Run console and review surface** ✅
   - Show the current plan, stage state, live command progress, diff, artifacts,
     test results, retry/cancel controls, and final acceptance status in both
     Web and Desktop.
   - Support download/open/copy for bounded artifacts and a clear local path
     reference without exposing file contents by default.

5. **Recovery rules** ✅ for Desktop-owned local tasks
   - Persist checkpoints before every mutation.
   - Reconcile uncertain journal entries with hosted task state before resuming.
   - Require user review instead of replaying an ambiguous write, command, or
     browser action.

### Acceptance tests

- A disposable repository can be inspected, changed, tested, and reported with
  a visible diff and passing test artifact.
- A failed test triggers at most the configured repair budget and then stops
  with a useful explanation.
- Network loss, application restart, and Windows restart produce exactly one
  side effect per stage.
- Workspace locks prevent competing writes; independent read-only tasks may run
  concurrently.
- A malicious payload cannot escape the workspace, add shell operators, inject
  credentials, or alter the job graph.

---

## A2. Local Connector Runtime

### Implementation status (2026-08-31)

- **A2 foundation shipped:** installed local connectors now publish an
  explicit, secret-free contract alongside every approved result: provider,
  operation, local authentication mode, read-only risk tier, declared scope,
  result limit, and per-run request bound. Tavily search and GitHub repository
  discovery use that contract; credential aliases remain resolved only in the
  Windows vault and are never included in hosted results.
- **A2 lifecycle shipped:** Desktop Settings now shows each connector's local
  readiness, declared read-only scope, request bound, and a connector-specific
  disconnect control. The executor keeps a bounded, proof-only local audit of
  completed, cancelled, failed, and revoked connector runs. It deliberately
  excludes credentials, query text, response text, source URLs, and repository
  names. Every connector operation remains behind the normal hosted approval
  gate before the desktop can contact a provider.
- **A2 browser handoff shipped:** approved GitHub and Google sign-in URLs can
  open in a dedicated per-provider Chromium profile on the PC. The handoff
  requires the normal local-browser capability and an explicit domain
  allowlist; its result confirms only that a local takeover window opened.
  Cookie jars, passkeys, OTPs, authorization codes, and even the profile path
  remain outside Smara's hosted control plane.
- **A2 remaining work:** local OAuth/device authorization for Google Workspace
  and GitHub, approval-gated writes, and the outbound-only MCP adapter. These
  require owner-controlled provider consent and will ship as separately
  testable connector slices.

### Goal

Let Smara use the user's authorized services while keeping OAuth tokens and
browser sessions on the user's device.

### Delivery order

1. **Google Workspace** — Drive search/read, Gmail read/draft, Calendar
   read/draft.
2. **GitHub** — repository discovery, issues, pull requests, Actions/logs,
   then explicit write operations.
3. **Local browser handoff** — isolated per-connector browser profiles with
   a visible takeover point for passwords, passkeys, OTPs, and CAPTCHAs.
4. **Desktop MCP adapter** — outbound-only local adapter for declared MCP
   tools. It must not require a public tunnel or expose a local port.
5. **Additional consented adapters** — Slack, Notion, Linear, and approved
   India-specific services only after their security and product review.

### Required connector contract

- Declared reads, writes, scopes, risk tier, rate limits, result limits, and
  approval requirements.
- OAuth/device authorization occurs locally. Hosted Smara receives capability
  metadata and redacted results, never refresh tokens.
- A connector can be disconnected and all local credentials removed in one
  action.
- Each action creates an audit event with target, effect summary, and result.
- Writes are previewed and approval-gated by default.

### Acceptance tests

- A user can connect and revoke a test Google/GitHub account without any token
  appearing in hosted API, events, logs, artifacts, or memory.
- Read actions stay within declared scopes and return bounded/redacted output.
- Write actions show an exact preview and cannot execute after expiry, denial,
  revocation, or reconnect ambiguity.
- A malformed or malicious MCP manifest cannot add a tool, expand scopes, or
  execute arbitrary local code.

---

## A3. Skills and Routines

### A3.1/A3.2 implementation slice (2026-09-01)

The reusable-skill contract foundation is now implemented in
`src/smara/skill_protocol.py` and covered by focused policy tests:

- `smara.skill.v1` manifests are strict, versioned, and declarative. They
  describe typed inputs/outputs, bounded DAG stages, local capabilities,
  connectors, risk, limits, tests, owner, and rollback instructions.
- Manifest validation rejects unknown fields, credential-shaped values,
  executable/script payloads, unknown capabilities, missing permissions,
  dependency cycles, and oversized arguments before a skill can be registered.
- A thread-safe registry enforces draft → tested → published → deprecated.
  Publishing requires a passed disposable test and explicit approver identity.
- Every registered version has a deterministic fingerprint. A changed
  manifest, including a nested mutation, fails closed instead of reusing an
  earlier approval. Registry records are detached copies so callers cannot
  mutate the approved state accidentally.

This is the policy/runtime foundation. The persistence and teach-to-skill
translation slice built on it is now shipped as well:

- `PersistentSkillRegistry` stores private skill manifests and lifecycle
  metadata with an atomic replace, bounded entry count, schema validation, and
  fail-closed tamper detection. Reopening the store preserves published
  approvals; corrupt or stale files are rejected without being replaced.
- `draft_skill_from_workflow(...)` converts only the existing validated,
  sequential local workflow contract into a draft skill. It infers explicit
  `$input.name` references, derives capability permissions, marks mutating
  stages as `risk=confirm`, and carries no executable code or credentials.
- Restart, tamper, workflow-secret, mutation-risk, and persistence tests are
  included in `tests/test_skill_protocol.py`.
- The hosted control plane now persists the same validated manifests per
  account in the durable task store (`skills` table/migration `021_skills.sql`)
  and exposes list, create, teach, test, publish, and deprecate endpoints under
  `/v1/skills/*`. Publishing records the authenticated account as approver;
  account filters are applied to every read and mutation.

Scheduled routines and authenticated event triggers remain the next A3 slice.
The private Desktop registry remains separate from the hosted source of truth;
published hosted skills are persisted per account in the control-plane store.

### Goal

Turn reliable, reviewed workflows into reusable capabilities without allowing
unreviewed executable code.

### Deliverables

1. **Versioned skill package**
   - Manifest: name, version, inputs, outputs, permissions, risk tier,
     provider/connector requirements, tests, owner, and rollback instructions.
   - Skills are declarative task graphs and typed tool calls, not arbitrary
     Python or shell scripts supplied by a model.
   - Support draft → test → publish → deprecate lifecycle.

2. **Teach-to-skill**
   - Record an approved, bounded workflow as structured events.
   - Convert it to a draft declarative skill with inferred inputs and explicit
     approval points.
   - Require the user to review and test it against disposable data before it
     can be scheduled.

3. **Routines and triggers**
   - Scheduled routines with time zone, budget, no-data policy, retry policy,
     channel notification, and pause control.
   - Event triggers only from authenticated connector events. No trigger may
     directly authorize a destructive action.
   - Run history includes inputs, source freshness, approvals, result, and
     failure reason.

### Acceptance tests

- A skill cannot run after its connector is revoked or its manifest changes
  without reapproval.
- A routine does not duplicate work across scheduler restart/reconnect.
- A stale or unavailable source causes a visible partial/failure result rather
  than a fabricated success.
- A user can pause, resume, test, and delete a routine with durable audit
  history.

---

## A4. Visible Agent Teams

### Goal

Use specialist agents for hard work while retaining one accountable task owner
and a human-readable execution record.

### Initial roles

| Role | May do | May not do |
| --- | --- | --- |
| Researcher | Search, fetch, compare sources, produce evidence | Mutate a workspace or external account |
| Builder | Make approved local workspace changes | Merge/push/deploy without a separate approval |
| Reviewer | Inspect diffs, tests, evidence, policy compliance | Modify the Builder's workspace directly |
| Operator | Coordinate plan, budgets, approvals, and final synthesis | Hide subtask output or bypass risk policy |

### Rules

- Parallelism is read-only by default; writes are serialized per workspace and
  external target.
- Each subtask has a bounded budget, tool allowlist, timeout, and artifact.
- The coordinator must cite the evidence/subtask that supports every final
  decision.
- User sees the team, task graph, current stage, cost/time, and failure state.

### Acceptance tests

- Parallel research yields a merged, deduplicated citation set.
- Conflicting recommendations require a reviewer decision rather than silent
  majority voting.
- A failed subtask produces a partial but useful final result.
- No sub-agent can access another account, workspace, credential alias, or
  unapproved write capability.

---

## A5. India-first Intelligence

### Goal

Make Smara materially better for Indian users, languages, documents, and daily
workflows—not merely translated.

### Workstreams

1. **Language and voice contract**
   - Detect English, Hindi, native-script Indic languages, transliteration, and
     code-mixed input.
   - Let the user choose response language, script, and voice output.
   - Render approvals, errors, and privacy explanations in the selected
     language; never downgrade safety language because a translation is hard.
   - Build a measured benchmark across supported Indian languages rather than
     relying on anecdotal quality.

2. **Document workbench**
   - Ingest, classify, OCR, and extract structured information from common
     Indian business and government documents.
   - Offer schema-based extraction with field-level confidence and source page
     references.
   - Add local PII detection/redaction choices before hosted processing.
   - Keep original documents, extracted text, and derived artifacts under clear
     retention and deletion controls.

3. **India research packs**
   - Build a curated, versioned authority registry for RBI, SEBI, MCA, GST,
     MeitY, DPIIT, IndiaAI, state portals, procurement sources, and other
     approved public sources.
   - Add freshness, jurisdiction, primary-source, and contradiction scoring.
   - Research answers must distinguish verified facts, interpretation, and
     unknowns.

4. **Research provider resilience**
   - Keep the existing primary provider and add one independently operated
     fallback behind the same bounded adapter contract.
   - Evaluate result quality, latency, coverage, cost, duplicate rate, and
     citation usefulness before turning on automatic fallback.
   - Do not add a provider key until the evaluation harness and fail-closed
     error handling are ready.

5. **Low-connectivity and cost control**
   - Queue local work and resume safely after intermittent connectivity.
   - Provide low-bandwidth artifact views, resumable uploads, and conservative
     provider routing for simple requests.
   - Show an estimate before expensive deep research, large OCR, or multi-agent
     work begins.

### Acceptance tests

- Indic prompts, code-mixed prompts, approvals, and error states render
  correctly in the chosen language/script.
- Document extraction returns citations/page references and rejects unsupported
  files safely.
- Research source packs favor primary Indian sources when relevant and disclose
  source freshness.
- Provider fallback is used only after the primary fails within its bounded
  policy; citations remain traceable to the provider/source that supplied them.

---

## A6. Optional Smara Node

### Goal

Allow approved local automation to continue after a primary laptop closes,
without turning Smara into a shared cloud computer.

### Design

- A separately enrolled, user-owned Windows/Linux machine or office mini-PC.
- Outbound-only connection and the same pairing, local credential vault,
  workspace scopes, approval policy, journal, lock, and reconciliation
  protocol as Smara Desktop.
- Explicit node health, availability, battery/power state, version, update
  channel, and owner controls.
- Hosted work may continue while the node is offline; private local stages wait
  safely and notify the user instead of falling back to the VM.

### Acceptance tests

- Node loss, restart, token revocation, and reconnect never duplicate a side
  effect.
- A node cannot access a workspace, connector, or credential not assigned to
  it.
- A user can pause or revoke a node and confirm it stops receiving work.

---

## 5. Cross-cutting platform work

### Evaluation and release gates

- Build a versioned autonomy evaluation suite: research, coding, documents,
  connectors, schedules, cancellation, reconnect, account isolation, and
  adversarial prompt/tool payloads.
- Maintain both deterministic offline tests and disposable live-provider tests.
- Require a real owner-device drill for every new mutation capability.
- Track success rate, verified completion rate, duplicate-side-effect rate,
  human-intervention rate, recovery time, cost per successful task, and unsafe
  action prevention rate.

### Safety and governance

- Permission policy per workspace, connector, domain, executable, and skill.
- Immutable audit events with secret redaction and bounded retention.
- Explicit data export/deletion and local credential revocation.
- Human approval for money movement, legal/medical advice delivery, production
  deployment, publishing, deletion, and external communication by default.
- Obtain specialist legal/privacy review before regulated Indian integrations or
  sensitive-document product claims.

### UX requirements

- One coherent task console across Web, Desktop, Telegram, and future mobile.
- Plain-language status: what Smara is doing, why it needs approval, what it
  changed, what failed, and what the user should do next.
- Never show a task as merely “completed” when the useful answer, diff, output,
  or artifact can be displayed.
- Prefer progressive disclosure: chat stays simple; task detail opens only when
  work becomes long-running, risky, or reviewable.

## 6. Success measures

| Measure | Initial target |
| --- | --- |
| Verified completion on bounded workspace tasks | ≥85% across the curated evaluation suite |
| Duplicate side effects after restart/reconnect | 0 tolerated |
| Mutation tasks with visible diff/proof/test result | 100% |
| Connector writes with explicit preview/approval | 100% |
| Research factual claims with traceable citations | 100% for cited-research mode |
| Indic language/document benchmark coverage | Published before broad India-first launch |
| Secret exposure in hosted logs/artifacts/memory | 0 tolerated |

## 7. First implementation milestone

Begin with **A1: Local Workspace Agent v2**. Ship it in small, reversible
packages:

1. Versioned workspace job schema and artifact contract.
2. Git worktree/copy isolation plus diff/hash/test artifacts.
3. Bounded repair/replan loop and cancellation checkpoints.
4. Web/Desktop run-console improvements.
5. Restart, conflict, adversarial-payload, and real owner-device tests.

Do not begin broad connector, multi-agent, or always-on-node work before this
milestone has an end-to-end disposable repository test and a physical Desktop
reconnect drill.

## Verification snapshot — 2026-09-04

- The synchronized source bundle was rebuilt and checked: Python test suite
  **320 passed**, Rust desktop tests **14 passed**, frontend production build
  passed, and Python bytecode compilation passed.
- The packaged executor passed a local safe-expression smoke test and reports
  unsafe expressions as failed tasks. Local graph inspection now requires an
  explicitly approved workspace; unsupported hosted task kinds fail closed;
  local notifications and memory-only task completion are persisted.
- The corrected release artifacts are present at the Tauri release binary and
  NSIS installer paths under `apps/desktop/src-tauri/target/release/`.
- Installation is still an owner action: the AppData executable is an older
  12.3 MB copy, no Smara Desktop process is currently running, and the latest
  NSIS installer has not yet been run on this machine.
- Production gates remain closed until the owner-device offline/restart/
  reconnect/revoke drill and authenticated hosted account-isolation checks pass.
