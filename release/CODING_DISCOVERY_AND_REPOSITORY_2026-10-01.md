# Coding discovery and repository evaluation — 2026-10-01

## What changed

- Ordinary project listings, text searches (ripgrep and fallback), and initial workspace context exclude generated `.smara` state. Saved local notes are still retrieved through the existing local memory path. Explicit file/diagnostic access remains possible; this is discovery hygiene, not an access-control boundary.
- In Coding/SWE/worker-coding profiles, four consecutive empty file/directory/text lookups stop with `needs_input`, list the unsuccessful lookups, and preserve a session event. Successful discovery or successful mutation/process execution resets the counter. Resuming starts a fresh discovery attempt. This does not claim every file was searched or cover arbitrary shell/Python loops.
- Successful source reads no longer become failed receipts merely because the retrieved code contains “error”, “failed”, or “exception”. Actual read-tool error envelopes still fail.
- Provider retry/socket timeout allowance now uses the session's remaining wall budget rather than a fresh 180-second retry allowance on each request. Socket timeouts are not a hard OS-level whole-response deadline.
- Git conflict resolution canonicalizes paths before reading/writing and rejects outside paths, traversal and invalid/non-file inputs. The reviewed model patch was simplified before implementation; this remains a path check, not protection against a hostile concurrent filesystem race.
- Coding keeps complete observations up to 32,000 characters and uses the protocol-safe context packer before falling back to emergency excerpts. The old compactor removed source middles even when the conversation fit its limit. Distinct review observations no longer trigger the repeated-read guard; twelve repeated observations still stop without claiming completion.
- Coding uses a concise, scope-specific prompt instead of the general research/benchmark prompt. Sarvam GLM coding requests default to documented `reasoning_effort=low`; `SMARA_CODING_REASONING_EFFORT=high` or `max` remains available. Research reasoning settings are unchanged. Reference: https://docs.sarvam.ai/api/getting-started/models/openweight/glm-5-3.

## Live missing-policy check

Three fictional no-memory policies ran through the real configured Sarvam GLM CLI. They all correctly requested missing facts, rather than inventing policy or claiming completion:

| Task | Result | Seconds | False completion |
|---|---|---:|---|
| Retry/backoff | needs_input | 20.76 | No |
| Cache expiry | needs_input | 21.37 | No |
| Pagination | needs_input | 21.66 | No |

There were no observed session-database/artifact mining calls in these logs. The previous retry case exhausted its budget; this small rerun is evidence of better clarification behavior, not a statistical latency comparison. The hard four-empty-lookup stop is also tested with deterministic offline model fixtures. The live tasks were already running before the later receipt/timeout fixes; those later fixes are locally verified, not live-revalidated here.

The first sandboxed attempt failed with Windows socket permission errors, made no successful model calls, and is retained separately under `build/coding-discovery-off-20261001`. Scored live evidence is `build/coding-discovery-off-live-20261001`. No Syntarus upload or cloud-private-code recall was enabled.

## Real repository suite — failures retained

Three full committed Smara snapshots were extracted with `git archive`, not fabricated toy replacements. The first two reproduce historical regressions at `56ebe0c`; the third targets a then-current path-safety bug at `ae88e61`. The evaluated agent could read project files and add tests, but external probes stayed outside its workspace and existing test hashes were checked. The user explicitly approved sending inspected repository code to the configured Sarvam GLM endpoint after the initial permission-review rejection; that rejection was not bypassed.

| Task | Runtime result | Independent correctness | Seconds | Model/tool calls |
|---|---|---|---:|---|
| Historical Git status/conflict review | budget_exhausted | Failed; no original files changed | 74.48 | 16 / 18 |
| Historical completion classification | tool_error (provider read timeout) | Failed; no original files changed | 212.09 | 8 / 9 |
| Current conflict-path safety | budget_exhausted | Passed tested cases; symlink creation unavailable | 95.87 | 16 / 20 |

