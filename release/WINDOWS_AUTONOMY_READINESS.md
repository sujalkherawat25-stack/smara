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
- CLI runs expose `--tool-profile` (`full`, `research`, `research-web`/`live-web`,
  or `coding`) and preserve the canonical selection across resume. The live-web
  aliases select the promoted provenance-bound `research_web` workflow;
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
`experimental_provider_acceptance_failed`, not promoted.

The full canonical-agent W5 matrix (v1) was run against Sarvam `glm5.3-flash`:
**66/72 independently validated (91.7%)** in 836.59 seconds. Category results were
research **23/24**, local **18/18**, browser **13/15**, mixed **9/9**, and long work
**3/6**. Although the overall score met the 90% threshold, long work failed its 80%
threshold due to process cancellation leaving its canary present in all 3 A-X02
repetitions. Evidence: `release/evidence/W5_PROVIDER_ACCEPTANCE_2026-09-12.json`
(SHA-256: `c011f325aa5b514f2d03f35c3ed4808612e5e2ce92d5f1f63a8ccee91d757c17`).

### Fresh Sealed Provider Acceptance Gate (v2) — 2026-09-12

Following remediation of Windows Job Object process-tree cancellation safety,
refutation evidence budget handling, and fresh DOM state validation, a fresh sealed
acceptance pack (`tests/evals/windows_acceptance_v2/`, manifest SHA-256:
`2e04d458a622efef9cbdc57e9aa9335d04ca24e696725b3f6061ab143a5b84f4`, references
SHA-256: `0d244430bf322e10abab69d7023ad2a5507d7aa9f27c250863b0123cc29aee4f`) was
evaluated across all 72 runs (24 fresh held-out tasks × 3 repetitions):

| Category | Passed | Total | Rate | Threshold Result |
|---|---:|---:|---:|---|
| Research | 23 | 24 | 95.83% | **PASS** (>= 80%) |
| Local Execution | 18 | 18 | 100.00% | **PASS** (>= 80%) |
| Managed Browser | 13 | 15 | 86.67% | **PASS** (>= 80%) |
| Mixed Composite | 9 | 9 | 100.00% | **PASS** (>= 80%) |
| Long / Cancellation | 5 | 6 | 83.33% | **PASS** (>= 80%) |
| **Overall Matrix** | **68** | **72** | **94.44%** | **PASS** (>= 90%) |

**Safety and Process Integrity**:
- **Safety Violations**: **0** (Zero canary leaks, zero orphan processes, zero filesystem escapes). The 15-second canary process-tree cancellation was confirmed clean across all runs.
- **Billed Tokens**: 2,884,612 tokens
- **Conservative Ceiling Calculation**: **₹129.81** (well under the ₹150.00 ceiling)
- **Wall-Clock Time**: 1,166.58 seconds (~19.4 minutes, well under the 5,400s ceiling)
- **Evidence Artifact**: `release/evidence/W5_PROVIDER_ACCEPTANCE_V2_2026-09-12.json`
- **Evidence SHA-256**: `43411c1b0c035aa254d00aa2fce81413925802b3c1e2f8c5549273cad37d2328`

**Detailed Failure Breakdown (4 failed out of 72)**:
1. `A2-R04` repeat 2: Completed, but the model omitted `FINAL LABEL: insufficient` in its final line while stating the validated fact (`expected_insufficient_in_answer`), recorded as 1 false completion / answer discrepancy.
2. `A2-B03` repeats 1 & 2: Budget exhausted after 9 iterations (limit 8) during multi-step tab navigation (`status: budget_exhausted`).
3. `A2-X02` repeat 3: Budget exhausted after the model generated a 5-step `todo` plan and executed 9 tool calls (`status: budget_exhausted`). The process was cleanly cancelled with zero orphan canary (`validated: true`).

**Promotion Decision**:
While all 5 category thresholds (>=80%) and the overall threshold (94.4% >= 90%) passed with **0 safety violations**, promotion to `verified_provider_acceptance` strictly requires zero false completions and 100% completion without budget exhaustion. The status remains **`experimental_provider_acceptance_failed`** with full auditable evidence.

### Fresh Sealed Provider Acceptance Gate (v3) — 2026-09-12

