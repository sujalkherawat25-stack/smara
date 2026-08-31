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

---

## A1. Local Workspace Agent v2

### Implementation status (2026-08-31)

- **A1.1 shipped in the control plane:** local steps now carry the versioned
  `smara.workspace.v1` job envelope. The desktop validates workspace-relative
  scope, allowed capabilities, approval policy, budgets, idempotency, and
  credential-alias-only inputs before dispatch. Results include bounded job
  metadata for the run console, while stage outputs, artifacts, hashes, and
  test records use the `smara.workspace.stage.v1` contract.
- Hosted-created desktop actions and workflows receive a deterministic,
  secret-free envelope automatically. Custom envelopes are revalidated at the
  tool boundary and again by the desktop before any local operation.
- **Still required for A1:** isolated Git worktrees/copies, adaptive repair
  loops, richer Web/Desktop run-console rendering, and physical restart,
  conflict, and adversarial acceptance drills.

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

2. **Isolated coding workspaces**
   - Use a Git worktree or copied workspace for code-change jobs when the
     repository supports it.
   - Capture a base revision, changed-file hashes, diff summary, and test
     output.
   - Require explicit user confirmation before merging, pushing, deploying,
     deleting, or overwriting work outside the task workspace.

3. **Adaptive but bounded replanning**
   - Permit a limited number of repair loops after a failed check.
   - Every repair loop must explain the failure, proposed change, and new
     verification command.
   - Stop and ask the user when the budget, permission, confidence threshold,
     or original scope is exceeded.

4. **Run console and review surface**
   - Show the current plan, stage state, live command progress, diff, artifacts,
     test results, retry/cancel controls, and final acceptance status in both
     Web and Desktop.
   - Support download/open/copy for bounded artifacts and a clear local path
     reference without exposing file contents by default.

5. **Recovery rules**
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
