# Smara: H3–H7 local execution plan

Updated: 2026-09-08. Status: H3–H7 foundations implemented; Windows autonomous research/local execution integration and acceptance remain active.

## Current plan — Windows readiness, 2026-09-08

This section supersedes the original delivery order and handoff below. Continue from the implemented foundations; do not rebuild H3.0. The original specification remains below as historical design context, not a list of wholly unimplemented features.

**Active scope:** one Windows local agent that can research a question, use a stateful browser, create or change local artifacts, verify results, and continue through interruption within a shared budget. “Ready” means demonstrated user-request-to-validated-result behavior through the actual CLI/application adapter, not only standalone component tests.

**Deferred by user decision:** Linux/WSL packaging, disposable-VM desktop D01–D20, OSWorld, and general OS coordinate control. These are not blockers for the Windows research/terminal/file/browser release. Keep them explicitly unavailable/deferred; do not substitute host desktop automation for VM evidence. Official external benchmarks, mobile feature expansion, and automatic skill/delegation promotion are also outside this immediate readiness gate. Existing delegation remains opt-in.

### What is implemented now

Reviewed HEAD: `8f47be3`, following `a4837c3` (foundations) and `5660f3d` (hardening). Evidence sources: `release/H3_H7_GATE_REPORT.md`, `release/capabilities.json`, `HARNESS_STATUS.md`, current source and tests. The prior report records 497 passed/1 skipped, repeated real Chromium tests, Rust cargo check, and a Windows wheel install/task smoke. Those are prior recorded results; fresh verification for this review is recorded separately below.

| Milestone | Current implementation | Disposition |
| --- | --- | --- |
| H3 durable execution/context | Incremental calls, usage reservation, continuation, token packing, progress guards, R25–R32 and adapter tests | Retain; extend true process-restart and realistic long-task acceptance |
| H4 research | ResearchGraph, EvidenceIndex, persisted research-pass state, provenance tests and scoring helpers | Core exists; adaptive orchestration and claim correctness are not release-ready |
| H5 browser | ManagedBrowser and real local Chromium fixture tests | Backend tested; canonical agent still exposes legacy inspection browser actions |
| H5 desktop | Fail-closed VM contract and coordinate tests | Deferred; no real VM capability claim |
| H6 delegation/learning | Child supervision, root ledger integration and skill quarantine | Retain opt-in; no measured default-promotion justification |
| H7 Windows/application | Canonical app adapter, local transport, Windows package evidence | Retain; test actual installed user journeys and capability discovery |
| H7 Linux | No validated Linux install/task run | Deferred |

The inventory currently declares **69 cases**, including 20 desktop contract-only cases. It does not represent 69 proven end-to-end capabilities or a recovered 172-case suite.

### Findings that drive the next implementation

1. **Incorrect claims can pass research support.** `EvidenceIndex.support` filters out words of length three or less and checks remaining substrings. An offline probe against “The value is 42 kilograms.” accepted both **“99 kilograms”** and **“The value is not 42 kilograms.”** as supported. Numeric-only claims produce no checked terms. Treat this function as an unreliable heuristic until repaired; provenance checks alone cannot establish semantic support.
2. **Graph completion is currently retrieval-based.** `research_tools.py` creates one topic node, uses fixed search angles, and resolves it supported when `fetched_count > 0`. Ready dependencies and contradiction expansion exist as APIs, but this research pass does not use them to conduct an adaptive multi-hop investigation.
3. **CLI research is not wired to that research state.** Autonomous dispatch calls `web_search`/`web_extract` directly. A persisted graph/index in a separate research adapter does not establish canonical CLI research continuity or final answer validation.
4. **Raw provenance is incomplete in the research pass.** It initializes `EvidenceIndex()` without the session ArtifactStore and hashes `result.excerpt` as content. This proves consistency of an excerpt, not recoverability of the original fetched response/PDF. PDF/OCR tests include supplied text and placeholder bytes; real extraction correctness remains a separate gate.
5. **Managed browser is not exposed to the autonomous tool loop.** `autonomous_agent.py` → `agent_tools.browser_action_tool` → `BrowserSidecarEngine` still offers scrape/navigation/screenshot/DOM inspection. Source search found ManagedBrowser only in its module, not a production adapter. Passing B01–B20 directly against it does not mean the agent can fill a form or manage tabs.
6. **Persistent processes are not first-class autonomous tools.** Harness start/poll/write/cancel exist, but the autonomous schema/dispatch does not expose those operations. The current soak closes/reopens SessionEngine within one Python process over a subsecond job; it is not evidence of surviving a killed CLI process or sustained work.
7. **Final output checks are mostly structural.** Harness checks length/pattern/JSON fields; the active finalization path needs claim/evidence and artifact-specific validators. A formatted answer is not a completed research task.

