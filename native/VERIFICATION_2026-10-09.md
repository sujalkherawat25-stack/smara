# Source-native migration — verification on 2026-10-09

## Bottom line

The development candidate runs Smara's own copy of the Codex Rust harness with
Sarvam, not a second Python agent loop or a separately installed Codex. Actual
coding, parallel execution, saved sessions, research readers and bounded
read-only scheduling have narrow acceptance evidence. The installed Desktop
has **not** been replaced, and this is **not** a published production release.

Branch: `codex/source-runtime-migration`. Upstream pin:
`14c8b7771ab2b617a131f5d8e55e98d18e56ed09`. The original Apache-2.0 LICENSE,
NOTICE and provenance remain. The pre-migration branch remains recoverable.
No private code/documents or Syntarus memory were uploaded by these probes.

## Current regression/build gates

- Latest full Smara Python suite: **1,283 passed, two skipped**, 472.23 seconds.
  Evidence: `build/native-v019-final-full-2026-10-09.xml`.
  Docker Linux-engine checks now execute instead of skipping. The two remaining
  skips require unavailable Windows symbolic-link privileges, not Docker.
  Separate real Windows directory-junction checks both pass; they complement,
  rather than replace, the skipped symbolic-link checks.
  Earlier reports remain: the original 1,254/26 run, and the first install-gate
  run (1,266 passed, three failed, 11 skipped during engine startup/build pressure).
  Its Docker/process failures passed on stable-engine rechecks. The intermittent
  Windows unauthorized-POST reset was fixed by bounded body draining and explicit
  closed JSON error responses; authentication remains mandatory. A regression
  exercises 20 unauthenticated plus 20 wrong-route requests without inference.
  UTF-8/LF protocol checks were then added. Their first full run exposed Windows
  CRLF translation (1,282 passed, one failed, two skipped); explicit LF framing
  fixed it. The failed report remains `build/native-utf8-install-full-2026-10-09.xml`.
  The final focused UTF-8/auth/readers gate is **43 passed, one symlink skip**.
- Focused native Python checks: **52 passed, one skipped**, 6.39 seconds.
  Evidence: `build/native-followup-focused-2026-10-09.xml`.
- Latest frontend regression suite: **22 passed**, no failures.
- Latest `tsc`/Vite production build passed, including native MCP confirmation,
  the visible v0.1.9 label and accurate adapter limits:
  `dist/assets/index-jbUIQnWs.js` and `index-DhudrTZA.css`.
- Tauri `cargo check` and actual debug `cargo build` passed after the shutdown
  ownership fix. The successful debug build took 16.80 seconds.
- Copied Rust model-info tests: nine passed in the earlier context-metadata
  check. This is **not** the full upstream Rust suite.
- `git diff --check` passed for tracked edits. The import and new migration
  files are ready for a local source commit; no push, installer cutover or public
  release has occurred at this point. Package/install results must be added below.

## v0.1.9 package/install follow-up

The user explicitly authorized remaining tests, push and a local candidate
install. Package metadata is aligned at **0.1.9**, distinguishing the migration
from installed 0.1.8. Native upstream development `--version` is not fabricated.

All **9,064 public imported source files** are staged, including the Rust
`secrets` crate accidentally excluded by the repository's generic ignore rule
and three upstream editor files. No build targets, binaries, credentials, user
history, personal CSVs or unrelated reports are staged. Original source notices
are preserved. This is still not the complete upstream Rust test suite.

The first release build failed with Windows metadata-mapping errors under high
compiler concurrency. Build jobs are now bounded (default two); release/package
and installed-path gates are still running. No debug binary will be represented
as a verified release build. The maintained installer verifies matching native
payload hashes, backs up old install-directory data, checks that preferences and
protected credentials are unchanged, and installs a versioned portable CLI.
Actual install/push outcomes must be recorded after they happen.
Only the validated generated `vendor/codex/codex-rs/target/debug/incremental`
cache (23,072,420,278 bytes) was removed for build disk pressure. Cargo can
regenerate it; source, debug binaries, release artifacts and user/Docker data
were retained.

