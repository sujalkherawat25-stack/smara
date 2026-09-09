# Smara harness status

- Base and current implementation commit before this H0 change: `08b943374151ba0f4af762e23e2a5a665e916b79`.
- Package: H0 — trustworthy legacy CLI baseline.
- Scope: legacy ReAct CLI/agent, browser sidecar, interim goals, delegation admission, and GAIA adapter only.
- Historical artifacts preserved: the pre-existing `reports/swe_bench_results.pdf` modification and untracked `reports/swe_bench_results.json` were not staged or changed.

## H0 changes

- Added result/revision-based verification state to `SmaraAutonomousAgent`; failed execution and unverified edits no longer return `completed`, and the final iteration no longer suppresses tools.
- Tool schemas and dispatch now use an admitted profile; unknown profiles do not fall back to the full set. `delegate_task` and direct orchestrator delegation are fail-closed pending H6 policy enforcement.
- Serialized all legacy model-emitted tool batches, including write/read and patch/test sequences.
- Bound legacy file, patch, directory, search, browser-output, and terminal-cwd arguments to `workspace_root`.
- Removed synthetic screenshot success and fail unimplemented E2E browser actions.
- Made goal checkpoints replace atomically, reject `ok: false`, failed statuses and nonzero exits, and schedule a valid DAG by ready dependencies.
- Switched the GAIA adapter from `LocalAutonomousAgent` to the CLI's `SmaraAutonomousAgent`, gives every task a temporary workspace, includes all selected tasks in headline accuracy, and emits a runtime-contract hash/metadata record.

## Evidence

Commands run from `C:\Users\sujal\smara` with `PYTHONPATH=src`:

```text
.\.venv\Scripts\python.exe -m pytest -q tests/test_h0_harness_regressions.py tests/test_react_direct_execution.py tests/test_autonomous_tools_surgical.py tests/test_terminal_and_file_tools.py tests/test_task_planner.py tests/test_benchmark_fairness.py tests/test_subagent_orchestrator.py tests/test_subagent_worktree.py
# 38 passed, 1 skipped in 4.32s

.\.venv\Scripts\python.exe -m compileall -q src\smara benchmarks
# passed
```

The offline parity check is `test_gaia_default_runner_uses_cli_engine_and_fresh_workspace`: it substitutes only the model response and verifies the benchmark instantiates `SmaraAutonomousAgent` with profile `full`, 25 iterations, and a temporary task workspace. The generated benchmark contract records `engine=SmaraAutonomousAgent`, profile, model config, policy and tool-schema SHA-256.

The supplied `C:\Users\sujal\memoryos\artifacts\smara-audit-20260906\reproduce.py` was executed with the repaired checkout. It reached its final profile check, demonstrating that synthetic screenshot and path-escape code paths were exercised; its deliberate final write to the immutable historical `reproduction-results.json` was denied with `PermissionError`. No MemoryOS audit artifact was changed. Equivalent repository regressions cover each H0 behavior in `tests/test_h0_harness_regressions.py` and the focused existing tests above.

## Remaining gaps / H1 evidence needed

- The legacy Python/shell tools still run on the host; path validation is not an OS sandbox. H2 needs the real broker, capability grants and process isolation.
- Verification parses legacy text receipts; H1 needs typed evidence, durable journal records and exact test scope rather than a compatibility decoder.
- Sessions, budgets, cancellation and recovery are not durable/canonical. H1 must demonstrate an interrupted real edit/test run resuming with the same result/event schema through CLI and benchmark.
- Browser actions remain inspection-only beyond H0 fail-closed behavior. No GAIA, SWE-bench, OSWorld, WebArena, or paid evaluation was run or claimed.

## H1 completion evidence