These findings distinguish working components from user-visible capability. Prior passing tests remain useful but do not cover these counterexamples.

### W1 — Research correctness and canonical integration (first)

Files: `evidence_index.py`, `research_eval.py`, `research_graph.py`, `research_tools.py`, `autonomous_agent.py`, `harness.py`, and focused new integration tests.

- First add regressions for the numeric and negation false positives above, plus wrong date/unit/entity, unsupported causal claims, and citations to missing/stale artifacts. Empty meaningful claim tokens must return unknown/unsupported rather than success.
- Separate provenance validity, extraction validity, and claim support. For structured facts use explicit entity/value/unit/date comparisons and valid unit conversions. For prose use supported/refuted/insufficient judgments with exact evidence passages; a model judge may assist but must not be the only acceptance oracle. Never advertise word overlap as semantic verification.
- Store original response bytes, final URL/redirects, extracted document version and stable passage locations in the session ArtifactStore. Keep the relationship from original bytes → extraction → passage → claim. Scope graph/index by session and attach their artifact IDs to continuation checkpoints.
- Expose typed research actions through the canonical broker (proposed: plan questions, search leads, fetch source, inspect evidence, resolve question, validate claims). Every internal fetch/model attempt must consume the shared budget/deadline and be cancellable; batching must not hide resource use.
- Drive ready DAG nodes, resolve prerequisites, distinguish refutation from missing information, and expand contradictions within a node/retrieval cap. A fetched page alone cannot resolve a question. Reuse known sources and stop when acceptance criteria are met or evidence/budget is insufficient.
- Make final report generation consume a claim/evidence map. Persist report, bibliography, graph, evidence index, limitations and validation result. Finalization must reject required unsupported claims; an explicitly partial report should retain unresolved items and a non-completed task status where the output contract requires full resolution.
- Add a real PDF table and scanned-image fixture with independently known values and extraction locations. If an extractor is unavailable, expose that capability as unavailable and preserve the unresolved question; metadata-only test records do not qualify.

Exit gate: at least 12 deterministic end-to-end cases through the actual agent loop and broker: direct fact, two-hop lookup, dependency chain, contradiction, duplicate publication, snippet-only failure, wrong number, negation, units/dates, real PDF table, uncertain OCR, and resume with cited evidence. All exact answers and required evidence checks pass; all planted unsupported claims are rejected. Model responses can be scripted for deterministic reliability tests, but label those separately from model-driven quality results. Run existing research and H0–H7 regression suites.

### W2 — Bring the real browser into canonical runs

Files: `managed_browser.py`, `agent_tools.py`, `autonomous_agent.py`, `harness.py`, browser integration tests.

- Add strict schemas and broker adapters for browser open/observe/act/tabs/switch/scroll/download/close. Bind each browser context and observation to its session and capability grant. Keep a stable execution owner for Playwright rather than sharing synchronous browser objects across arbitrary threads.
- Route supported legacy browser operations to the managed backend without breaking existing users. Advertise the available actions accurately to the model and doctor output.
- Ground actions using current observed references; validate URL transitions, uploads and download destinations at the broker boundary. Fresh profiles by default; user account profiles require an explicit configured scope. Fixture pages and fetched text cannot grant new execution permissions.
- Persist action receipts and resulting observations; attach browser handles to checkpoints but invalidate/reconcile them after backend loss. Make cancel stop owned browser work promptly. Verify tab/session isolation across simultaneous tasks.
- Validate form state, saved/downloaded bytes and application state independently. A successful click call or screenshot alone cannot complete the task.

Exit gate: retain B01–B20 and add five actual agent-loop journeys: search→open→cite, multi-tab comparison, form edit/submit with fixture database check, download→local analysis, and browser loss/cancel→explicit recovery. Repeat each state-sensitive journey three times from clean contexts. These run on local fixture sites and require no VM.