## Actual Desktop checks

The real GUI used isolated test state/workspaces, not the user's old history.
Only the approved synthetic arithmetic prompt was sent to the configured
Sarvam GLM endpoint. Research readers and memory were off for this check.

- Real Desktop Send streamed a correct answer: `17 × 23 = 391`. The model did
  **not** follow the exact “result only” formatting instruction; arithmetic
  and transport passed, not that formatting requirement.
- Disconnect, reconnect and selecting the saved conversation restored the
  user and completed assistant messages. Native ledger has one `task_started`
  and one `task_complete`; resuming did not start another native turn.
  Thread: `01a11cf8-0274-7ba2-a91f-32b39cdf9fc0`, under
  `build/native-desktop-candidate/data/native-runtime/sessions/2026/10/09`.
  HTTP retry counts/billing were not measured by this GUI check.
- Actual GUI Stop was exercised with a **labelled local stream fixture**, not
  a pretend model answer. The final rebuilt candidate closed its only stream,
  recorded native `turn_aborted` / `interrupted`, and did not record completion.
- Reconnecting/resuming the interrupted conversation did not send another
  request: one local request, zero completed streams, one closed stream.
  Maintained report: `build/native-desktop-offline/stop-report.json`.
- Interrupted streamed text is not necessarily retained by native rollouts.
  The UI now says that explicitly; it does not fabricate a completed answer.
  The final GUI recheck displayed the new recovery warning.

GUI controls were inspected/operated through Computer Use. The Stop report
independently verifies transport/native ledgers, **not** which GUI button was
clicked. Actual Allow/Deny/security-prompt clicks still require human testing;
they were not automated or silently granted.

## Recovery and permission fixes in this follow-up

- Readable conversation labels; only labels are shortened, not stored prompts.
- Send blocked while a saved thread is loading; late stale loads ignored.
- Lifecycle single-flight guards and visible disconnect cleanup state.
- Worker answers keyed by request ID plus question ID, preventing cross-use.
- Failed shutdown retains frontend generation **and** backend process ownership
  for cleanup retry. Legacy settings cannot open after unconfirmed shutdown.
- Initialization and cleanup failures both remain visible, not unhandled.
- Native MCP action-confirmation support added for the exact empty-form
  `codex_approval_kind = mcp_tool_call` shape. It shows the original parameters
  and grants only once (`_meta: null`), without copying persistence offers.
  OAuth/device verification, URL authorization and data-entry forms still fail
  closed; this is not generic MCP login support.

The actual copied Rust engine emitted three local-clock MCP confirmations. The
maintained Desktop response helper declined the first, accepted the second and
declined the third. A fresh confirmation after acceptance proved it was not
remembered. Native denial returned no clock result, acceptance returned a current
UTC/local clock, and the actual result reached model history. Six scripted local provider
requests, zero paid requests, no mocked MCP results. Evidence:
`build/native-mcp-approval-single-use-2026-10-09.json`.
This is native protocol/helper acceptance, **not** human GUI approval acceptance.
The first checks failed because the test searched escaped wire JSON instead of
parsing structured content. Both failed reports remain; the final check validates
the actual timestamp and exact history replay, rather than weakening the test.

## Other native capability evidence (earlier in this continuation)

| Capability | Evidence | Important limit |
| --- | --- | --- |
| Two real read-only delegated workers | `build/native-parallel-final-2026-10-08.json`: nine real requests, peak three streams | Not isolated coding worktrees |
| Two real coding sessions in native-managed worktrees | `build/native-worktrees-final-2026-10-09.json`: ten real requests, peak two streams; reproduced failures, unchanged tests, combined eight tests passed | Independent CLI sessions; **parent-directed worktree delegation not tested/implemented** |
| Clock, local memory, public RFC fetch and compaction recall | `build/native-readers-rebuilt-2026-10-09.json`: four real requests, clock/memory/fetch, recall after manual compaction | Small synthetic/public task, not million-token stress/deep-research quality certification |
| Stop/crash with actual delayed terminal child | `build/native-recovery-final-2026-10-09.json`: delayed write prevented, fresh process resume, no replay/false completion | Scripted provider; not Windows reboot or every sandbox escape |
| Native bounded read-only schedule | `build/native-schedule-2026-10-09.json`: real arithmetic task, one request, disabled/removed afterward | No OS auto-start or company-write jobs left enabled |