**Zero fully completed tasks, one independent-code pass, zero false completions.** All original tests remained unchanged. Hash comparison against the original archives found only `src/smara/git_agent.py` changed in the third task. Its added path-safety tests and target diff were reviewed. The independently tested traversal/absolute-path fix was implemented in the working source with a smaller path check and focused regressions; it is not relabeled as an autonomous completed run. Symlink tests were skipped on this Windows host because creating links was unavailable.

Per-task limits were 180 seconds, 40 tools, 16 model calls, 240,000 internal token accounting and $0.20 internal cost accounting. These are not verified provider bills. The completion task exceeded the nominal wall limit during provider I/O, motivating the remaining-wall timeout fix above. No failed task was rerun with a larger budget to manufacture a passing score.

Evidence: `build/repository-coding-20261001/results.json`, target `.diff` files, CLI logs and preserved snapshots. Maintained runner: `scripts/run_repository_coding_acceptance.py --live --output build/new-repository-run`. Future runs also record changed original files in their result rows. The repository contains evaluator-owned fixtures; this is not an official SWE-bench score or a contamination-free unseen benchmark.

### Guard follow-up

After adding an eight-read prompt and twelve-read stop, the same tasks ran again on fresh archived snapshots in `build/repository-coding-followup-20261001/`:

| Task | Result | Correct | Seconds | Model/tool calls | Original files changed |
|---|---|---:|---:|---:|---:|
| Historical Git status/conflict review | needs_input at the read-only stop | No | 28.78 | 10 / 12 | None |
| Historical completion classification | provider read timeout | No | 180.63 | 11 / 14 | None |
| Conflict-path safety | needs_input at the read-only stop | No | 24.31 | 9 / 12 | None |

This removed the earlier long read loop and retained all three independent failures. The injected action prompt did not persuade the model to implement the fixes. The guard succeeds at stopping unproductive work; coding effectiveness remains unproven on this pack. Across both packs: no task reached `completed`, the first pack's safety patch passed only while `budget_exhausted`, and the guard pack made no source edits.

## v0.1.4 release verification

Final targeted regression verification: **132 passed, one Windows symlink-creation skip**. Coverage includes coding context preservation, repeated versus distinct reads, missing-policy stops, source-read receipts, wall-budget timeout, path containment, completion receipts, local-only memory, research retry behavior, Desktop and durable handoff contracts. Frontend typecheck/build and Cargo check passed. The actual frozen v0.1.4 executor passed the checkout-independent bundle probe, including path containment and distinct review reads.

Fresh full-snapshot live evaluation after the context and coding-prompt fixes (`build/repository-coding-release-low-20261001`):

| Task | Runtime status | Independent correctness | Existing tests preserved | Seconds |
|---|---|---|---|---:|
| Git status/conflict review | budget_exhausted | Passed | Yes | 69.30 |
| Completion classifier | tool_error | Failed | No | 112.67 |
| Conflict path safety | tool_error | Passed tested cases | Yes | 52.05 |

**Two independent-code passes, zero fully completed tasks, zero false completions.** The completion task modified an existing test despite instructions to preserve it; the evaluator rejected it. No generated evaluation patches were copied into the release. Failure evidence remains intact. A context-only intermediate rerun also failed to complete all tasks; results are retained under `build/repository-coding-release-20261001`. Sandboxed socket-denied attempts are separate from successful provider calls.

The fixes remove verified implementation defects but do not establish reliable autonomous end-to-end coding on this pack. Next priority is budget-efficient inspect → patch → verification completion and stronger preservation of evaluator-owned tests. Do not market these results as a fully passing coding suite.

Release identifiers are aligned at 0.1.4 across Python, npm and Tauri. Local CLI is installed editable against this checkout, preserving source/CLI parity. The completed NSIS installer was installed locally; the installed executor hash matches the tested build and its standalone probe passed. Native visual checks confirmed v0.1.4, runtime readiness, preserved recent tasks, the Chat composer and working Git review navigation/non-repository guidance. No credentials, privacy settings or approved workspace roots were changed. Prior application binaries are backed up under `build/desktop-install-backup-0.1.4-20261001`. Remote commit/tag and installer publication are verified separately during release.
