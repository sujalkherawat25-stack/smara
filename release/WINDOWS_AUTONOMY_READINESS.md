# Windows autonomy readiness decision

## Implementation update — 2026-09-12

The remaining local implementation work found by the corrected audit is now in
place:

- durable process handles are session-owned; foreign-session poll, stdin and
  cancellation are denied before a process side effect;
- background process deadlines are enforced without requiring a model poll;
- finalization refuses to complete while an owned process is running, failed,
  timed out or has uncertain restart state;
- JSON, CSV and report artifact contracts are evaluated against immutable content
  hashes and persisted as validator receipts at finalization;
- CLI runs expose `--tool-profile` (`full`, `research`, or `coding`) and preserve
  the selection across resume;
- the application adapter resolves the same saved model profile and credentials as
  the CLI and returns the canonical session envelope;
- Desktop renders actual status, answer, events, remaining budget, artifact paths,
  unresolved work and the resume command. It no longer labels an unknown result
  completed;
- doctor resolves the active saved credential without printing it, tests the real
  browser backend, and reports the configured context capacity.

Verification after the provider-smoke fixes: **624 passed, 1 skipped, 2 existing
short test-key warnings in 173.74 seconds**. The focused W3/W4/adapter matrix passed 21 tests and
the Desktop TypeScript check passed. A clean virtual environment with system site
packages disabled installed the rebuilt wheel plus its browser extra. Installed
doctor and content-checked browser/file/terminal/persistence smoke tests passed from
a separate Unicode/space workspace without `PYTHONPATH`. Final wheel SHA-256:
`03cd171eb15165f60404c72e6e2bc20185bfc3ae4ef2b7a624035160f84e7573`.

The code implementation and deterministic local verification are complete for the
active Windows research/browser/file/terminal scope. On 2026-09-12 a temporary
credential was used without persisting it to run a bounded canonical-agent smoke
against Sarvam `glm5.3-flash`: research claim evaluation, a JSON artifact task and
a real managed-browser form task all completed and passed independent state/content
validators (**3/3**). The runs used 15 model calls, 11 tool calls and 104,944 billed
tokens; Smara's conservative reservation accounting was $0.15, which is not a
provider invoice. Evidence is in `release/evidence/W5_PROVIDER_SMOKE_2026-09-12.json`.
Its SHA-256 is `b291250ee7a7f373a25ddee58e5cce2c078e0e172fb928c0c949be153ef109af`.

The smoke exposed and drove fixes for two real defects: UTF-8 text/Markdown/CSV/JSON
could not enter the artifact-backed research evidence path, and a passing declared
artifact validator was evaluated too late to satisfy the current-revision
verification gate. Both now have regressions. The overall capability is
`experimental_provider_smoke_verified`, not promoted: the intended-model 24×3 W5
acceptance matrix is still unmeasured, and the older 72-run component pack below
remains regression evidence rather than autonomous acceptance.

## Corrected gate assessment — 2026-09-10

This assessment supersedes the promotion claims below. Windows readiness is
**not established**. The 72 passing W5 repetitions invoke components directly;
local outputs are written by the test and mixed tasks do not run a browser.
They are development regressions, not 72 validated autonomous task runs.
References were changed after the first run, so this pack is not held out.
Zero false completions and boundary escapes were not measured by this pack.

The soak measures elapsed wall time and SQLite reopens in one Python process.
It bypasses durable call admission and inspects a process table that does not
contain its synchronous subprocesses. It does not prove the required forced
CLI interruption, enforced aggregate budget, or absence of OS-level orphans.
Its original result is retained as limited evidence, not a passed W3 gate.

W3 still needs canonical agent journeys, actual CLI kill/restart, enforced
process deadlines without polling, validators wired into finalization, and a
qualifying soak. W4 needs CLI profile wiring, actual UI field handling and an
isolated install with system site packages disabled. W5 needs replacement held-out
tasks exercised through the canonical agent and then the intended provider.
Environment-variable checks alone do not establish whether stored provider
credentials are configured; inspect the application's configuration resolver.

On 2026-09-10, process ownership checks were added before polling, stdin writes
and cancellation. Foreign-session access and path traversal are rejected before
any process side effect. Focused verification: 24 passed in 10.09 seconds.

Date: 2026-09-09. Decision: **deterministic Windows scope verified; provider-driven
autonomy remains experimental**.

## Measured result

The sealed 24-task pack contains 8 research, 6 local coding/data/artifact, 5
browser, 3 mixed research/browser/output, and 2 long-work tasks. Inputs are in
`tests/evals/windows_acceptance/manifest.json`; reference outcomes are kept in a
separate validator file and are never copied into task workspaces. Validators were
predeclared. Delegation was disabled. Each task ran from a clean temporary workspace
three times.

Deterministic result: **72/72 task runs validated (100%)**, with **100% in every
category**, zero false completions, zero boundary escapes, and zero orphan work.
The additional pack-integrity check brings the pytest result to **73 passed in
30.61 seconds**. This exceeds the proposed 90% overall/80% per-category reliability
threshold for the deterministic tier.

Manifest SHA-256: `76b0949fda5f530e377ffbe00509e99d4d69ffb5d8348b3616d61b8143b80836`.
Reference SHA-256: `ba1f29d834c7c8cdfb7077be9872ed92a669e048030fb04b8f06d09aaa3e3c18`.
Fixture SHA-256: `4efd92be58705320b2a332830eecbda1468b2d5ef9d204e24d1a4b31db6480d2`.

## Environment and cost

Base commit for the acceptance run: `9c2aca5309a9596078f1613991dbdc4f93184a87`;
pre-scorecard dirty diff hash: `0e8cbba7e92e1d49e716983774063bba7361ca25`.
Windows, Python 3.14.0, local Chromium through Playwright. Docker Desktop engine
29.5.3 (`linux/amd64`) was healthy but Linux is deferred by the active plan. Offline
fixture cost was zero. No paid provider calls were made.

Supplemental only: the same wheel installed in the local `python:3.12-slim`
Linux container and passed content-checked file/read/process execution
(`LINUX_WHEEL_SMOKE_OK`). This is useful portability evidence but does not replace
the deferred Linux dependency/browser/upgrade matrix and does not promote Linux.

## Release boundary

No model/search provider credential is configured in this environment. Therefore
the mandated intended-model 24×3 trial and bounded provider smoke slice were not
run, and no model success/cost score is claimed. The capability remains
`experimental_provider_unmeasured`; deterministic research, browser, files,
terminal, persistence, packaging and adapter behaviors are verified at their
documented scopes. OCR is unavailable. Linux, VM desktop, OSWorld, official GAIA
and official SWE-bench Verified remain deferred or unmeasured.

The 30-minute deterministic W3 soak passed and is recorded separately at
`release/evidence/W3_30_MINUTE_SOAK.json`: 3,728.282 elapsed seconds, 226 forced
reopens, and zero orphan processes. Evidence SHA-256:
`4177762af6162215876834ea790a9de577fb99743eae25d880891c978435d45b`.

Final source regression after adding the pack: **616 passed, 1 skipped, 2 existing
JWT-key warnings in 178.13 seconds**.