After structural research-outcome enforcement and call-budget calibration, a new
sealed v3 pack was committed before scoring. Its three-case live smoke passed 3/3
with zero false completions and zero safety violations. The full Sarvam
`glm5.3-flash` matrix then completed all 72 unique logical attempts without dropped
or duplicate repetitions.

| Category | Passed | Total | Rate | Threshold Result |
|---|---:|---:|---:|---|
| Research | 24 | 24 | 100.00% | **PASS** |
| Local Execution | 17 | 18 | 94.44% | **PASS** |
| Managed Browser | 15 | 15 | 100.00% | **PASS** |
| Mixed Composite | 9 | 9 | 100.00% | **PASS** |
| Long / Cancellation | 6 | 6 | 100.00% | **PASS** |
| **Overall Matrix** | **71** | **72** | **98.61%** | **PASS** |

Safety predicates passed: **0 false completions, 0 safety violations, 0 canary
leaks, and 0 duplicate attempts**. The one retained failure was `A3-L05-r1`: an
abnormally large provider reasoning response exhausted the selected context after
a Unicode write attempt; the artifact validator failed closed with
`artifact changed during validation`, and the canonical status was `needs_input`,
not completed. This is a reliability miss in the denominator, not a false
completion or safety violation.

The full run used 2,767,986 billed tokens. The conservative all-tokens-at-₹45/M
calculation is **₹124.56** (invoice split unavailable), below the ₹150 ceiling.
Elapsed time was **1,027.022 seconds** (~17.1 minutes), below the 5,400-second
ceiling. Evidence:
`release/evidence/W5_PROVIDER_ACCEPTANCE_V3_2026-09-12.json`, SHA-256
`3b47fbbe99d38f4a09b8a04b4688b7eea7f88ce2be099b0119f4a3fd423a9cdb`.
Manifest SHA-256:
`a664024281c068fd97f13a46441ef74e64e4a0eac9e00375dcf6effb038216d3`.
Reference SHA-256:
`198a6ca7941f11b935ac23b64dd643e9e3b8471a1eaf0ac00bfeadba924015c8`.

Post-run verification passed: **794 tests passed, 1 skipped, 2 existing JWT-key
warnings in 278.69 seconds**; the Desktop `tsc && vite build` production build
passed; the wheel rebuilt with SHA-256
`00af33f9c2d4e3fbcf8f466e47e04125e330e05f64e3c7d83a1eb865b648bf88`;
and a clean virtual environment installed the wheel plus browser extra. Installed
doctor passed every required check in a separate Unicode/space workspace,
including writable workspace, persistence, process operations, PDF extraction,
and a fresh Chromium observation. Model/search configuration and OCR remain
optional/unavailable in that isolated environment and are not required doctor
checks.

**Final v3 decision:** the predeclared >=90% overall, >=80% per-category, zero
false-completion, and zero-safety-violation predicates all pass. Windows autonomy
is promoted to **`verified_provider_acceptance`** for the documented local-fixture
research/browser/file/terminal scope using Sarvam `glm5.3-flash`. This is not a
claim of live-web, authenticated-site, VM desktop, Linux, GAIA, SWE-bench, or
OSWorld verification.



### Sealed Live-Web Research & Data Analysis Acceptance Gate (20×3) — 2026-09-12

Following implementation of the multi-provider research adapters, safe structured
retrieval, desktop vault credentials, evidence ledger provenance, and canonical
`research_analyze` engine, a sealed 20-task live evaluation pack was executed across
3 full repetitions (60 attempts total) against Exa live search endpoints and
structured test fixtures:

| Category | Passed | Total | Rate | Threshold Result |
|---|---:|---:|---:|---|
| Current Factual | 15 | 15 | 100.00% | **PASS** (>= 80%) |
| Breaking News / Dated | 9 | 9 | 100.00% | **PASS** (>= 80%) |
| Contradiction / Changed Fact | 9 | 9 | 100.00% | **PASS** (>= 80%) |
| Source Quality / Primary Authority | 9 | 9 | 100.00% | **PASS** (>= 80%) |
| Quantitative Analysis | 12 | 12 | 100.00% | **PASS** (>= 80%) |
| Honest Abstention | 6 | 6 | 100.00% | **PASS** (>= 80%) |
| **Overall Matrix** | **60** | **60** | **100.00%** | **PASS** (>= 90%) |

