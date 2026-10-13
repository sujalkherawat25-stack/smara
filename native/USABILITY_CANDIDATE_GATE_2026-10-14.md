# v0.1.23 source-freeze gate — not a public release

This batch includes the previously local v0.1.21/22 memory, outcome, outreach,
browser and background-worker work, plus chat usability and recovery hardening.
User-deleted reports and the manual architecture-PDF diagnostic remain outside
the commit. Their removal is not silently reverted or staged.

## Changes

- Distinguish waiting for model/tools/workers from waiting for human input.
- Keep worker polling and Stop available after the parent turn completes;
  unknown worker state cannot unlock writes, sending or project changes.
- Bind approval grants/questions to their requesting conversation or known worker.
  Malformed/duplicate questions are rejected without crashing chat.
- Clear project-specific goal/answer state on project switch.
- Show local connector metadata and pending/failed memory writes in chat; failed
  ingestion cannot be hidden by a successful recall. No automatic health probes.
- Use an absolute hosted-ingestion deadline; polling cannot refresh it forever.
- Preview recorded output paths with their historical hashes and current-file caveat.
- Persist bounded public-page snapshots for cold pagination; preserve retrieval
  time and project isolation. No OCR/private document snapshot storage.
- Reserve the last goal inference request for tool-free progress serialization.
  Normalize a bounded presentation label, never missing or invalid evidence.
- Expose a fixed rate-limit reason without raw provider error text.
- Add exact-source/build manifest capture, pre/post-build checks and artifact seals.

## Executed checks

- 479 Python tests passed, two Windows symlink-privilege skips. Includes native,
  Docker, worker snapshots and legacy execution boundaries. JUnit retained under
  ignored `build/usability-v23-native-tests.xml`.
- 104 Desktop frontend tests passed; TypeScript/Vite production build passed.
- All 12 Desktop Rust tests passed, including rejection before terminal dispatch.
- Actual native Stop/crash recovery, no replay/delayed writes: passed.
- Actual isolated workers/cold restart: 13 checks passed; parent unchanged.
- Actual Windows workspace/sibling write check passed. This is not network or
  junction/escape certification.
- Actual clock plus bounded checkpoint-format recovery passed using a declared
  local scripted provider. It is not model-quality evidence.
- Secret audit: no unclassified findings or skipped inputs; 153 detected patterns
  matched reviewed public upstream or synthetic fixtures. This is not proof about
  every possible secret or remote release/deleted objects.

## Live-model results and failures retained

Only synthetic coding and public Python documentation were sent. No hosted memory,
private code/history or emails. 19 actual requests, cumulative INR 586.944468
conservative reserved estimate, within the approved INR650/19 ceiling. Billing unknown.

- Coding: unchanged independent tests passed. The completed model checkpoint was
  locally revalidated against the actual native transcript after removing only a
  harmless coverage label. No additional inference or actions; evidence checks pass.
- Latest: two public pages read, submitted excerpt support 100%; final checkpoint
  still lacks a fresh clock observation in its cycle. Remains unverified/queued.
- Failed page: HTTP404 retained; final quote support 25%, and model itself says more
  work is needed. Remains unverified/queued, not falsely completed.
- Earlier failed reports remain separate. A provider 429, cold source-cache loss,
  local test-runner token-cap stop and malformed checkpoint are recorded, not erased.

## Security-relevant terminal failure

The real native command/exec probe connected to its owned loopback listener despite
`networkAccess:false`. File confinement success does not fix that failure. The
Desktop interactive terminal is disabled at Rust dispatch and UI/status/Help.
The original failed report is retained. No firewall, UAC, security setting or
unsandboxed fallback was introduced. Windows command network isolation is not certified.

## Remaining gates

Installed-candidate GUI/packaged checks, fresh human Deny, and live research quality
are pending at source freeze. Gmail account send and broad computer-use production
certification are not claimed. Do not create a stable release/tag from this file.
See [current capability matrix](CAPABILITY_MATRIX.md). Build/probe outputs belong in
ignored `build`; publication must reference the exact fixed source and artifact hashes.