### W3 — Dependable terminal/files and real continuation

Files: autonomous tool schema/dispatch, `harness.py` ProcessSupervisor and continuation, CLI/session commands.

- Expose start/poll/stdin/cancel as typed, durable agent tools with bounded log chunks/cursors, timeout and explicit cwd. Associate every process with its session; do not treat starting a background process as completion of the requested task.
- Define actual restart semantics: either an owned supervisor survives the CLI and permits authenticated reconnection, or CLI loss terminates the process and marks the receipt interrupted/uncertain. Do not promise reattachment to an in-memory process table after process death. Preserve durable logs and reconcile state before retry.
- Test actual subprocess kill/restart, cancellation during a running tool/provider call, and interruption before/after a mutation receipt. Never replay uncertain writes automatically. Persist usage through resume and deny new work after exhaustion.
- Bind coding verification to changed artifacts and explicit requested scope. Add validators for common CSV/JSON/report outputs, including content correctness; syntax or file existence alone is insufficient.
- Calibrate context profiles for configured local providers. Preserve conservative fallback, but report it and measure avoidable context loss. Exercise three compactions over real tool output while preserving constraints and evidence.

Exit gate: eight journeys: inspect/patch/test repair, failed tests, user edit conflict, Unicode/space paths, interactive stdin process, cancellation canary, real CLI process restart, and three-compaction long work. Every expected outcome/side effect is independently validated. Add a 30-minute deterministic multi-action soak with forced interruption, bounded resources and no orphan processes; keep that result separate from a model-driven long task.

### W4 — Windows setup and practical capability discovery

- Extend `doctor --json` to test the installed browser backend, configured model profile, search provider availability, extraction dependencies, workspace writability, artifact/session persistence and process operations independently. Report configured, available, tested and unavailable separately; do not print secrets.
- Use one discoverable profile for research and one for local execution, with explicit capabilities, model context, budget and output contract. Do not hard-code a second planning engine for either profile.
- Make CLI and desktop adapter return the same session ID, progress, remaining budget, artifact locations, unresolved work and resume instructions. Verify the relevant UI actually handles these fields, not only that the adapter can return them.
- Repeat a clean Windows package install from the final wheel outside the source tree without PYTHONPATH. Document required browser/extraction extras and test missing-dependency messages. Use actual configured provider settings without displaying credentials.

Exit gate: installed CLI research/browser/file/terminal smoke journeys pass; desktop canonical adapter matches session events/outcomes. Release manifest distinguishes Windows-ready features from deferred Linux/VM features. Tests and package smoke must cover the same final commit.

### W5 — Practical autonomous acceptance and release decision

Build a sealed **24-task Windows acceptance pack**: 8 research, 6 local coding/data/artifact tasks, 5 browser workflows and 3 mixed research→browser→local-output tasks, plus 2 long-running tasks. Keep exact task inputs and reference answers separate from the agent workspace. Choose task-specific validators before runs.

1. Run the deterministic integration gates first, with clean sessions/memory/skills and delegation disabled by default. Gate on 100% expected reliability outcomes, including correct failed/partial statuses, zero false-completion cases, no boundary escapes and no orphan work.
2. Run the 24 tasks with the intended model/provider, three clean repetitions each. Suggested promotion threshold: at least 90% overall validated success and 80% in every category, with zero false completions or budget/cancellation violations. These are proposed readiness thresholds, not current scores. Record correct abstentions separately from task success.
3. Predeclare model, pricing/configuration, task budgets and total evaluation cost ceiling. Begin with offline/local fixture runs; use a deliberately bounded provider-backed smoke slice before larger runs. Do not start paid official benchmarks as part of a documentation update.
4. Record per-task answer correctness, evidence precision/coverage, validator outcomes, duration, tool/model calls, actual/unknown cost, recovery events and artifacts. Failed tasks remain in the denominator. Fix issues on development fixtures and replace contaminated held-out tasks before rerunning acceptance.
5. Publish `release/WINDOWS_AUTONOMY_READINESS.md` and update capability status only for the measured scope. If thresholds fail, keep the feature experimental and add a specific repair slice. “Fully ready” never means silently ignoring failed or deferred gates.

