# Desktop and CLI QA — 2026-10-01

## Changes

- Recent Tasks opens the selected task's objective, result/error, steps, activity, and files.
- Task details has initial focus, trapped keyboard navigation, Escape dismissal, restored opener focus, and protection against stale async responses.
- Structured task results are formatted for readability.
- Explicit coding profiles select the coding budget automatically.
- Quoted Windows Python test runners produce verification receipts; echoed runner names and shell-masked exits do not.
- Explicitly unfinished final answers remain `needs_input` in both agent runtimes. This conservative heuristic is not proof of full requirement coverage.

## Verification

- 166 targeted pytest tests passed in 30.73 seconds across completion receipts, harness/session integration, runtime, CLI, coding-memory/terminal, desktop, build contract, and durable handoff tests.
- The durable integration test executes a real local pytest failure, repair, and successful rerun. Provider/planner fixtures elsewhere are test doubles, not live-model evaluations.
- TypeScript/Vite build, cargo check, optimized Tauri build, and NSIS packaging succeeded.
- Built and installed frozen executors passed standalone bundle probes for storage, canonical adapter imports, completion guards, and test receipts without checkout/Python PATH dependency.
- Native app visual checks covered Chat, Studio, Workspace, Settings, completed/failed task details, expandable sections, focus wrapping/restoration, starter prompts, multiline composer, Ctrl+N, Ctrl+K, and Escape.
- No live-provider prompt was submitted during visual QA; neither a full repository suite nor a new live-model evaluation is claimed.

## Local deployment

- Installed `Smara Desktop_0.1.3_x64-setup.exe` successfully; installer exit code 0.
- Installed executor SHA256 matches the built executor. Installed launcher contains the latest frontend bundle, `index-CPwpOb95.js`.
- Existing user state was preserved. Previous app/resources backup: `build/desktop-install-backup-qa-20261001-130343`.
- App left open on a clean Chat view.