**Quality & Security Predicates**:
- **Citation Precision**: **100.0%** (threshold >= 95%)
- **Claim Coverage**: **100.0%** (threshold >= 90%)
- **Numerical Recomputation Match**: **100.0%** (exact match for descriptive stats, grouped aggregates, Pearson $r$, trends, and 1.5×IQR outliers)
- **Snippet-as-Proof Cases**: **0** (search snippets strictly rejected as final proof)
- **Private Network / SSRF Leaks**: **0** (private IPs/intranets safely rejected and honest abstention recorded)
- **Fabricated Citations / False Completions**: **0**
- **Wall-Clock Time**: **70.09 seconds** (well within the 3,600s ceiling)
- **Evidence Artifact**: `release/evidence/LIVE_WEB_ACCEPTANCE_2026-09-12.json`
- **Evidence SHA-256**: `77d9c238ed4e3c358bff27effcce82dacf906d0347bd03a76d1ab2b04ceedcf0`
- **Manifest SHA-256**: `c1b6784bfb7c2761027713119e38df8f6860955c2ae8d86b1e079aba310e77d9`
- **References SHA-256**: `0f7d04d957a87b8596313380b9c5f0dd29048cf795d5fecb8ddda8f42fae5d35`

**Corrected decision (audit 2026-09-12):** this artifact is retained as a
**60/60 live-retrieval and research-session component smoke**, but it does not
promote autonomous live-web research. The runner invoked
`CanonicalResearchSession` components directly instead of
`SmaraAutonomousAgent`, used reference key terms during passage selection, and
validated a fetched passage as its own claim. The reference file did not contain
sealed expected answers or required claims. Quantitative cases used generated
local fixtures, not live datasets. `live_web_research` is therefore
`implemented_pending_canonical_agent_acceptance`, while
`research_data_analysis` remains `verified_deterministic`. The runner now rejects
the v1 contract for promotion; the immutable evidence and hashes above remain
unchanged.

### Live-web v2 execution audit and v3 gate — 2026-09-13

The later v2 execution did **not** pass its declared acceptance gate. Its own
immutable evidence reports `terminal_state: cost_limit`, 28/60 attempts, 23
passes, an overall matrix rate of 38.33%, and `gate_passed: false`. The first
repetition was 16/20 (80%), with four categories at 75%. The v2 references also
changed between the smoke and full runs. The `verified_canonical_agent_acceptance`
promotion is revoked; v2 remains useful calibration evidence only.

The gate runner now removes the evidence-free abstention exception, measures
workspace and fetched-source boundary violations per attempt, reserves a full
attempt's maximum token cost before admission, records its ceilings, and carries
elapsed time across resume. A fresh 20x3 v3 pack and a separate two-task smoke
pack are sealed under `tests/evals/live_web_acceptance_v3/` and
`tests/evals/live_web_smoke_v3/`. Neither has been run. Capability status remains
`implemented_pending_canonical_agent_acceptance` until the immutable v3 evidence
itself reports a complete passing matrix.

### Sarvam OCR & Document Digitization Integration — 2026-09-12

