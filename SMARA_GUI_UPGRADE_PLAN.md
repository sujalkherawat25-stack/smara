# Smara GUI Upgrade Plan

**Status:** next product slice — planning complete, implementation not started  
**Updated:** 2026-09-17  
**Scope:** Smara Desktop (`apps/desktop`) and hosted Smara web (`frontend`)

## The short answer

Yes. The CLI and control plane are now mature enough that the GUI should be the
next focus. We should upgrade the existing GUI instead of rebuilding it or
changing the working CLI path.

The GUI already has the right building blocks:

- Desktop chat, model settings, browser tools, terminal, workspace, memory,
  skills, DAG, swarm, tests, and integrations.
- Hosted Work with durable tasks, approvals, cancel/retry, live events,
  evidence, artifacts, and result inspection.
- Desktop Goals with real status, unresolved work, budget, artifacts, and
  resume information.

The problem is consistency. Chat uses a mostly separate path, the desktop
research screen is an older deep-only market view, the model pill is partly
hard-coded, and durable runs are not presented as one coherent timeline across
Desktop and Web.

## What is currently missing

1. One run model shared by chat, quick research, deep research, coding, and
   durable tasks.
2. A visible `Auto / Quick / Deep` choice in the main composer, with Auto able
   to explain why it chose a lane.
3. A model/profile picker that reflects the configured profiles rather than a
   display-only model toggle.
4. One run center showing active, completed, failed, cancelled, and
   needs-input work. The desktop loads tasks but does not yet make that task
   list the primary navigation surface.
5. One result view that always shows status, answer/output, unresolved work,
   budget, evidence, artifacts, and a safe resume action.
6. A research workspace that supports both quick and deep work, progress,
   sources/citations, report artifacts, cancel, and resume. The current
   desktop Browser research tab is deep-only and still uses older
   “market-intelligence” terminology.
7. Clear capability and permission states for local files, browser, terminal,
   documents, and future OCR, without exposing credentials.

## Recommended build order

### Phase 1 — Shared run contract and run center

Create a small UI-facing `RunSummary`/`RunDetail` contract mapped from the
existing task and CLI result fields. It should include:

- status and truthful terminal state;
- selected lane and the Auto lane decision/reasons;
- current stage and a compact event timeline;
- budget, elapsed time, and cost when available;
- answer/output, unresolved work, evidence, and artifacts;
- resume command and safe actions such as cancel, retry, or open artifact.

Use this contract in both Web Work and Desktop. Make the Desktop sidebar show
the loaded task list and open the same detail surface when a run is selected.

**Done when:** a user can find any run and understand what happened without
opening the CLI or reading raw logs.

### Phase 2 — Better composer and model controls

Add to the main composer:

- lane selector: `Auto`, `Quick research`, `Deep research`, and normal task;
- configured model/profile selector with local/hosted labels and health state;
- a short estimate for deep work (time, source range, and budget);
- clear “Auto chose … because …” feedback after dispatch.

Pass `research_mode` and `tool_profile` through the Desktop chat command while
keeping old callers compatible. Do not show raw API keys.

**Done when:** Desktop and Web dispatch the same lane/profile semantics as the
CLI, and the selected profile is real rather than decorative.

### Phase 3 — Research workspace

Replace the old deep-only Browser research presentation with a focused
research surface:

- one question box and lane selector;
- live stage progress (`planning`, `searching`, `fetching`, `checking`,
  `synthesizing`, `complete`);
- source list with domain, freshness, citation/evidence status, and
  contradiction warnings;
- quick answer view and deep report view;
- open/download report, cancel, resume, and retry;
- visible source count and bounded progress for deep runs.

The existing browser scrape/E2E tools remain available as separate tools; they
should not be confused with research mode.

**Done when:** the GUI can start and follow both quick and deep research with
the same truthful outcome fields as the CLI.

### Phase 4 — Capability and permissions center

Make local capabilities understandable before they are used:

- show whether Desktop is paired and online;
- show approved workspace, browser, terminal, and connector scopes;
- show document/OCR capability as available, unavailable, or remote;
- explain approvals in plain language and link each artifact to its source;
- keep secrets represented only by profile name/alias and health status.

This is where future OCR and ingestion providers will appear, but they must
remain visibly unavailable until their entitlement and acceptance gate pass.

### Phase 5 — Polish and release verification

- responsive Desktop and Web layouts;
- consistent loading, empty, needs-input, failure, and partial-result states;
- keyboard shortcuts and accessible labels;
- no false “completed” banners;
- Desktop production build, Web production build, Rust check, and the existing
  Python regression suite;
- a small GUI smoke matrix: Auto, Quick, Deep, resume, artifact open, cancel,
  failed run, and approval-required run.

## Explicitly deferred future work

These items remain in the roadmap and are not prerequisites for the GUI
upgrade:

- **Unlimited OCR / live OCR provider acceptance:** future. The current laptop
  has an RTX 4050 with 6 GB VRAM; local Unlimited-OCR is not a reliable
  default. Plan for a remote service or a machine with at least the model's
  tested GPU headroom, then add it behind the same document capability
  contract.
- **Full multimodal ingestion:** future. YouTube transcripts/audio fallback,
  large PDF collection OCR, broad academic full-text databases, and
  multimodal document collections need separate bounded adapters and live
  acceptance gates.
- **Native VM/real desktop control:** future. The Docker guest path is the
  current safe validation environment; native VM/owner-device control remains
  unavailable until its isolation and recovery drill passes.
- **Linux/WSL packaging and validation:** future release work.
- **Multi-agent delegation and learned-skill promotion:** the control-plane
  foundation exists, but broad provider-backed delegation and automatic skill
  promotion remain opt-in/experimental until matched-budget ablations and
  promotion gates are complete.
- **Advanced/domain-specific analysis expansion:** deterministic descriptive,
  grouped, forecast, observational, and statistical checks exist; broader
  causal and domain-specific methods remain future work with independent
  validators.

## Definition of done for the GUI slice

The GUI upgrade is ready when a user can:

1. type one request and choose Auto, Quick, or Deep;
2. see the real configured model/profile and the lane decision;
3. watch a truthful run timeline;
4. inspect the answer, unresolved work, budget, evidence, and artifacts;
5. resume, cancel, retry, or open the generated report safely; and
6. see the same outcome from Desktop, Web, and CLI without contradictory
   status labels.

The first implementation should be Phase 1, then Phase 2. OCR, Unlimited OCR,
and the other deferred capabilities should not be pulled into the GUI sprint.