- Added `smara.harness.SessionEngine`: SQLite-backed event/state journal, content-hashed JSON receipts, per-session artifacts, explicit `completed`, `tool_error`, and `cancelled` results, resume token, inspect, and cancellation.
- The headless `smara run --json [--prompt-file FILE]` path wraps the current CLI `SmaraAutonomousAgent` turn in that engine. `smara resume`, `smara cancel`, and `smara inspect --events --json` expose the same persisted session record.
- The GAIA adapter now wraps its fresh-workspace CLI-agent turn in the same `SessionEngine`; it reports the shared engine contract without using Desktop state.
- H1 regression: `tests/test_h1_session_engine.py::test_interrupted_edit_test_resumes_with_same_event_schema` writes a real file, interrupts before its test call, reopens the session, executes the test receipt, and asserts the persisted event schema/result.
- Latest gate command: `PYTHONPATH=src .\\.venv\\Scripts\\python.exe -m pytest -q tests/test_h0_harness_regressions.py tests/test_react_direct_execution.py tests/test_autonomous_tools_surgical.py tests/test_terminal_and_file_tools.py tests/test_task_planner.py tests/test_benchmark_fairness.py tests/test_subagent_orchestrator.py tests/test_subagent_worktree.py tests/test_h1_session_engine.py` → **39 passed, 1 skipped in 4.41s**. `compileall` also passed.

## H1/H2 repair and completion evidence (2026-09-06)

The earlier H1 commit was not accepted as-is: its single-file mock had no schema
validation, writer exclusion, aggregate budget enforcement, truthful mutation
recovery, revision-bound evidence, or process supervision, and the CLI `resume`
command only printed state. The repaired `SessionEngine` is now version
`h2-local-2` and preserves the old constructor/run API for compatibility.

- Added the audit's explicit `RunRequest`, `RunResult`, `ToolCall`, `ToolResult`,
  `Evidence`, and `RunEvent` types and stable machine-readable run statuses.
- SQLite journals use WAL, `synchronous=FULL`, schema versions/migrations,
  append-only sequenced events, persisted calls and evidence, and a
  cross-process single-writer lock. Admitted mutations without a journaled
  result resume as `needs_input`/uncertain and are never blindly replayed.
- Aggregate wall/tool-call budgets are persisted and exhausted runs retain a
  resume token and pending calls. Content-addressed, atomically written receipt
  artifacts retain raw tool results outside model context.
- `ToolBroker` is the single admission point for the shipped coding mutation
  and process primitives: strict schemas, explicit grants/workspace IDs,
  canonical root checks (including Windows junctions), scoped environments,
  hash-guarded atomic writes/patches, explicit cwd, and serialized mutations.
- Constrained mode denies arbitrary host execution unless an external sandbox
  is supplied. CLI local coding is explicitly unrestricted-local. The legacy
  CLI's read/write/patch/terminal/Python routes now enter the broker while
  retaining their public tool names and output compatibility.
- Persistent process start/poll/stdin/cancel is brokered. On Windows, processes
  are assigned to kill-on-close Job Objects, so cancellation terminates the
  process tree; the delayed-canary regression proves no post-cancel mutation.
- Verification evidence records scope and exact workspace revision. Nonzero
  checks fail, later edits stale prior evidence, and syntax-only evidence cannot
  certify a changed workspace as focused/full-test verified.
- Provider retry policy now fails 400/401/403/404/409/422 immediately and honors
  bounded `Retry-After` for 429/transient failures. CLI `doctor --json`, real
  resume, empty-prompt rejection, and named budget-profile validation are live.

Latest evidence from `C:\Users\sujal\smara` with `PYTHONPATH=src`:

```text
python -m pytest -q
# 402 passed, 1 skipped in 45.96s

# tests/test_h2_execution_broker.py repeated 10 times
# 140/140 passed; each run 14 passed (about 5.3s)

python -m pytest -q <H0/H1/H2 focused matrix>
# 48 passed, 2 skipped in 7.27s (before two provider and two final H2 cases were added)

python -m compileall -q src\smara benchmarks
# passed

python -m smara.cli doctor --json
# ok=true; engine=h2-local-2; provider/browser/desktop availability reported separately
```

The two repository skips are pre-existing/conditional. The H2 junction escape
case itself runs and passes on this Windows host (it falls back from symlink to
an NTFS junction when Developer Mode is unavailable).

## Honest boundary after H2

