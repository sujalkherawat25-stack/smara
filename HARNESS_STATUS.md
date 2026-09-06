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
