# Smara Local Agent Rebuild Plan

> A capability-first recovery plan for Smara Desktop. This replaces neither
> the hosted-task roadmap nor the production gates. It makes the Desktop a
> capable, local-first agent that can work without a hosted Smara connection.

## Why this plan exists

The current Desktop executor has five safe capabilities — files, document
creation/editing, terminal, browser inspection, and local integrations — but
the chat experience is not dependable enough to expose them:

- Local planning depends too heavily on whether a selected model supports
  OpenAI-style tool calls.
- Safe deterministic tasks such as time and calculation can fall through to a
  general chat reply.
- Local research, coding, and document work are not presented as clear
  first-class actions from one local agent surface.
- The current tests prove individual executor functions, but do not yet prove
  the complete user journey from natural-language request to proof/result.

The objective is not arbitrary machine control. It is a dependable local agent
that plans, asks once when needed, executes in the approved workspace, shows
live proof, and works while Hosted Smara is unavailable.

## Reference review: external local-agent architecture

An externally installed, permissively licensed local-agent implementation was
reviewed as an architectural reference. Smara will not copy credentials, user
data, or unreviewed code. The useful patterns to adapt natively are:

| Reference pattern | Smara implementation choice |
| --- | --- |
| Large tool catalogue | Small typed, capability-scoped catalogue with one validator per operation |
| Toolsets and discovery | Desktop Capability Broker lists only currently enabled skills and connectors |
| Evaluations | Versioned offline capability corpus plus disposable end-to-end workspaces |
| Sessions and memory | Local transcript/profile store, separate from Syntarus project memory |
| Skills and cron | Declarative skills/routines; no arbitrary downloaded scripts |
| Browser / terminal supervision | Visible runs, bounded outputs, cancellation, workspace locks, and approval policy |

Do **not** copy unrestricted shell assumptions, arbitrary plugin code, cloud
execution backends, or local credential files into Smara.

## Non-negotiable local contract

1. Desktop remains useful with Hosted Smara disconnected.
2. The model never directly performs an action: it creates a typed request;
   the local broker validates and executes it.
3. Two modes only: **Ask before acting** and **Approve for me**. Both are
   controlled on Desktop; high-risk actions still require an explicit prompt.
4. Every completed action returns a visible result, changed-file proof,
   artifact, source list, command output, or a clear failure explanation.
5. Secrets, browser cookies, raw local files, and OAuth refresh tokens never
   leave the PC.

## Capability evaluation suite (build before feature expansion)

Add `tests/evals/local_agent/` with stable fixtures and a disposable workspace.
Every case records: request, required capability, expected approval mode,
expected proof, allowed side effects, and forbidden side effects.

| Group | Initial cases | Required outcome |
| --- | ---: | --- |
| Direct utilities | 12 | Clock/date, calculator, path status, model status; never answer "I cannot" when the local tool exists |
| File/workspace reads | 16 | Tree, search, git summary, bounded reads, no path escape |
| Documents | 16 | Create/edit/read DOCX, XLSX, PPTX, PDF and verify generated artifact/hash |
| Coding | 20 | Inspect, patch, run allowed test, diff, failed-test repair stop, workspace lock |
| Terminal | 14 | Typed argv/recipes, output streaming, cancellation, no shell injection |
| Browser/research | 18 | Search, fetch, inspect, citations, domain policy, download proof, offline error contract |
| Connectors | 12 | Read-only GitHub/Google fixtures, alias-only secrets, revoke/expiry behavior |
| Recovery/security | 20 | Restart, reconnect, duplicate prevention, malicious paths, malformed tool plans, secret redaction |

Promotion requires all deterministic cases to pass plus a disposable native
Desktop run for each mutating capability.

## Delivery sequence

### L0 — Correctness and visibility (first)

- Expand deterministic intent routing to tolerate normal conversational
  phrasing for clock, date, calculation, status, and help.
- Add a local Capability Broker that can invoke deterministic local tools
  before an LLM is consulted.
- Show the selected mode, enabled tools, blocked reason, approval request,
  running command/tool, and final proof directly in Desktop chat/activity.
- Add the initial capability corpus and make it a release requirement.

**Done when:** simple requests never depend on the hosted service or model tool
calling; each result is observable in the Desktop UI.

### L1 — Reliable local planning

- Keep native function calling for capable models.
- Add a strict JSON-plan fallback for compatible models that lack tool-call
  support; parse it as data and validate it through the same broker.
- If neither route is available, explain exactly which local model capability
  is missing instead of pretending work was completed.
- Support inspect → plan → act → verify loops with fixed iteration/time/output
  budgets and cancellation checkpoints.

**Done when:** supported private providers reliably create Desktop-owned tasks
for file, terminal, browser, document, and connector requests.

### L2 — Local research engine

- Provide a Desktop-owned research workflow: formulate query variants, search
  a configured local provider, fetch approved pages, extract bounded evidence,
  deduplicate sources, and write a cited answer/artifact.
- Use one configured primary provider and one independent fallback only after a
  measured evaluation; do not silently fabricate citations when offline.
- Make source count, fetch failures, date, and citation links visible.

**Done when:** a local deep-research request produces a useful cited report or
a precise provider/configuration failure without Hosted Smara.

### L3 — Workspace and document agent

- Promote the existing executor functions into explicit task recipes for code,
  CSV/XLSX, DOCX, PPTX, PDF, image metadata, and archive inspection.
- Add workspace discovery, project instruction loading, Git diff/test summary,
  patch preview, and artifact preview/download.
- Preserve the current worktree/copy isolation, locks, undo, and proof rules.

**Done when:** a disposable repository and a mixed-document folder can be
inspected, changed, verified, and reviewed entirely from Desktop.

### L4 — Browser and local connectors

- Controlled browser sessions with visible page state, text/DOM extraction,
  download review, and explicit handoff for passwords/OTP/CAPTCHA.
- Local OAuth/device flows for GitHub and Google; then bounded read operations
  and previewed, explicit write operations.
- Outbound-only MCP adapter with manifest review, tool schemas, scopes, rate
  limits, and no public local port.

**Done when:** real user accounts can be connected/revoked locally and every
external action is attributable, bounded, and reviewable.

### L5 — Skills, routines, and dependable autonomy

- Teach proven local workflows into signed declarative skills.
- Local routines with pause, run history, network-aware retry, budget, and
  idempotency protection.
- Optional user-owned always-on node only after the physical recovery suite is
  green.

## Explicit exclusions until reviewed

- Arbitrary downloaded plugins or scripts.
- Unbounded shell commands, credential export, hidden browser automation, or
  remote execution on the Smara VM.
- Financial, publishing, deployment, deletion, or external messaging without
  an explicit approval point.

## First execution slice

1. ✅ Shipped L0's conversational deterministic router and regression tests.
2. 🟡 The Desktop-native utility tests cover clock, calculation, status, and
   strict arithmetic input rejection. The larger disposable-workspace corpus
   remains next.
3. ✅ Built the first Desktop Capability Broker slice: local clock,
   calculator, status/capability discovery, and typed task emission are
   independent of a hosted connection. Local chat no longer requires a model
   just to use those built-ins.
4. ✅ Added a strict JSON-plan fallback for private providers that do not
   implement OpenAI function calls. Every JSON plan goes through the same
   capability and payload validation as native function calls.
5. 🟡 Run the native package against the broader corpus before adding more
   mutating workflows.