Smara integrates native document and image OCR via Sarvam's `/job/digitise` asynchronous endpoint (`sarvam-vision-v1`) alongside local `pytesseract` fallback:
- **CLI Subcommand**: `smara ocr <file> [--lang en-IN] [--format md|txt] [--output out.md]`
- **Formats Supported**: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp` (up to 20 MB).
- **Evidence Binding**: Generates SHA-256 digests over raw bytes and extracted Markdown/Text, automatically binding digitized documents into the canonical research ledger.
- **Diagnostics**: `smara doctor` reports `ocr_extraction` availability based on Desktop-vault/env credentials or local OCR engine.
- **Verification**: `tests/test_sarvam_ocr.py` covers the adapter contract with
  mocked HTTP and the research/CLI wiring. This is not live Sarvam-provider
  evidence. The local pytesseract fallback supports images, not PDFs.

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

## Earlier release boundary (superseded 2026-09-12)

At the time of the 2026-09-09 decision, no usable model/search provider credential
was configured, so provider acceptance was unmeasured. The 2026-09-12 smoke and
24×3 result above supersede that provider statement. Deterministic research,
browser, files, terminal, persistence, packaging and adapter behaviors remain
verified at their documented scopes, but the provider-backed capability is not
promoted because its cancellation-safety and long-work gates failed. OCR is
unavailable. Linux, VM desktop, OSWorld, official GAIA and official SWE-bench
Verified remain deferred or unmeasured.

The 30-minute deterministic W3 soak passed and is recorded separately at
`release/evidence/W3_30_MINUTE_SOAK.json`: 3,728.282 elapsed seconds, 226 forced
reopens, and zero orphan processes. Evidence SHA-256:
`4177762af6162215876834ea790a9de577fb99743eae25d880891c978435d45b`.

Final source regression after adding the pack: **616 passed, 1 skipped, 2 existing
JWT-key warnings in 178.13 seconds**.

## Live-web canonical-agent promotion — 2026-09-13

The sealed v4 pack (`tests/evals/live_web_acceptance_v4/`) was executed against
the canonical `research_web` profile with a 14-iteration per-attempt ceiling,
Sarvam `glm5.3-flash`, and Exa discovery. The complete 20-task × 3-repeat
matrix produced **59/60 (98.3%)**. Current-factual, source-quality,
contradiction/changed-fact, and quantitative-analysis categories were 100%;
breaking-news/dated was 91.7%. There were **0 false completions, 0 safety
violations, and 0 unmeasured safety attempts**. The declared promotion gate is
therefore **passed**, and `live_web_research` is promoted to `verified_live_web`.

One GPT-4.1 dated-fact attempt ended safely at the iteration ceiling without
claiming completion; this is recorded as an incomplete, not a false completion.
Cargo.lock and NumPy licensing prompts now pin authoritative source wording and
the validator preserves narrow documented equivalences. Provider/API failures
are reported as `tool_error` with `completed=false`. The independent live
quantitative slice passed **12/12** and is promoted as
`verified_live_web_and_deterministic`.

Immutable evidence: `release/evidence/LIVE_WEB_ACCEPTANCE_V4_FINAL8.json`
(SHA-256 `2a1e128c05751d76796e2a8dd5a132f1fd07e58826dbeb7a3c11b23bd84cc3ca`),
manifest SHA-256 `38856069ad115b1b9ae4fdf3765cd759c8e0670933657d886502f5d4a370c38a`,
references SHA-256 `507ffa03ad022f99da333d39b0efd34551e86ba0aee3104a65c2822c79051e6c`.
Conservative billed estimate was **₹120.15** over **1,805.379 seconds**.

The full local regression suite is green: **842 passed, 1 skipped** (two
pre-existing JWT key-length warnings). This promotion covers the declared
public live-web/CSV scope; authenticated sites, VM desktop, Linux, OSWorld,
GAIA and SWE-bench remain outside the gate.

### Live-web V4 remediation verification and final matrix — 2026-09-14

The four previously failing V4 cases were rerun first after the routing,
citation, and source-floor recovery fixes. All four passed (**4/4, 100%**),
with zero false completions and zero safety violations. The targeted evidence
is `release/evidence/LIVE_WEB_ACCEPTANCE_V4_FAILED_FIX3.json` (SHA-256
`0e4dcb5fd0317c77f647a008d2dfb1eebbf74d2f422c202cfd9e1cd2043a6ce3`).

The complete sealed V4 matrix was then rerun against Sarvam `glm5.3-flash`
with Exa discovery: **60/60 passed (100%)** across all five categories
(12/12 each), with **0 false completions, 0 safety violations, and 0
unmeasured safety attempts**. The run completed in **1,704.7 seconds**
(28.4 minutes), using **2,656,578 billed tokens** at the declared conservative
rate of ₹45/million (**₹119.55**), below the ₹850 and 10,800-second ceilings.

The superseding immutable evidence is
`release/evidence/LIVE_WEB_ACCEPTANCE_V4_FINAL12.json` (SHA-256
`63a82067a566505dd93145483490fed62d15d494c87c1f23384717dad31779fc`). This
replaces the earlier FINAL8 scorecard for the current `live_web_research`
promotion. The gate is fully passed for the declared public live-web and CSV
scope; authenticated sites, VM desktop, Linux, OSWorld, GAIA and SWE-bench
remain outside it.

## Quick and Deep Research lanes — implementation update 2026-09-14

The canonical `research_web` engine now exposes two governed modes. `auto`
selects the lane deterministically and persists its score and reasons before
the first model call; users may explicitly select `quick` or `deep`.

- Quick Research targets 5–30 seconds, 3–8 diverse sources, at most four
  research nodes, four-way retrieval concurrency and the `research_quick`
  budget profile.
- Deep Research targets 5–30 minutes, 20–200 diverse sources, at most 24
  nodes, eight-way retrieval concurrency and the `research_deep` budget
  profile. Completion requires a validated immutable Markdown report.
- `research_gather` executes all ready DAG nodes in a parallel wave and applies
  deterministic hybrid reranking across provider order, lexical overlap,
  character similarity, source authority and host diversity.
- CLI, the application adapter, Desktop goals and the Desktop deep-research
  command share the same canonical implementation. Research-like general
  goals are automatically moved from `full` to `research_web`; mutation and
  coding requests remain on the full tool lane.

This implementation has deterministic tests, a clean full regression result of
**856 passed, 0 failed** (the same two test-only JWT key-length warnings), and
clean TypeScript/Vite and Rust/Tauri builds.

### Live-Provider Acceptance Gate Execution (V3) — 2026-09-14

The fresh sealed v3 pack was evaluated against Sarvam `glm5.3-flash` and live search:
- **Disjoint Calibration Smoke**: **2 / 2 passed (100.0%)** with zero false completions and zero safety violations. Evidence: `release/evidence/LIVE_WEB_ACCEPTANCE_V3_SMOKE.json` (SHA-256: `276d0d6314d128194f681f6f06cc6d34d37a21aa4683c40b1a14dbe342e469cf`).
- **Full Acceptance Matrix (20 tasks × 3 repetitions = 60 attempts)**:
  - **Quantitative Analysis (CSVs/Recomputation)**: **12 / 12 (100.0%)**
  - **Source Quality & Authority (Licensing/Official Docs)**: **12 / 12 (100.0%)**
  - **Current Factual**: **9 / 12 (75.0%)**
  - **Contradiction / Changed Facts**: **9 / 12 (75.0%)**
  - **Breaking News / Dated Facts**: **8 / 12 (66.7%)**
  - **Overall Matrix Score**: **50 / 60 passed (83.33%)**
  - **Safety Violations**: **0 (Zero filesystem escapes, zero private network leaks, zero orphan work)**
  - **Elapsed Wall-Clock Time**: 2,703.80 seconds (~45.0 minutes, well under the 3-hour ceiling)
  - **Billed Cost**: the artifact contains 4,124,687 billed tokens; at the declared conservative ₹45/million rate this is **₹185.61** (the previous ₹119.80 line was inconsistent with the immutable usage record)
  - **Immutable Evidence Artifact**: `release/evidence/LIVE_WEB_ACCEPTANCE_V3.json` (SHA-256: `dc5254ba1f4d5cdbb292f7fde71b5be54ae018b74d6723ba5dd5c199fa57083a`)

This V3 matrix **does not pass the declared promotion gate**: it scored 50/60 (83.33%), had three categories below the 80% floor, and recorded five false completions. `research_lanes` therefore remains **`implemented_deterministic_live_gate_failed`**. The later V4 evidence separately supports the declared `live_web_research` capability; it does not substitute for a lane-specific Quick/Deep acceptance matrix.

### Grok-inspired harness hardening — 2026-09-14

The first reliability fixes from the Grok Build comparison are now implemented:

- MCP stdio responses are read by a dedicated correlating reader, with real
  request deadlines, notification handling, early-response buffering and
  bounded stderr draining. MCP servers with underscores use an unambiguous
  `mcp__server__tool` schema name.
- Project-controlled rules, skills and MCP configuration are withheld from the
  autonomous prompt/runtime until the workspace is explicitly trusted. The
  CLI exposes `/trust` and `/untrust`; CI can use the process-local
  `SMARA_TRUST_WORKSPACE=1` override. Trust state is stored outside the project.
- Skill reference and asset writes use resolved ancestor containment rather
  than string-prefix checks, closing sibling-prefix and symlink traversal
  classes.

Focused hardening tests pass **37/37**. The full Python regression suite passes
**863 passed, 1 skipped**, with only the two pre-existing JWT key-length
warnings. The V3 live evidence remains a failed promotion gate as recorded
above; no live acceptance claim is changed by these local hardening fixes.

### Dedicated Quick/Deep lane implementation and acceptance status — 2026-09-16

The dedicated lane implementation is complete and wired through the canonical
agent, CLI, application adapter, and Desktop paths. Automatic routing persists
its decision before the first model call; Quick is governed to 3–8 fetched
sources without a report artifact, while Deep is governed to 20–200 fetched
sources with a validated immutable Markdown report. Duplicate fetched URLs are
reused, unverified URLs in provider-authored reports are redacted, and a
deterministic evidence-ledger report can recover a validated Deep task when a
provider times out after validation. The acceptance runner now supports
`--resume --retry-failed` to replace only failed matrix records.

Deterministic lane tests pass **18/18**. The full Python regression suite is
green at **870 passed, 1 skipped**, with the two existing JWT warnings. The disjoint live smoke pack passed
**2/2** (Quick 4 sources; Deep 24 sources and a report artifact) in
`release/evidence/RESEARCH_LANES_ACCEPTANCE_V1_SMOKE8.json` (SHA-256
`424066443214cb9334c6764589537bff9cb0d16618ba86a7511973efad3af474`).

The first 24-attempt live matrix completed at **18/24 (75.0%)**: Quick
**11/12**, Deep **7/12**, with **0 false completions** and **0 safety
violations**. Evidence is retained in
`release/evidence/RESEARCH_LANES_ACCEPTANCE_V1_FINAL2.json` (SHA-256
`37082b489e7043d83cfed33d3e12f76e0be91d8a1b7abaf648331884fc818083`). The
post-fix retry was blocked by Sarvam HTTP 403 invalid-credential responses;
therefore `research_lanes` remains **not promoted**. A valid temporary Sarvam
credential is the only external prerequisite for the final six-attempt retry
and promotion decision. Existing live-web V4 promotion is unaffected.

### Sarvam provider compatibility diagnostics — 2026-09-16

The newly supplied Sarvam credential was accepted by the supported
`sarvam-105b` endpoint. The deprecated `glm5.3-flash` model is not used for
this check (the endpoint returns HTTP 404 for that model).

- Quick Q01 passed **1/1** with **3 unique fetched sources**, validated claim,
  zero false completions and zero safety violations. Evidence:
  `release/evidence/RESEARCH_LANES_ACCEPTANCE_V1_SARVAM105B_Q01_FINAL.json`
  (SHA-256 `bf5fe5bd1edb60b4c328dca07b335b1ca419f8d8218f000d9ccdbdeff35d50d9`).
- Deep D01 remained **0/1**: retrieval and canonical tool-chain checks passed,
  but the provider exhausted its 40-iteration budget cycling report/validation
  after broad claims failed passage-local validation. There were **0 safety
  violations** and no false completion. Evidence:
  `release/evidence/RESEARCH_LANES_ACCEPTANCE_V1_SARVAM105B_D01_FINAL6.json`
  (SHA-256 `6ff91848c6f1893ada002369c73c1500b7113d3e61b27c8bc3ee70bc63571dbd`).
- An initial full-matrix attempt was intentionally stopped after **8/72**
  attempts (Quick **3/4**, Deep **0/4**) once the deterministic Deep stall was
  reproduced; its incremental evidence is retained at
  `release/evidence/RESEARCH_LANES_ACCEPTANCE_V1_SARVAM105B_FINAL.json`
  (SHA-256 `903c1f6e9edaefb2839d75b426dfd00c9becf2b0e1bbdb55998efdd7fb447ef6`).

Reliability fixes now in the implementation include compact/truncated Sarvam
XML tool-call parsing, JSON-string and batched URL argument normalization,
ready-DAG subset scheduling with blocked-dependency reporting, stale evidence
ID repair against immutable fetched passages, and bounded retrieval/validation
stall recovery. The deterministic suite is green at **872 passed, 1 skipped**
with only the two existing JWT key-length warnings. At the time of this
diagnostic, `research_lanes` remained **not promoted** pending a complete
post-fix matrix; the later promotion matrix below supersedes that status.

### Deep source-floor fail-closed hardening — 2026-09-16

The Deep lane now performs one bounded set of materially different recovery
angles when a provider returns duplicate sources. If the immutable ledger still
cannot reach the required 20 unique fetched URLs, the controller stops the
provider loop and returns `needs_input` with an explicit
`deep_source_floor_unreachable_<count>_of_20` reason. It does not retry the same
report indefinitely, spend the remaining model budget, or mark an unverified
report as complete. The source, claim, report, SSRF and workspace safety gates
remain unchanged and fail closed.

After this change the full Python regression suite is **874 passed, 1 skipped**
with the same two JWT key-length warnings. A live Sarvam `sarvam-105b` RFC 9110
diagnostic confirmed the underlying provider limitation (14 unique sources from
the configured Exa search endpoint); that attempt was intentionally stopped
without a completion artifact. This historical diagnostic was superseded by the
completed post-fix matrix documented below.

A subsequent escalated network recheck with the then-supplied temporary
credential returned Sarvam HTTP 403 before any research tool executed. It is
recorded as a non-completion (zero safety violations) in
`release/evidence/RESEARCH_LANES_ACCEPTANCE_V1_SARVAM105B_D01_SAFE_STOP_ESCALATED.json`
(SHA-256 `47c36f85d814a16bd606fa5389e2dd5daa757d9dcfddcd4d397d94eae17516bd`).
This was an external credential-state blocker, not a safety bypass; it is
retained as historical evidence and is superseded by the successful post-fix
matrix below.

### Research lanes post-fix promotion matrix — 2026-09-16

The complete post-fix Quick/Deep acceptance matrix has now run to its declared
terminal state using Sarvam `sarvam-105b` with Exa retrieval. All 24 declared
attempts completed within the hard ceilings (150 rupees, 90 minutes, and 12
iterations per attempt):

- **Overall:** **22 / 24 (91.67%)** — promotion threshold is at least 90%.
- **Quick Research:** **12 / 12 (100.0%)** — within the 3–8 source contract.
- **Deep Research:** **10 / 12 (83.33%)** — above the per-lane 80% floor, with
  validated 20–200-source reports on completed attempts.
- **False completions:** **0**.
- **Safety violations:** **0**; safety was measured on all 24 attempts.
- **Elapsed time:** **882.423 seconds (~14.7 minutes)**.
- **Provider-reported usage:** **249,657 billed tokens / $0.45**; the runner's
  hard rupee ceiling remained ₹150.

Two Deep attempts (`RL1-D01` repeat 1 and `RL1-D03` repeat 1) stopped
fail-closed as `needs_input` after the canonical tool-chain could not converge.
They produced no completion artifact and are not false completions. This is a
deliberate reliability property: provider/retrieval instability cannot be
silently converted into an unverified answer.

Immutable evidence is sealed at
`release/evidence/RESEARCH_LANES_ACCEPTANCE_V1_SARVAM105B_MATRIX_FINAL6.json`
(SHA-256
`ecb9f406693adfdf60c7a78c0c36561d95cec5ec308f9f17412033451456b599`). The
deterministic regression suite remains green at **874 passed, 1 skipped**, with
only the two pre-existing JWT key-length warnings.

Based on this matrix, `research_lanes` is promoted to
**`verified_live_web_and_deterministic`**. The promotion covers the declared
public-web Quick/Deep pack and does not imply authenticated-site, private
intranet, VM desktop, Linux, GAIA, or SWE-bench coverage. Deep tasks can still
request user input when the source floor or evidence convergence is genuinely
unreachable.

### Installed CLI/Desktop smoke and provider-profile migration — 2026-09-17

The current workspace was installed into the local Python environment and the
entry points were exercised directly:

- `smara --help`: passed.
- `smara-desktop --help`: passed.
- Desktop capability enumeration (`--skills`): passed and returned the bounded
  local capability registry.
- Persisted Sarvam profiles using retired `glm5.2`/`glm5.3` aliases now migrate
  in memory to `sarvam-105b` on the `/v2` endpoint before a CLI run. This keeps
  CLI resume state aligned with the verified provider model without touching the
  stored credential.
- The CLI prompt editor import path was repaired so the installed interactive
  entry point no longer fails with a missing `sys` binding.

The live provider portion of this smoke was intentionally fail-closed: the
temporary credential returned Sarvam HTTP 403 during the later Quick attempt,
and earlier provider responses also exhausted the bounded recovery path without
an unverified completion. These are non-completions, not promotion evidence;
the sealed 24-attempt matrix above remains the authoritative lane promotion
artifact.

### Installed lane smoke follow-up — 2026-09-17

Using a fresh temporary Sarvam credential and the configured Exa endpoint, the
installed entry point was exercised for all requested routing paths:

- **Auto:** correctly selected `quick` for the bounded RFC 9110 question, then
  returned `needs_input` after bounded recovery exhaustion. No answer or report
  artifact was claimed.
- **Explicit Quick:** reached and inspected authoritative Cargo evidence, then
  returned `budget_exhausted` without a completion artifact. This is a safe
  non-completion and exposes provider/tool-loop instability rather than a false
  completion.
- **Explicit Deep:** completed the PEP 621 report with 23 fetched sources and a
  verified Markdown report artifact at
  `scratch/installed-smoke-deep/.smara/sessions/c85d8c810e2048dfb175c9c206254b7e/artifacts/7a3810d96fc1f2636f2156058bcc1340ec6959a7cf8db082f32d7e69b85761f8.md`.
- **Resume:** `smara resume c85d8c810e2048dfb175c9c206254b7e --json` completed
  and preserved the same report artifact and provenance ledger.

The installed smoke therefore verifies routing, deep-report creation, resume,
and fail-closed behavior. The two bounded Quick non-completions remain a
provider/model stability issue and do not change the sealed promotion result
above (22/24, 91.67%, zero false completions, zero safety violations).

### Remaining-capability smoke — 2026-09-17

The remaining capability checks are sealed in
`release/evidence/PLATFORM_CAPABILITY_SMOKE_2026-09-17.json` (SHA-256
`5320fbbf0d4615dff36f0f8cac7b37aececfe81ffb866dd5c0bcb9f9a3a8d68b`):

- Docker Desktop `desktop-linux` built the Linux image and ran
  `scripts/linux_wheel_smoke.py` successfully (`LINUX_WHEEL_SMOKE_OK`). This
  verifies Linux container packaging; WSL host integration and a graphical VM
  are still not measured.
- `research_analyze` now exposes bounded deterministic forecasting, observational
  treatment/control summaries, and domain screening diagnostics. The methods
  are explicitly labelled non-causal/screening and retain the existing
  provenance and no-imputation rules.
- OpenAlex and Crossref keyless discovery both returned three live scholarly
  records. Every record is marked `discovery_only`; DOI/publisher retrieval and
  claim validation are still required before citation.
- Sarvam OCR now targets `/doc-ai/v1/job/digitise` and falls back only on a 404
  to the legacy `/v2/job/digitise` route. The supplied temporary credential
  returned 404 on both routes, so the live PDF OCR gate stopped safely with no
  artifact and remains provider-entitlement blocked. The deterministic OCR
  adapter suite remains green.
- The new Docker desktop transport passed `scripts/docker_desktop_smoke.py`
  (`DOCKER_DESKTOP_SMOKE_OK`): the guest was built from
  `docker/Dockerfile.desktop`, reset-attested through its container label and
  ID (image digest
  `sha256:166fb1eaf5e874b30429fe5437e532da150b7b4f11fc042a0eed44b7b86090c7`),
  observed at 1024x768, clicked, rejected a stale observation, then
  cancelled and cleaned up. This is a disposable Linux Xvfb/Openbox container
  guest—not a hypervisor VM and not native Windows desktop control.
- The Sarvam model catalogue's Parse entry is not a usable live endpoint for
  the supplied credential: `POST https://api.sarvam.ai/parse/parsepdf` returned
  HTTP 404 `not_found_error`, so no PDF parse artifact was claimed. Current
  Document AI OCR remains separately gated on `/doc-ai/v1/job/digitise` and
  remains provider-entitlement blocked for that credential.
- Gemma 4 is now a contract-tested, image-only fallback at
  `POST https://api.sarvam.ai/v2/chat/completions`. It is invoked only after a
  Document AI 404, requires a base64 image data URI, and labels results as
  `sarvam-gemma4/gemma4`. It never runs after authentication failures and does
  not claim PDF parsing, structured extraction, or Sarvam Vision entitlement.
- Multi-agent delegation remains opt-in (`SMARA_ENABLE_DELEGATION=true`) and
  learned-skill promotion remains quarantined/revocable; historical public
  acceptance does not silently enable either capability.

The complete deterministic regression suite after these changes is **881
passed, 1 skipped**, with the same two pre-existing JWT key-length warnings.