Implementation order: **W1 → W2 → W3 → W4 → W5**. W1–W3 are the functional priorities; W4 packages them; W5 proves usability. Do not spend the next session on Linux, VM transport, new agent roles or UI expansion.

### Next implementation session handoff

> Work in `C:\Users\sujal\smara`. Read the current Windows readiness section of `SMARA_H3_H7_EXECUTION_PLAN.md`, repository instructions, and `release/H3_H7_GATE_REPORT.md`. Implement **W1 only**, continuing the existing H3–H7 code. First reproduce and fix the research claim checker accepting “99 kilograms” and negation against “42 kilograms.” Wire adaptive research graph/evidence state into the canonical autonomous session and broker, store recoverable original source artifacts, and gate final research completion on independently checkable claim evidence. Add the 12 specified end-to-end fixtures and preserve existing tests. Record exact tests, artifacts, limitations and a scoped completion commit. Preserve unrelated working changes. Linux/WSL, VM/OSWorld, paid official benchmarks and delegation promotion are deferred. Do not claim Windows autonomous research ready until W1–W5 evidence exists.

### Fresh review evidence

Read-only source inspection and an offline in-memory claim-support probe were performed on 2026-09-08. The probe returned `(True, 'supported')` for “99 kilograms”, “42”, and “The value is not 42 kilograms.” against “The value is 42 kilograms.” No network/provider calls or application source edits were needed. Existing uncommitted report/test/artifact changes were preserved.

Fresh full test command: `$env:PYTHONPATH='src'; .venv/Scripts/python.exe -m pytest -q`. Result: **497 passed, 1 skipped, 2 warnings in 97.89 seconds**, exit code 0. The warnings concern the existing short JWT test key. Passing existing tests does not invalidate the uncovered claim-support counterexamples or prove missing integration paths.

---

## Original H3–H7 specification (2026-09-07; historical)

## Objective and scope

Make sustained local work dependable through one durable execution engine, then expand research, browser, desktop and delegation capabilities. Interactive CLI, headless evaluation and application clients must consume the same execution and verification contracts. This document continues H1/H2; it does not restart the rebuild or claim that planned benchmarks have passed.

The deleted audit documents are not dependencies. The user's supplied H3–H7 requirements are the roadmap contract. Existing root roadmaps remain historical context; use this file for the next execution sequence. No paid benchmark is required to begin.

## Current capability assessment

Reviewed HEAD: `17ec93e` (`fix: complete durable brokered harness gates`), following `0790900` (H1 session engine) and `403b5fd` (H0 baseline). The working tree already contains changes to `src/smara/autonomous_agent.py`, `tests/test_task_planner.py`, and SWE report artifacts. Preserve these; findings here describe the working checkout, not only HEAD.

| Area | Present implementation | Remaining work demonstrated by source inspection |
| --- | --- | --- |
| Durable execution | `src/smara/harness.py`: SQLite sessions, call states, receipts, content-addressed artifacts, typed contracts, session lock, resume/cancel | JSON CLI invokes the autonomous loop as one opaque `agent_turn`; individual model/tool operations need durable admission and continuation |
| Tool/process safety | `ToolBroker`: schema/capability checks, resolved paths, guarded atomic writes, supervised process operations, cancellation | Broker coverage must include the actual autonomous tool catalogue; direct broker tests do not establish every entry-point guarantee |
| Completion evidence | Revision-associated focused/full evidence gates in `SessionEngine` | `cli.py` assigns full evidence to a changed, completed `agent_turn`; replace that inference with actual validator receipts |
| Budgets | Wall/tool/model/token/dollar fields and named profiles exist | `continue_run` checks wall time and outer tool count; inner provider calls, token/cost consumption and children need one enforced ledger |
| Context | `_compact_conversation_history` in `autonomous_agent.py`; `_compact_history` in `local_agent_runtime.py`; todo preservation and result offloading | Character limits remain; require one token-aware packer and recoverable, structured state |
| Progress | Signature counts and repeated-call warning in autonomous loop | Warning still permits execution; no durable progress-based loop breaker for rereads, alternating calls or todo churn |
| Research | `research.py`, `research_tools.py`, `deep_research.py`; source retrieval, scraping, synthesis and evidence tests | `scraped_content or snippet` fallback does not distinguish claim support rigorously; add question dependencies and passage/table provenance |
| Browser | `browser_sidecar.py`: fetch/inspection, screenshot and flow result infrastructure | A persistent interactive browser session with grounded actions is still required; HTTP navigation/text assertions do not prove interaction |
| Delegation | Roles, worktree isolation and batch orchestration in `subagent_orchestrator.py` | Thread pools and `os.chdir` remain; success is inferred from answer presence/API-error text, rather than canonical verified completion |
| App surfaces | Desktop/mobile directories, local planner and client tests exist | Keep their presentation/integration value while converging execution; different planners must not become separate autonomous brains |
| Packaging | `pyproject.toml` defines CLI/desktop entry points | Clean Windows/Linux installation and end-to-end release evidence remain distinct from source-tree tests |

