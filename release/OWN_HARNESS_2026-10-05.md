# Smara v0.1.8 — own-harness restoration

## Architecture decision

Smara uses its own agent engine and selected Sarvam/HTTP-compatible provider.
Installing Codex must not redirect prompts to another provider or account.
The untracked experimental `codex_harness.py` bridge and its five bridge-only
tests were removed. The supplied pasted transcript retains the experiment's
description; existing private Codex state and credentials were not deleted.
Six replacement routing regressions verify the chosen architecture, including
research routing, provider preservation, ask mode, and rejection of CLI endpoints.

The original bridge copied another application's login, silently replaced the
selected model, used sandbox/hook-trust bypass flags, checked cancellation only
when stdout advanced, did not concurrently drain stderr, and could report failed
execution as completed. It is not deployed or supported in this version.

## Changes

- Restore Desktop and interactive CLI routing to Smara's own provider-backed loop.
- Retain canonical research validation/completeness checks; no success-gate bypass.
- Retain durable thread/turn/item events, exact-action approvals, cancellation
  fencing, bounded Docker commands, worker budgets and explicit recovery.
- Failed worker/swarm notices no longer show success checkmarks. Failed delegation
  retains its objective/context, and the Desktop explains opt-in worker execution.
- Restore the unchanged sealed Quick budget contract: 120 seconds, 40 tools,
  20 model calls, 150,000 accounting tokens, and $2 harness accounting.
- Align Python, frontend, Tauri, Cargo and frozen-runtime verification at 0.1.8.
- Normalize malformed line endings/trailing whitespace in pending source changes.

## Scope and limitations

This commits the pending shared-session/execution hardening needed by the rebuilt
candidate, not only the routing removal. Existing unrelated CSVs, reports, raw
model transcripts, temporary test folders and private state are not publication
inputs. The copied `.smara/codex` login from the earlier experiment remains on
disk unused; this work does not copy, refresh or delete it.

Parallel delegation is opt-in (`SMARA_ENABLE_DELEGATION=1`), capped at four
workers, with shared parent budgets and reviewable patches. It is not a promise
of reliable autonomous parallel coding. Docker tasks have no network; prepare
project dependencies in reviewed images. Recovery is explicit and uncertain
mutations are not automatically replayed. The isolated desktop backend is not
native Windows app control.

Previously recorded real-model coding/research quality failures are unresolved.
No new paid provider run is part of this update. Deterministic tests and a
checkout-free frozen probe establish integration contracts, not model quality.
No public GitHub release or release tag is created by this deployment.

## Verification and deployment

- Final Python suite: 1,220 passed, one skipped (423.32 seconds).
- Session/deadline/worker controls: 29 passed. Focused routing/Quick policy:
  37 passed. Native Rust tests: 22 passed. Frontend state tests: four passed.
- TypeScript/Vite and the Tauri NSIS build passed. Production browser transport
  checks passed for 103,999-character input, quiet-run warnings, cancellation,
  lost completion and retry failures. These use a simulated Tauri transport.
- Checkout-free frozen probes passed on both the built and installed executors,
  including worker spawn, terminal exit receipts, completion guards, checkpoint
  protocol and research-watch storage.
- Installed v0.1.8 with NSIS exit code zero; default CLI reports 0.1.8.
  Actual Windows UI inspection confirmed Chat, Run Center and Swarm navigation,
  Sarvam GLM 5.3 selection, retained history and opt-in worker disclosure.
  No provider prompt or worker task was submitted during visual verification.
- Credentials, Desktop configuration/UI state, chat-history and Codex config/auth
  fingerprints were unchanged immediately after installation. Previous binaries
  are recoverable under ignored `build/deployment-backup-20261005`.

Built and installed executor SHA-256:
`D3A811B8CD726C00472BF8F8A43D58D48ED51BF4034C993A5A57407B98B4DD27`.

Installer SHA-256:
`3A66ADDC348F24EBDE28408F2A40FCF6C93B6AAA79159A859307FE4472DFFBF7`.
