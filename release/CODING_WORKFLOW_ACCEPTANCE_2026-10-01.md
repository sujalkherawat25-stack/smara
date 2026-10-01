# Coding workflow acceptance — 2026-10-01

## Outcome and limits

Ran real Sarvam GLM 5.3 through Smara's canonical CLI command handler, using isolated synthetic workspaces. Independent contract probes stayed outside each agent workspace. No fixed model responses, test bypasses, or relaxed assertions were used. Original visible tests were preserved throughout.

The initial pack passed 9/10 independent checks and exposed one false completion. After fixes, the full rerun passed independent checks for all ten tasks, but only eight reported `completed`: two exhausted deliberately small budgets after producing correct code. Fresh bounded reruns of those two tasks completed successfully. This is **eight clean completed runs plus two budget follow-ups**, not a claim of ten first-pass autonomous successes.

| Task | Independent checks in full rerun | Reported result | Model/tool calls | Time (s) | Failed tool receipts |
|---|---|---|---|---|---|
| Port/range parser | Passed | completed | 6 / 7 | 17.79 | 1 |
| CSV quoting/newlines | Passed | completed | 7 / 7 | 21.54 | 1 |
| Boolean configuration | Passed | completed | 4 / 5 | 10.02 | 0 |
| Path traversal protection | Passed | budget_exhausted | 7 / 8 | 49.78 | 1 |
| Mutable default isolation | Passed | completed | 4 / 5 | 14.76 | 0 |
| Cross-module refactor | Passed | completed | 4 / 8 | 9.64 | 0 |
| CLI errors/exit codes | Passed | completed | 4 / 5 | 14.87 | 0 |
| Remove unnecessary dependency | Passed | completed | 5 / 7 | 12.37 | 0 |
| Add regression tests | Passed | completed | 4 / 4 | 8.03 | 0 |
| Missing policy → fresh-process resume | Passed after policy supplied | budget_exhausted | 12 / 14 | 21.33 | 0 |

Failed receipts include intentionally reproduced test failures; they are not all infrastructure faults. Path-safety follow-up: completed, 66.08 seconds, 10 model/10 tool calls. Policy/resume follow-up: initial `needs_input`, then completed in a new OS process, 24.99 seconds total, 10 model/11 tool calls. The evaluator supplied policy.txt between phases; this is an intentional two-phase recovery scenario, not an unassisted first-pass task.

Full rerun ceiling per task: 120 seconds, 32 tools, 12 model calls, 120,000 token accounting, $0.15 harness accounting. Follow-ups and maintained runner defaults: 180 seconds, 40 tools, 16 model calls, 240,000 tokens, $0.20. These are conservative internal limits, **not verified provider bills**. Budgets and original failure evidence were retained; no run was relabeled to hide exhaustion.

## Memory off / local / Syntarus

Three fictional policies covered retry/backoff, cache expiry, and pagination. Each arm started with the same code and visible tests. Facts were deliberately absent from the no-memory arm; requesting clarification there is appropriate. Local recall used the production local note path; the Syntarus arm used actual retrieved context, not a hand-authored substitute. Only fictional policies were uploaded under an isolated test identity. The supplied key was transient and was not saved in source or reports.

| Policy | Local: correct / model-tool time | Syntarus: correct / model-tool time | Cloud retrieval | Initial processing + retrieval |
|---|---|---|---|---|
| Retry/backoff | Yes / 21.42 s | Yes / 15.81 s | 546.76 ms | 2.98 s |
| Cache expiry | Yes / 15.54 s | Yes / 10.64 s | 706.45 ms | 2.90 s |
| Pagination | Yes / 12.83 s | Yes / 11.94 s | 666.88 ms | 2.93 s |

Both facts-enabled arms passed 3/3 independent checks. That demonstrates continuity from saved facts, **not a Syntarus-specific accuracy advantage**, statistical reliability, semantic superiority, or production readiness. Cloud timings above must be added to execution time; no billing comparison was verified. The tiny pack does not test contradictory/stale memory or broad retrieval noise.

Initial no-memory runs exposed two more wording variants falsely marked completed. After completion guards and missing-policy guidance, the rerun produced two honest `needs_input` outcomes and one `budget_exhausted` outcome, with zero false completions. The retry task spent its budget looking through internal runtime journals for unavailable facts; that remaining inefficiency is recorded, not hidden. Automatic private-code recall remains local-only.

## Fixes informed by the runs

- Explicit `needs_input`, missing policy, blocked implementation, and admitted unimplemented/stub answers cannot be reported as completed by either runtime's shared completion guard.
- Resuming an input-blocked session adds a current-workspace recheck instead of relying on stale missing-file observations.
- Waiting for input pauses wall-time accounting; resuming preserves used tokens, model/tool calls, and cost limits.
- Desktop exposes the active coding folder, Coding mode, and direct Review changes / Verify tests shortcuts.
- Git/test bridge commands use the selected approved folder rather than preferring the Smara source checkout. Invalid explicit coding folders fail instead of silently falling back.
- Saving workspace settings no longer silently switches approval mode to automatic. Folder changes begin a new conversation and are blocked during a running turn.
- Starter cards describe normal coding/research tasks rather than implying official benchmark certification or committing as a default review action.
- Installed-app QA caught Git conflict detection scanning the entire selected home folder. It now reads only Git's unmerged index entries (including non-text conflicts), returns the dictionary shape expected by Desktop, and does not scan non-repositories. Porcelain status preserves its leading status column, fixing misclassified first-file changes. Non-repositories show a choose-project message rather than a misleading clean-tree result.

The completion guard is conservative text detection, not a semantic proof that every requested requirement was implemented. Independent checks and diff review are still necessary.

## Reproduction and evidence

Validation: 181 targeted Python tests passed in 32.34 seconds, including four real local-Git regressions; 19 Rust tests passed. TypeScript/Vite, PyInstaller and the Tauri NSIS build passed. The actual frozen executor passed checks without a checkout or Python on PATH. Installation preserved existing user settings/history and a backup of the prior installation remains in `build/desktop-install-backup-coding-20261001`.

Native installed-app checks covered the new Chat workflow bar, starter cards, Coding selector, composer scrollbar, Git review loading, and Tests-panel navigation. Read-only probes of the installed bridge returned in roughly four to five seconds for both the existing home-folder selection and this repository. No whole-home-directory tests, in-app permission changes, commits, or model prompts were submitted during native QA. The live-model coding pack was run through CLI, not through UI clicks. These are focused checks, not a claim that every Desktop view is bug-free.

- Maintained runners: `scripts/run_coding_acceptance.py` and `scripts/run_coding_memory_comparison.py`.
- Initial pack: `build/coding-ten-20261001/`.
- Full post-fix pack: `build/coding-ten-final-20261001/`.
- Two budget follow-ups: `build/coding-ten-budget-followup-20261001/`.
- Authenticated three-arm comparison: `build/coding-three-arm-auth-20261001/`.
- Post-fix no-memory check: `build/coding-three-arm-off-guided-20261001/`.
- Intermediate failed attempts, including the initial stdin credential transport failure and an evaluator workspace-argument mistake, remain under build; they were not counted as Smara successes or cloud recall failures.

Example: `.venv/Scripts/python.exe scripts/run_coding_acceptance.py --live --output build/new-coding-run`. Use a new evidence directory each time. Memory comparison requires a valid transient `SYNTARUS_API_KEY`; it uploads only the synthetic policies defined in the runner.

Next: a small real-repository coding pack with human-reviewed diffs, and a bounded stop for missing-policy searches through internal journals. Do not expand automatic cloud scope on this experiment alone.
