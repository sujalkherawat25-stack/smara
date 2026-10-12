# Goals, outcomes and speech — v0.1.15 candidate

## Changes

- Desktop, CLI and Telegram share a persistent goal ledger and one native-session
  supervisor. It resumes the same session for bounded cycles, records feedback,
  and stops for approvals, cumulative limits, interruption or repeated lack of
  progress. There is no second planning/tool engine.
- Coding checks use recorded native test exits after edits. Research checks use
  fetched source pages and exact quoted excerpts. Mixed goals require both.
  Time-sensitive goals need a clock read in the current cycle. Output hashes
  are checked again at acceptance. Only the user accepts an outcome as complete.
- Goals show finished steps, evidence gaps, elapsed time, failed page reads and
  claim-to-source review. Telegram status/result messages use the same evidence.
- Explicit Sarvam speech input/output uses Saaras v3 and Bulbul v3. Recording
  stays local until Transcribe; the transcript becomes a draft, never an
  automatically sent message. Read aloud asks before uploading response text.
  Recording/playback cleanup, playback error reporting and local-audio CSP are
  covered by regressions.

## Completed checks

- Native Python contracts: 288 passed, two skipped.
- Separate compatibility and research contracts: 63 and 65 passed.
- Desktop frontend: 74 passed; TypeScript/Vite build passed.
- Desktop Rust: 11 passed.
- Native-only wheel boundary: passed; retired Python execution modules excluded.
- Reachable-history/working-tree secret gate: no unclassified findings or skipped
  inputs. Detected matches have reviewed public upstream/synthetic origins.
  This is a pattern audit, not proof that every possible secret is absent.
- Initial approved Sarvam check: one TTS/STT round trip passed, and research-only
  reached Ready for review with two fetched Python documentation pages and three
  exact excerpts. Coding and mixed checks failed at sandboxed native patching;
  those failures are retained, not replaced with passing fixture output.
  Eleven chat requests reserved INR269.87202 of an INR300 estimate ceiling.
  Speech billing was separate; actual provider billing is unknown.

## Outstanding candidate gates

An initial probe found MXC failing workspace writes while restricted-token
passed. Smara now selects the verified restricted-token sandbox by default on
Windows; MXC remains unselected until it passes its own gate. The model-free
probe confirms an in-workspace write, rejects an outside write and requests no
approval or unsandboxed retry on the selected backend. The approved coding/mixed
rerun used eight chat requests and reserved INR217.65 of its INR250 conservative
estimate. Coding changed only the synthetic source and its independent tests
passed, but the goal was blocked by an outcome-check validation bug (coding
claims incorrectly required research URLs). Mixed fetched both public pages and
changed only synthetic source; its pytest request remained approval-gated and
the bounded run paused at 180 seconds. Independent synthetic tests passed and
the supplied test file stayed unchanged. Do not repeat Sarvam calls; the request
ceiling was reached. Speech and research-only checks were not repeated.

The actual packaged v0.1.15 GUI passed an isolated offline check: project setup
loaded, one Unicode response streamed from the loopback-only fixture, and Stop
closed that stream while the native ledger recorded an interrupted turn (no
paid requests). CLI and Desktop native payload hashes match; the installer is
SHA-256 `22253CFD17A89B041A364F363D032185BDF50DCA98142245227A4669B71D008E`.
Install and source push are separate delivery actions; no public GitHub release
is authorized by this batch.

## Cleanup and limits

Fourteen obsolete generated build/CLI directories were removed, freeing about
2.18 GiB. The previous version, rollback copies, source, histories and protected
credentials were preserved. Generated packages can be rebuilt/reinstalled.

Quotation provenance is not semantic entailment or full answer coverage. These
small synthetic/public-document tasks do not establish broad capability parity
or business-outcome quality. Live Telegram delivery needs a bot token and human
pairing; microphone permission is a human action. Neither is certified by the
protocol fixtures or speech API round trip.

## Maintained runners

- `scripts/run_native_goals_acceptance.py`: actual native protocol with a
  declared local scripted provider; continuation, feedback, limits and Deny.
- `scripts/check_native_patch_sandbox.py`: actual native apply_patch with a
  declared local scripted provider, never an approved unsandboxed retry.
- `scripts/check_goals_voice_live.py`: separate persisted live reservations;
  synthetic coding fixtures and public documentation only. Failed reports stay
  in ignored local build evidence. It is not an invoice or a broad benchmark.
- `scripts/cleanup-smara-generated.ps1`: preview first, explicit version paths,
  checks for links, tracked files, running processes and user state before delete.