These are integration gaps, not a claim that all H1/H2 code is defective. Keep the existing engine and incrementally connect its guarantees to real runs.

## Architecture contract

`CLI / benchmark / app adapter → SessionEngine → model step → admitted ToolCall → ToolBroker/backend → ToolResult + artifact → independent verifier → RunEvent / RunResult`.

1. Persist each model step, tool admission, result, usage update and continuation checkpoint. Do not hide an entire task inside one successful tool receipt.
2. The engine owns task state, budgets, cancellation and completion. Adapters own input/output formatting. Backends execute typed requests, without independent planning loops.
3. Evidence references identify immutable artifacts and their subject revision/state. A model declaration, screenshot existence or zero exit code from an arbitrary command is insufficient evidence of task completion.
4. Large outputs live in the artifact store; model context receives bounded excerpts plus resolvable references. Summaries never replace original evidence.
5. Crash recovery treats admitted mutations with unknown outcomes as uncertain. Inspect/reconcile before retry; never blindly replay.
6. Carry capability grants and budgets through every nested call. Remote pages, files and skill content are task data, not authority to change those grants.

## Delivery order

Implement **H3.0 → H3.1 → H3.2 → H3.3 → H4 → H5 browser → H5 desktop → H6 → H7**. Thin event adapters can be developed once H3.0 stabilizes, but application feature expansion must not delay execution reliability. Keep concurrent legacy delegation disabled in evaluated durable runs until H6 passes isolation gates.

### H3.0 — Connect the durable engine to the actual loop

Primary files: `harness.py`, `autonomous_agent.py`, `cli.py`, `local_agent_runtime.py`, and current benchmark adapters. Before changing interfaces, enumerate CLI interactive, ask, run/JSON, resume, goal, GAIA and desktop routes and record which engine each calls.

- Add an incremental session step API; preserve existing session schema through explicit migrations. The model proposes calls; the engine admits and records each call before execution.
- Route autonomous tools through broker adapters; inventory missing schemas and effects. Preserve ordered mutations and read-after-write dependencies. Unknown effects default to serialized execution.
- Record provider request IDs, model usage, retries and response status. Reserve budget before calls, reconcile actual usage afterward, and deny further calls when exhausted. Treat missing usage as unknown with a conservative reservation, never as free. Retry costs count.
- Enforce deadlines and cancellation inside provider/tool execution, not only between outer calls. Define paused/resumed wall-time accounting and test it; restarting must not reset spent budget.
- Replace synthetic `agent_turn` full-test metadata with verifier-owned evidence including command, scope, revision and output artifact. Validate the requested output contract for non-coding tasks too.
- Persist model history/state sufficient to resume at a step boundary. Existing ambiguous sessions must remain inspectable and report uncertainty instead of silently rerunning the whole prompt.

Gate: one scripted offline edit→failed test→repair→passing test scenario produces equivalent semantic events/results through interactive and JSON CLI and the benchmark adapter. Kill after admission and after result persistence; no duplicate mutation. Exhaust model/token/cost budgets inside a multi-call run and verify no subsequent call occurs. Cancellation stops owned work. Preserve all H0–H2 regression behavior.

### H3.1 — Token-budgeted packing and structured continuation

Introduce small modules such as `context_packing.py` and `continuation.py`; integrate both existing compaction call sites rather than adding another independent compressor.

