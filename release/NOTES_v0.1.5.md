# Smara v0.1.5 — Long Messages & Output Continuation (Beta)

## Changes

- Removed artificial 8,000/12,000/20,000-character message and task truncation in the affected CLI, Desktop, API and persistence paths. Desktop saves complete turns instead of destructively pruning history.
- Large supported model-context inputs and tool outputs are archived locally with a content hash and character-paged reads. Model requests remain bounded; archival does not mean the model has read every section.
- Local conversational prose responses can continue after a provider output limit, with at most eight additional requests. A failed or exhausted continuation retains partial text and reports incomplete status.
- Memory listing requires an explicit command. Pasted prose containing words such as memory, show and list is no longer mistaken for a request to list memories.
- Updated Desktop, CLI and frozen executor to v0.1.5.

## Verification

- Python regression selection: 181 passed, one Windows symlink-related skip.
- Rust memory-command routing regression passed; frontend typecheck/build, Cargo validation, NSIS packaging and built/installed frozen-executor probes passed.
- Real Sarvam CLI checks: 24,308-character input passed in 1.44 seconds; 160,305-character input passed in 2.51 seconds, including recovery of a marker from the archived middle section.
- Desktop installed and visually checked at v0.1.5; existing user history preserved. Native Desktop long-message submission was not separately live-tested.
- Output continuation was regression-tested with provider fixtures, not a live long-output benchmark.

## Limits and retained evidence

Provider context windows, disk capacity, file/process safety limits and request budgets still apply. This is not unlimited context or output. Previously truncated text cannot be reconstructed; resend the original message if needed. Large transcript rendering/storage performance has not been stress-tested at arbitrary scale.

The initial live inline check returned the correct markers but failed the CLI's required FINAL ANSWER format. Its failure logs remain in the local build evidence. The final acceptance prompts explicitly request that format; the CLI format gate was not bypassed. Reported token/cost counters are internal accounting, not verified Sarvam billing.

Private-code memory recall remains local-only. No credentials or private conversation contents are included in this release.