The worktree run's original report records a Windows cleanup handle error.
The exact owned synthetic fixture was subsequently cleaned; the report was not
rewritten to erase that failure. Earlier reader/worker failures remain separate
from their final reruns.

The registered development `smara-desktop --native-app-server` path was also
rechecked after the acceptance-helper change: actual native patch approval,
denied command not executed, native tool results/history, resume/list/graceful
shutdown passed with four scripted provider requests. This is not paid-model
quality or general OS sandbox certification.

## Packaging candidates

- Rebuilt portable wrapper: `build/native-cli-candidate/smara.exe`,
  **22,271,545 bytes**. Adjacent debug native/helper binaries add roughly 412 MB;
  **the entire package is not 22 MB**. Release-profile slimming remains.
- Native offline help now documents readers, schedules, provenance and explicit
  legacy access without reading model profiles or triggering inference.
- Rebuilt frozen CLI passed source ownership, provenance, Unicode memory,
  stdio readers, public RFC fetch, schedule loading and no-legacy-loop checks:
  `build/native-packaged-cli-live-2026-10-09.json`. Zero paid provider requests.
- Rebuilt wheel: `build/native-wheel/smara-0.1.8-py3-none-any.whl`, 616,047 bytes,
  SHA-256 `0572a908fc2b0c9fa17c97367b1b3f071c57abc47c5f1059cdd1953866d01f4b`.
  Separate-target installation smoke passed: installed module origin (not the
  editable checkout), provenance, offline native help and local MCP clock.
  Evidence: `build/native-wheel-installed-2026-10-09.json`.
- That wheel smoke **reuses existing interpreter dependencies** and explicitly
  selects Smara's own native binary. The wheel does **not** bundle Rust binaries
  or prove a fresh machine can install all dependencies offline.
- Desktop production frontend and debug backend rebuilt; no release-profile
  NSIS candidate, install, commit, tag, push or public release was performed.

## Remaining sequence — do not call the migration finished

1. Human Desktop approval/denial check, including worker prompts and failed
   process/network recovery. Broader Windows ACL, junction, network and process
   escape gates remain; protected 0700 parent-folder compatibility is unresolved.
2. Parent-directed coding workers that actually own isolated worktree execution
   contexts, inherit restricted permissions, survive recovery and return reviewed
   patches. Do not replace that with two independent CLI invocations and call
   it an orchestrated coding swarm.
3. Native browser/computer-use and approved remote-memory adapters. Public
   research/local-memory primitives are connected; personal browser sessions,
   screenshots/computer actions, Syntarus and the old research review UI are not.
   Keep browser/tool permissions on the native authority, not a parallel planner.
4. Broader real-model research/coding, long-history/output, provider capability
   and maintained quality evaluations. Configured context metadata is not proof
   that million-token workloads succeeded.
5. Release-profile binaries plus Windows helpers, Desktop installer and install
   acceptance, then authorized commit/push/release. Existing history/credentials
   must stay intact. Company operations require separately approved scopes and
   outcome/idempotency gates; do not enable them as part of this cutover.

## Reproduce bounded/offline gates

```powershell
npm --prefix apps/desktop test
npm --prefix apps/desktop run build
python -m scripts.run_native_protocol_acceptance --tool-roundtrip --console-entrypoint
python -m scripts.run_native_mcp_approval_acceptance --report build/native-mcp-approval-recheck.json
python -m scripts.run_native_desktop_candidate --offline-stream
python -m scripts.run_packaged_cli_acceptance --live-fetch
python -m scripts.run_native_wheel_acceptance
```

Use the repository virtualenv. GUI checks need the frontend dev server at port
1420; public-fetch acceptance uses the public internet but no paid model. Real
provider runners require explicit scope/budget approval. Failed original reports
should remain visible alongside any recheck.