- Define a model context profile with tokenizer identity, input capacity, output reserve and protocol/tool-schema overhead. Count the complete serialized request, including attachments and tool definitions. When exact tokenization is unavailable, use a documented conservative bound and mark accounting quality; avoid a universal characters-per-token assumption.
- Enforce `packed input + reserved output + safety margin <= context capacity` before every model call. If mandatory state cannot fit, offload optional content or stop with a recoverable explanation; do not truncate instructions or tool-call pairs into invalid messages.
- Persist a versioned handoff: objective, user constraints, acceptance criteria, task IDs/status, decisions, unresolved questions, changed paths/revisions, failed and passing evidence IDs, pending/uncertain calls, active process/browser handles, spent/remaining budget, and next action.
- Pack mandatory constraints and state first, relevant evidence excerpts next, recent complete turns next, then optional history. Preserve assistant/tool protocol grouping. Store the original transcript and summary lineage.
- Resolve artifact IDs on demand with hash checks. Missing/corrupt evidence invalidates dependent claims. Artifact retention must protect referenced checkpoints. Compaction must not promote failed tests to passing or stale evidence to current.

Gate: force three compactions during the same task, restart after each, and compare required task state/evidence references to a no-compaction control. Every referenced artifact resolves unchanged; the final validator outcome matches. Exercise Unicode, huge schemas, oversized tool output and low-context profiles.

### H3.2 — Progress-aware stall and anti-thrashing detection

Introduce durable progress records, not just tool counters. Progress includes changed file/content hashes, newly resolved questions, new supporting evidence, validator transitions and task acceptance results. Editing todo wording is not progress.

- Fingerprint tool + normalized arguments + observed subject revision. Unchanged repeated reads reuse receipts; changed files and advancing process output permit fresh reads.
- Detect repeated identical failures, alternating A/B read loops, edit/revert cycles, and todo churn over a bounded window. Persist counters across compaction and restart.
- Escalate through cached result → explicit strategy-change request → bounded recovery attempt → recoverable stop with blocker/evidence. Initial thresholds are configuration values to calibrate against fixtures, not universal truths.
- Use capped backoff for legitimate process polling and external waits. Avoid treating long-running tools or necessary revalidation after an edit as a stall.
- Emit progress/stall/recovery events with reason and triggering receipt IDs. Never label a stalled task completed.

### H3.3 — Reliability fixtures and long-run gate

The original R25–R32 definitions are unavailable. The following are **new proposed definitions**, not a reconstruction of deleted fixtures. Place them in a versioned manifest before implementation.

| ID | Fixture | Independent acceptance check |
| --- | --- | --- |
| R25 | Three forced compactions during repair | Constraint/task-state parity and 100% required evidence-ID retention |
| R26 | Context saturation, Unicode and large schemas | Every submitted request fits the declared budget and remains protocol-valid |
| R27 | Restart immediately after compaction | Exact checkpoint lineage and next pending step; no replayed mutation |
| R28 | Missing/corrupt/stale evidence | No completion relying on invalid artifact or revision |
| R29 | Identical and alternating read loops | Bounded calls and observable recovery/stop without false progress |
| R30 | Todo churn and edit/revert thrashing | Churn cannot reset the stall window or certify progress |
| R31 | Real progress amid repeated reads/polls | Necessary reread/revalidation succeeds without premature stall stop |
| R32 | Budget/cancel through compaction and resume | Aggregate consumption persists and no work executes after enforced stop |

H3 exits when all eight cases pass, three forced compactions preserve required references, adapter parity passes and H0–H2 remain green. Add a deterministic long-duration process/restart soak and a separately budgeted model-driven long task; report them separately.

### H4 — Adaptive research and multimodal evidence

Build on current research retrieval; add `research_graph.py` and `evidence_index.py` or equivalent focused modules.