H0-H2 now establish the durable single-agent coding/terminal foundation and
the deterministic reliability behavior relevant to R01-R24. This does not
claim the later H3 context/100-action gates, H4 research, H5 browser/desktop,
H6 delegation, public benchmark scores, Linux matrix, or an installed external
container sandbox. Constrained terminal mode therefore remains fail-closed.

## H3-H7 implementation update (2026-09-07)

The actual autonomous loop now uses incremental SessionEngine model/tool
steps, conservative full-request context packing, hash-verified continuation
checkpoints, durable progress/stall recovery, and revision-bound verifier
evidence. Research emits a persisted question graph and typed evidence index.
A managed real Chromium backend, fail-closed isolated-VM desktop contract,
bounded process delegation core, quarantined skill candidates, and an
authenticated/idempotent local command transport are implemented.

Latest full source gate: **497 passed, 1 skipped, 2 existing JWT-key warnings
in 97.31s**. The strengthened managed-browser B01-B20 suite passed three
clean-context repetitions. The desktop Rust bridge also passed a clean-target
`cargo check --locked`. The final Windows wheel SHA-256 is
`d712bea624a2a352e8fe4e9adf48c8fa80735b16039101592bf7bd7346206bbb`;
it installed and completed a canonical engine task from a Unicode/space path.

The detailed evidence and honest blocked boundary are in
`release/H3_H7_GATE_REPORT.md`. Real disposable-VM D01-D20 execution and Linux
installation are blocked by missing environments. Official external benchmark
scores and the delegation ablation remain unmeasured, so none are promoted.

## W1 canonical research update (2026-09-09)

The revised Windows-readiness plan's W1 slice now passes its deterministic
agent-loop gate. Research-profile runs use session-scoped plan, search, fetch,
inspect, resolve and validate actions; retain original and extracted artifacts;
and reject unsupported numbers, negation, dates, entities, causal claims,
snippets, stale state and immediate-final bypasses. The versioned 17-case
manifest is `tests/evals/windows_research/manifest.json`. The full suite result
is **518 passed, 1 skipped, 2 existing JWT-key warnings in 120.70 seconds**.
Detailed scope and limitations are in `release/W1_RESEARCH_GATE_REPORT.md`.
Live-model/live-web quality remains unmeasured and OCR is explicitly
unavailable on this installation, so neither is promoted.

## W2 canonical browser update (2026-09-09)

The stateful Chromium backend is now part of canonical autonomous sessions via
typed, capability-checked tools. Observations and screenshots are persisted,
browser handles are checkpointed but invalidated after backend loss, downloads
remain workspace-scoped, and session cancellation closes owned browser work.
All five required agent-loop journeys passed three clean-context repetitions
(**15 passed in 31.05 seconds**). See `release/W2_BROWSER_GATE_REPORT.md`.
This does not promote live-site quality, authenticated profiles, desktop/VM
control, or overall Windows readiness; W3-W5 remain active.

## W3 dependable execution update (2026-09-09)

Canonical sessions now expose typed durable process start/poll/stdin/cancel,
bounded cursor logs, explicit non-reattachable restart state, concurrent-edit
hash guards, content-aware CSV/JSON/report validation, and tested three-stage
compaction recovery. The eight-journey gate plus H2/context regressions passed
**29 tests in 9.24 seconds**. The 30-minute soak and provider-driven long task
remain separate release measurements; see `release/W3_EXECUTION_GATE_REPORT.md`.
The W3 full regression result is **541 passed, 1 skipped, 2 existing JWT-key
warnings in 145.47 seconds**.

## W4 Windows package update (2026-09-09)

Functional capability discovery, explicit research/local profiles, and canonical
application envelope parity are implemented. The final wheel was installed outside
the source tree and passed doctor plus research/browser/file/terminal smokes from a
Unicode/space workspace without `PYTHONPATH`. See
`release/W4_WINDOWS_PACKAGE_REPORT.md`. Provider-backed quality, OCR, Linux and VM
desktop remain unmeasured/unavailable and are not promoted.
