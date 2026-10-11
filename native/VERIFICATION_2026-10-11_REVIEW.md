# v0.1.12 verification — 2026-10-11

## Shipped scope

Changes/file/hunk review, explicit stage/unstage/revert, supported last-turn
Undo with local recovery receipts, local branch comparison, agent command
transcript, model-free CLI review and Help instructions. The native runtime
remains the only agent/tool execution authority.

## Checked

- All native Python integration unit tests: **178 passed, 2 skipped**. These
  use offline fixtures, not a real-model success substitute. The earlier wider
  regression run and its limits are documented in [CHANGES.md](CHANGES.md).
- Frontend: **47 passed**; TypeScript/Vite production build passed.
- Rust Desktop activity guard: **4 passed**; release build passed.
- Installed v0.1.12 CLI: actual diff → stage → staged review → unstage →
  revert → recovery against a separate synthetic Git project passed.
- Installed Windows Desktop: real diff/file/hunk controls, panel switching,
  Agent terminal empty state, and the v0.1.12 Help page inspected. No GUI Git
  write, security approval or provider-test button was operated. GUI writes
  are covered by real-Git backend/CLI tests and synthetic component tests.
- Installer validates Desktop bundle marker/version, all six native resources
  and the frozen companion against local build payloads. Existing preferences
  and protected credentials were hash-identical before/after installation.
  Existing install-directory skills and previous app/CLI copies are preserved
  in local backups. Fresh-terminal PATH resolves the 0.1.12 CLI first.
- Frozen Desktop companion contains the review helper and product-version
  data; all eight archived execution modules remain excluded. CLI reports the
  product release separately from the imported core's development version.
- Redacted staged credential-pattern scan: **34 files, zero matches**.
- Decompressed package scan: **1,766 code modules, 574 data entries, 10 release
  resources; zero matches**. This is pattern detection, not proof that every
  possible secret format is absent.
- Fresh source/history gate: **11,945 Git blobs**, **9,909 workspace files**,
  **153 reviewed public-upstream/synthetic patterns**, **zero unclassified
  findings and zero skipped inputs**. The upstream origins were verified
  read-only; no credential values were uploaded. This does not audit deleted
  remote Git objects or every historical GitHub release asset.

No paid model requests, private-code uploads or remote-memory writes were made
for these checks. This does not certify full research quality, all Windows
escape cases, full desktop/computer use, or Codex GUI parity.

## Interactive terminal prerequisite — not shipped as a feature

`scripts/run_native_terminal_acceptance.py` exercises the actual copied runtime
with isolated synthetic folders and an inert loopback model URL.

Passed before stopping: buffered command output/nonzero exit, workspace write,
and outside-workspace write rejection. PTY/streaming was **blocked**, with the
actual native error: `streaming command/exec is not supported with windows
sandbox`. The runner retains this result and exits nonzero. Unicode stdin,
resize and termination are not reported as passed.

Do not remove this upstream safety guard or substitute unsandboxed
`process/spawn`. The next terminal implementation must add a genuine Windows
sandboxed streaming/control transport, then test cancellation, lost connection,
worker/Git activity locks, and path/network boundaries. File/artifact preview
remains a separate next UI step; it is not included in v0.1.12.

## Installer integrity

Installer: `Smara Desktop_0.1.12_x64-setup.exe` — 144,397,076 bytes.

SHA-256: `87a5e15e94cc873837d63906a81486f2743094be4c2f16d04cdacbc6608eb2d1`
