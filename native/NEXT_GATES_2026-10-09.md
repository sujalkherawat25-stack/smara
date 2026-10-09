# Native workers, DOM browser and secret audit — 2026-10-09

## State of this follow-up

The source now adds parent-directed isolated V1 coding workers, a native-approved
public-origin DOM browser, explicit CLI/Desktop opt-ins and worker status cards.
Native Rust remains the sole execution/session/permission authority. There is no
second Python agent loop. Upstream pin, Apache-2.0 LICENSE/NOTICE and original
names remain; `UPSTREAM.json` records the local worker changes.

The user approved **source commit/push after verification**, not installation or
a public release for this follow-up. The existing local v0.1.9 app/CLI and
optimized release payload were not replaced. Evidence below
uses the newly built **debug** native runtime, SHA-256
`1ba1a3204a56a1aa2e4f1df05ea6ba93876fc1f0ac66465dd377668dd5db90c8`.
Unrelated user files/reports and failed acceptance fixtures were preserved.

## Verification

| Gate | Result / evidence | Scope |
| --- | --- | --- |
| Native Rust build | Passed, locked offline debug build | Not an optimized release build |
| Focused Rust worker tests | 3 passed | Permission intersection, unrelated checkout rejection, registered Windows verbatim-path restore |
| Python regression | 101 passed, 1 Windows symlink-privilege skip; `build/native-next-focused-verified2.xml` | Native adapters/auth/CLI/scheduler, secret gate and actual owned Chromium fixtures; not the full legacy suite |
| Desktop tests | 25 passed; production frontend build passed | Source components, not installed visual acceptance |
| Tauri | `cargo check` passed | Native bootstrap arguments compile |
| Parent-directed workers | `build/native-isolated-workers-verified.json`: all 13 checks passed | Two actual concurrent workers, real patches and sandbox commands; parent unchanged, untracked file not copied, cold restart executes both children in original checkouts, no inference replay |
| Browser native approval | `build/native-browser-final.json`: all 6 checks passed | Actual MCP/fresh Chromium/public page; deny → allow once → deny, actual observation; scripted provider |
| Clock native approval | `build/native-mcp-approval-next-verified.json`: all 6 checks passed | Actual clock and Desktop response helper; no persistent grant |
| Stop/crash recovery | `build/native-recovery-next-verified.json`: both passed | Actual delayed shell child is stopped; saved session resumes without action replay or false completion |
| Bootstrap/Unicode | `build/native-protocol-next-verified.json`: passed | Actual native patch, explicit command denial, Unicode streaming, thread resume/list and graceful shutdown |
| Windows write boundary | Passed | Workspace write allowed, sibling write denied; no UAC setup, not all network/path escape cases |

All follow-up runtime probes used declared **scripted model providers**, zero paid
requests and synthetic/public data only. Actual native tool results were not
mocked. They prove these mechanisms, not real-model task quality or full Codex
parity. Recovery tests removed only their own exact disposable fixtures.

Failures remain visible: the first worker checks caught symbolic permission-root
and Windows `\\?\` cold-restore problems. Canonicalization and materialized
permission intersection fixed those causes. A default disabled-browser MCP entry
then failed native transport validation; supplying a valid inert local transport
fixed ordinary launches. A mutable public page no longer repeated its title in
its body: the browser gate now checks actual nonempty retrieved text and consistent
pagination, not the old quote. Password/file-input checks cover case-insensitive
HTML type spelling. No failed report was overwritten as a pass.

## GitHub/source secret exposure check

Read-only GitHub metadata showed `main` and `codex/source-runtime-migration` at
`2a79efcb7e1624d94b5f9142f60c68b4fde591c2`. The redacted pattern scan covers tracked
and non-ignored workspace files plus all locally reachable branch/tag blobs. No
real Smara credentials were identified. Sarvam/Syntarus key-pattern matches were
absent. Every vendor finding was checked against the exact public upstream blob;
one existing secret-rejection fixture has an exact reviewed fingerprint, not a
blanket test-directory exemption. Unknown findings or skipped inputs fail the
new CI gate. `.env.*` is excluded from new additions; `.env.example` remains usable.
The final full scan passed: 11,801 Git blobs, 11,590 workspace files considered,
153 reviewed pattern matches, zero unclassified findings and zero reported skips.
Evidence: `build/secret-audit-next-final-2026-10-09.json`.

One GitHub secret-scanning alert **remains open**, validity unknown:
[alert 1](https://github.com/sujalkherawat25-stack/smara/security/secret-scanning/1).
Its AWS-shaped value is in `vendor/codex/codex-rs/cli/src/doctor/output.rs:1891`,
an unchanged public upstream redaction unit test. Local and official source blobs
both equal `3ce3fd1b28932ab7b9c7930c3190ce1d3508d7d8`; it is a synthetic test value,
not a newly introduced Smara credential. It was not dismissed and history was
not rewritten. No credential was tested against AWS or another live service.

The workflow is part of this verified source batch; GitHub execution is a
separate gate after push, not inferred from a local pass. Patterns
are not an exhaustive proof, and this check does **not** unpack GitHub release
installer binaries or cover deleted/unreachable GitHub objects. Keys pasted in
chat should be rotated regardless of this source result. Redacted private scan
reports stay under ignored `build/`, not release assets.

## Remaining sequence

1. Finish installed Desktop visual Send → Stop → Disconnect → reconnect/resume,
   plus a human Deny check. Computer Use's Windows capture helper twice failed
   with `foreground window did not report a process id`; no security controls
   were clicked. A user reply saying "do it" is not a manual pass.
2. Run explicitly scoped real-model isolated coding/browser quality checks and
   broader sandbox/path/recovery gates. Debug mechanism tests do not replace them.
3. Add screenshot computer use only with an explicitly selected vision-capable
   model and disposable guest scope first. Sarvam GLM 5.3 is text-only. No host
   computer adapter, personal browser login, remote-memory upload or company-write
   automation was enabled here.
4. Bring claim/source review and refresh outcomes onto the native research tools
   and existing paused-by-default read-only scheduler. Legacy research UI/schedules
   are not automatically migrated or run.
5. After the authorized source push, obtain approval for installation and verify
   the optimized package,
   frozen browser-driver checks, installed-path acceptance and visual gate before
   publishing. Native executables/helpers and Chromium-driver packaging must be
   verified together; no new version or public release is claimed here.

## Reproduce the bounded source gates

Use the repository virtualenv and explicitly select this checkout's debug runtime
so old `native/dist` or installed binaries cannot be mistaken for the new build:

```powershell
$env:SMARA_NATIVE_BINARY='C:\Users\sujal\smara\vendor\codex\codex-rs\target\debug\smara-native.exe'
python -m scripts.run_native_isolated_workers_acceptance --report build/workers-recheck.json
python -m scripts.run_native_browser_acceptance --report build/browser-recheck.json
python -m scripts.run_native_recovery_acceptance --report build/recovery-recheck.json
python -m scripts.audit_repository_secrets --verify-upstream --gate --report build/secret-recheck.json
```

The public browser check is a single bounded probe, not repeated monitoring or a
live-site load test. Real-provider runners require separate scope/budget approval.