- Persist a question/hypothesis DAG: node question, dependency IDs, unresolved/supported/refuted/blocked state, evidence IDs and stopping criterion. Resolve prerequisites before dependent synthesis; prevent cycles and cap expansion. Contradictions trigger targeted retrieval within budget.
- Store canonical URL, redirect chain, retrieval time, content hash and extraction version. Deduplicate identical content while retaining independent publications and dated versions. Explicitly type search snippet, fetched passage, PDF page/table and image/OCR evidence.
- Fetch full evidence for claims that need it. Snippets may guide discovery but cannot silently satisfy full-page/table support requirements. Preserve retrieval failures and uncertainty.
- Attach claim citations to exact passage offsets/text hashes, page/bounding box or table row/column coordinates. Verify cited text exists, supports the claim and preserves units, dates and qualifiers. Combine deterministic provenance checks with separate semantic assessment; URL count is not accuracy.
- Store original PDFs/images and extraction/OCR provenance. Test scanned tables, conflicting units and uncertain OCR rather than assuming extraction correctness.

Gate: sealed local research corpus with multi-hop, contradiction, duplicate, unavailable-page and multimodal cases. Exact-answer and citation-support checks must both pass. Fresh session/memory/skills per case; no answer keys in agent-readable paths. Publish answer accuracy and evidence precision/coverage separately. Live-web evaluation is a separate reproducibility tier.

### H5 — Stateful browser, then isolated desktop

**H5a browser:** retain the sidecar interface where useful, replacing execution internals with a managed Playwright/CDP backend. Choose/pin supported versions when implementing and validate against their official documentation.

- Browser context/session ownership, stable tab IDs, navigation lifecycle, popups, frame identifiers, dialogs, downloads and explicit profile isolation.
- Accessibility/DOM snapshots and screenshots carry observation ID, tab, URL, timestamp and page generation. Element references expire when relevant state changes; refresh before acting on stale targets.
- Click/type/scroll requests reference observed elements or calibrated screenshot coordinates. Record action attempt and resulting state separately. Never return a successful fallback image or pass an unsupported action.
- Cancel/close owned resources predictably. On process restart, reconnect only after validating ownership/state; otherwise invalidate handles and recover explicitly.
- Validate changes using fixture application state, persisted form data, downloads or DOM conditions, not action return codes alone.

Define B01–B20: navigation, link click, text entry, submit, checkbox, select, scroll/lazy content, new tab, tab switch, popup, iframe, dialog, download, upload, SPA update, delayed element, stale reference, navigation failure, restart recovery, cancellation. Use a deterministic local fixture site and reset its database per case.

**H5b desktop:** add a separate OS backend behind the same broker, executed in a disposable VM with reset snapshots. No host desktop benchmark actions.

- Record screenshot pixel size, display origin, DPI scaling, viewport/crop transforms and target window. Transform image coordinates to guest coordinates explicitly; reject stale or out-of-bounds observations.
- Run observe→ground→act→observe with a deadline, action budget, interrupt path and guest process ownership. Handle focus changes, dialogs, scaling and multiple displays.
- Keep clipboard/file transfer scoped to the fixture. Restore the VM between cases and independently inspect saved files/application state.

Define D01–D20: launch, focus, type, hotkey, click, drag, scroll, save, open, window move, resize, dialog, clipboard, DPI transform, display offset, occlusion, stale frame, restart, timeout and cancellation. Report these internal desktop cases separately from official OSWorld results.

Gate: all 20 browser and 20 desktop fixtures pass real state validators; unsupported setup is blocked/skipped, never passed. Repeat state-sensitive cases three times from clean state. Store actions, observations, validators and reset identity.

### H6 — Bounded delegation and useful learning

- Replace thread-pool workers with supervised child processes using explicit `cwd`, filtered environment, isolated workspace and structured IPC. No parent-process `os.chdir`. Reuse H2 supervision rather than creating unrelated lifecycle control.
- Parent reserves child budget atomically before launch. All descendants draw from the root ledger; cap depth, fan-out, model/tool usage, money and deadlines. Cancellation kills descendants and reconciles reservations once. Parent crashes must not leave unowned workers spending budget.
- Workers use the canonical session engine. Return status, evidence IDs, usage, patch and base revision. Answer presence is not success. Parent validates patches on an integration worktree before accepting changes; handle conflicting edits explicitly.
- Skill candidates in `.smara/skills/` carry provenance, capabilities, procedure version and evaluation history. Keep candidates quarantined. Promote only after held-out transfer tests pass; support revocation and rollback. Exclude credentials, copied benchmark answers and task-specific memorization.

