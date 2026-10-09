# Source-native migration verification — 2026-10-08

This is a development migration candidate, not a production release or a claim
of full Codex Desktop/cloud equivalence. The public source is copied into Smara,
not loaded from an installed Codex CLI. The proprietary Desktop UI and hosted
services are not part of the public source import.

## Source and ownership boundary

- Upstream revision: `14c8b7771ab2b617a131f5d8e55e98d18e56ed09`.
- All 9,064 tracked upstream files were imported. A SHA-256 comparison found no
  missing files or unexpected changes before the final documented fork changes.
- The local source changes are recorded in `UPSTREAM.json`: Smara CLI target and
  branding/product-command guards, separate default runtime home, and native
  freeform patch exposure for foreign models. License/NOTICE remain intact.
- Native tools, reasoning/action loop, rollouts, permissions, process management,
  sandbox implementations, MCP and multi-agent orchestration come from Rust.
  Python only launches the runtime and translates the model wire protocol.
- Current branch: `codex/source-runtime-migration`. Recovery branch:
  `codex/pre-source-migration-20261008`, at pre-migration commit `343ee23`.
- Personal files, keys, conversations, databases and unrelated untracked work
  were not deleted. Legacy code is explicit migration access, not an automatic
  agent fallback. Legacy research schedules are saved but no longer auto-start
  through the Desktop candidate while runtime ownership changes.

## Passed checks

- Full native CLI/core compile check with the copied Cargo lockfile.
- Debug native executable plus both Windows sandbox helpers built successfully.
  These debug binaries are not release artifacts.
- Desktop TypeScript/Vite build and native Tauri `cargo check`.
- 9 frontend regression tests, including stale generations, early process exit,
  denied approval responses and failed connection ownership.
- 83 focused Python regressions: native adapter, Desktop client, legacy harness
  routing, console-entry-point ownership, fail-closed model selection, bounded
  acceptance-runner mechanics and session protocol. Report:
  `build/native-driving-seat-audit-final.xml` (31.05 seconds), including the
  worker-probe concurrent-stream lifetime check.
- 4 copied Rust home-directory tests and 8 model-info tests.
- Actual native protocol and tools, with a clearly scripted local provider:
  initialize; safe thread configuration; native patch after explicit approval;
  actual tool-result replay; streamed completion; denied command not executed;
  resume; list; graceful shutdown. Four scripted provider requests. No paid
  inference was involved and no native tool results were mocked. Repeated
  successfully through the registered `smara-desktop --native-app-server`
  console entry point, not only a direct Python bootstrap.
- One real Sarvam GLM 5.3 text-only CLI probe: one provider request, native exit
  code 0, arithmetic answer `391`. Native reported 9,039 input and 20 output
  tokens. This is transport evidence, not coding/research quality evidence or
  a statement of actual billing. No repository code/user documents were sent.
- One non-elevated Windows write-boundary probe in normal inherited-ACL scratch
  folders: workspace write succeeded; sibling outside-workspace write blocked.
  No sandbox accounts were provisioned and no UAC/elevated setup was requested.
- Real Sarvam coding acceptance on a disposable synthetic project passed via
  native CLI and the app-server protocol used by Desktop. Each used six actual
  provider requests and four native commands: inspect, reproduce failing tests,
  apply a model-chosen patch, rerun five passing tests. Tests were byte-for-byte
  unchanged; independent additional cases also passed. No patch or provider
  answer was scripted. Reports: `build/native-coding-cli-2026-10-08.json` and
  `build/native-coding-restart-final-2026-10-08.json`.
- Fresh app-server process resumed the real coding thread with the completed
  item identities intact. No new turn/inference was sent during resume. This
  proves a clean process restart, not a killed-worker/reboot recovery scenario.
- Real native two-worker probe passed with Sarvam in read-only mode: two
  distinct workers spawned, both results collected, workers closed, and actual
  provider streams overlapped (peak three, including the coordinating parent).
  Nine provider requests; inputs remained unchanged. Report:
  `build/native-parallel-final-2026-10-08.json`. This is a small read-only
  delegation test, not separate-worktree coding/isolation certification.
- Desktop shutdown now requires the owning client generation and waits in a
  background worker. Reconnect disposes the old listener and clears unloaded
  thread state. Frontend tests/build and Tauri compile check passed afterward.
- Repository `.venv` CLI registration updated to editable Smara 0.1.8. `smara`
  now starts our copied native executable; `smara-legacy` retains explicit old
  CLI access. `source-status`, help and version dispatch were checked. The
  debug native binary still reports upstream development version `0.0.0`; it
  is not a numbered public release. The installed Desktop was not replaced.

Runners: `scripts/run_native_protocol_acceptance.py --tool-roundtrip --console-entrypoint`,
`scripts/run_native_live_acceptance.py`, and
`scripts/run_native_sandbox_acceptance.py`. New live task runners:
`scripts/run_native_coding_acceptance.py` and
`python -m scripts.run_native_parallel_acceptance`.

## Failures and limits kept visible

- Earlier legacy Docker timing tests failed on cold runs. The final focused run
  passed all 75 tests without changing/weakening those tests. Cold-start timing
  flakiness is not claimed fixed by this source migration.
- A repeat inside the Codex command-tool sandbox produced 11 failures and six
  skips from blocked loopback sockets, protected test-folder access and Docker
  availability. The same 75 tests passed outside that outer tool sandbox; the
  native runtime's own sandbox/approval settings and assertions were unchanged.
- The Windows write probe failed under a Python-created protected 0700 temp
  parent, including when requesting the native `:workspace` permission profile.
  It passed under normal inherited project-folder ACLs. Private-parent access
  remains a compatibility gate, not a reason to bypass the sandbox.
- The full old Smara suite and full upstream Codex suite were not run.
- Separate-worktree worker isolation and killed-worker/reboot recovery are
  not established by the small read-only delegation/clean-restart probes.
- The first worker task completed both workers with overlapping inference,
  but its answer omitted a project name required by the strict coverage check.
  The task was clarified to request the name explicitly, without changing the
  assertion. Original failure: `build/native-parallel-2026-10-08.json`; the
  successful rerun is recorded separately, not substituted for the failure.
- One coding/restart run passed its task assertions, then encountered a
  transient Windows handle while cleaning its synthetic runtime home. Cleanup
  now retries only the exact validated scratch directory and retains/report
  leftovers if still blocked. The final rerun completed and cleaned normally.
- Browser/computer-use, Syntarus memory and research adapters remain in the
  explicitly labeled legacy view. They are not claimed migrated into native MCP.
- Hosted Responses tools are rejected by the Chat adapter; native hosted search
  is disabled for it. Provider context-window/capability metadata, images,
  deferred tool discovery and compaction require provider-specific acceptance.
- Desktop integration compiles; the new native view has not yet had interactive
  visual/end-to-end acceptance or an installer cutover.
- Source/CLI entry-point changes do not mean the installed Desktop has changed.
  Native wheel packaging and a release-profile installer remain release gates.

## Next sequence

1. Complete native Desktop visual/end-to-end acceptance: approval/denial,
   specialized permission types, interruption, reconnect and resumed histories.
2. Prove separate-worktree coding workers and crash/reboot recovery; test Windows
   network, path/junction and process-tree boundaries without blanket grants.
3. Port existing research, browser/computer-use and memory tools to native MCP
   adapters so there is only one execution/permission/session authority.
4. Restore scheduling on that authority with explicit scopes, budgets and
   idempotency; do not resume company-affecting writes automatically.
5. Package native CLI binaries and a release-profile Desktop installer, perform
   visual/end-to-end acceptance, then replace the installed app/publish.