Gate: adversarial nested delegation cannot exceed root budgets; process timeout/cancel leaves no live descendants; concurrent worktrees do not interfere. A matched-model, repeated ablation must preserve success quality and improve predeclared latency and cost metrics versus one agent before delegation becomes a default. If it does not, retain opt-in delegation and publish the negative result. Evaluate promoted skills on fresh held-out tasks with memory isolation.

### H7 — Release evidence and application boundary

- Build/install wheels and command entry points in clean Windows and Linux environments. Test supported Python versions, spaces/Unicode paths, missing optional dependencies, fresh profiles, upgrade/migration and uninstall behavior. Avoid source-tree/PYTHONPATH dependencies in release checks.
- Expose a versioned canonical event envelope with sequence cursor, session ID, timestamps, reconnect/replay and terminal state. Define run/inspect/resume/cancel commands through an authenticated local transport with scoped workspace access.
- Desktop/mobile render events and submit user actions to the engine. Remove or adapt autonomous planning paths that bypass it. Test disconnected client/reconnect, duplicate commands, event gaps and cancellation parity.
- Publish capability rows as verified/experimental/unavailable, each linked to fixture manifest, validator outputs and environment. Separate offline deterministic gates from provider-driven measurements and official external benchmarks.

Gate: install-to-task smoke tests pass on Windows/Linux; application and CLI yield equivalent validated outcomes; all promoted capabilities have reproducible held-out scorecards. Historical 35/86 GAIA and four local coding exercises are not current release scores or official SWE-bench Verified results.

## Evaluation and evidence protocol

Create `tests/evals/local_execution/manifest.json` and fixtures/validators as implementation deliverables. The previously proposed 172 internal cases are not assumed to exist; recover their scope through a new versioned inventory with IDs, category counts, fixtures and owners before claiming coverage. R25–R32 and B01–B20/D01–D20 above are the explicitly specified minimum for this plan, not the entire 172-case suite.

For each run record commit plus dirty diff hash, engine/schema version, OS/Python/dependencies, model/provider/settings, fixture/split hash, seed, reset identity, capability grants, budgets and actual usage, result, independent validator outcome and evidence artifact IDs. Keep validator answers inaccessible to the agent. Report failed, blocked and skipped cases explicitly with the full denominator.

Use deterministic offline tests first, local real browser/VM tests second, matched-model held-out trials third, and paid official benchmarks only as a separate scheduled evaluation with explicit cost scope. Proposed 60% GAIA and 60% SWE-bench Verified remain aspirations until measured on named official splits/protocols. Pin sample selection before runs; do not tune on held-out failures and continue calling them held-out.

Repeat nondeterministic runs at least three times, report per-run outcomes, aggregate success, cost, latency and uncertainty. Declare ablation thresholds before measurement. Store no credentials in traces. Completion commits must contain scoped code/tests and a report linking artifacts; do not commit unrelated working changes.

## Original session handoff (superseded; do not execute)

> Work in `C:\Users\sujal\smara`. Read `SMARA_H3_H7_EXECUTION_PLAN.md` and repository instructions. Implement **H3.0 only**. Preserve pre-existing working changes. Map all execution entry points, connect individual autonomous model/tool steps to the existing SessionEngine and ToolBroker, enforce the shared usage/cancellation ledger, and replace inferred full-test evidence with actual validator receipts. Add offline parity, crash-resume, failed-verification and inner-budget regressions. Run H0/H1/H2 plus focused integration tests, record exact commands/results and limitations, and produce a scoped completion commit. Do not begin H3.1, browser/desktop features, paid benchmarks or delegation expansion until the H3.0 gate has evidence.

## Original inspection validation (2026-09-07)

Application source was not changed for this planning task. The system Python lacks pytest; the repository virtualenv contains pytest but requires `PYTHONPATH=src` for source checkout imports. The focused test command used is:

```powershell
$env:PYTHONPATH='src'
.venv/Scripts/python.exe -m pytest -q tests/test_h0_harness_regressions.py tests/test_h1_session_engine.py tests/test_h2_execution_broker.py tests/test_deep_research_evidence.py tests/test_subagent_orchestrator.py tests/test_benchmark_fairness.py
```

Result: **29 passed in 11.82 seconds**, exit code 0, on this Windows working checkout. This selected suite is regression evidence only; it is not the full test suite, a clean-package test, or a capability benchmark.
