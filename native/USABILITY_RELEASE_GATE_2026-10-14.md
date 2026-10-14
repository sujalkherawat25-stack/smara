# v0.1.23 follow-up release gate

Status: **completed; v0.1.23 published from the exact tested build**.
The earlier source-freeze gate is historical, not the current release result.
This post-release documentation update does not move the tag or rebuild the app.

## Fixed boundary

Windows now explicitly selects MXC. There is no automatic restricted-token
fallback: its earlier loopback test failed despite a no-network request.
The actual MXC command gate passed TCP, UDP, IPv6, descendant, junction-write
and PTY lifecycle probes. This is a bounded host-specific check, not universal
Windows escape certification.

The separately launched filesystem helper omitted `SystemRoot`. MXC rejected
that filtered environment before executing a patch. A synthetic native probe
reproduced the failure and succeeded with only `SystemRoot` restored. The
case-insensitive allowlist retains platform variables, not provider credentials.
The actual rebuilt patch and worker gates passed without permission widening.
Unit success alone was not used as evidence that the fix ships.

Core SHA-256: `de9bac858a0ec4e03603b7a999f6f35d4bc4426a44bdeb0b21283a4090a61a15`.
All post-fix execution reports record that same binary: five patch checks,
13 isolated-worker/cold-restart checks, 13 MXC terminal checks and 11 Stop/crash
checks passed. Scripted providers were explicitly offline; actual tools, file
changes, network/path probes and cleanup were not substituted with mocks.

## Completed checks

- Python native/package boundary batch: 473 passed, 11 skipped. Nine skips were
  Docker-dependent; the subsequent real Docker/workspace lane passed all 15
  checks with Docker 29.2.0. Two Windows symlink-privilege cases remain skipped.
  These counts overlap and must not be summed as unique tests.
- Desktop: 105 frontend tests, TypeScript/Vite production build, 12 Rust tests.
- Native Rust SystemRoot/secret-exclusion regression passed.
- Read-only secret gate: 12,178 reachable blobs, 9,944 working-tree entries,
  153 reviewed public/synthetic matches, zero unclassified findings/skipped inputs.
  Remote release assets/deleted objects and arbitrary undetectable secrets are
  outside this scan's scope. Repeat the gate on the final commit.

The extended parallel executor unit run had 371 passes and four failures.
The Unix `true` fixture passed with the already-installed Git tool on the test
process's PATH. The two timing checks passed individually without changing
their deadlines/assertions. The full serial rerun passed all 374 executed tests,
explicitly excluding only the unavailable Windows symlink privilege test. Original failures are not
reclassified as passes; OS security settings were not changed.

## Fresh real-provider research gate

Both public-documentation cases reached `ready_for_review`: latest/date intent
and failed-page recovery. Eight actual requests, INR248.606820 conservative
reservation, within the approved INR400/eight-request bound; actual billing
unknown. No private code/history, hosted memory or emails were sent.

Latest had current-cycle clock evidence and two fetched public pages. The
failed-page case retained HTTP404 and used a final tool-free checkpoint on its
saved session; completed searches/reads were not replayed. Submitted excerpts
matched cached page text. Quote presence/evidence linkage does not prove semantic
correctness or a real business outcome. Earlier failed live reports are retained.

## Final installed verification and publication

The installed terminal exposed a PowerShell-specific gap after the Python PTY
gate: PSReadLine attempted personal-history access outside the workspace and
duplicated line editing already handled by the GUI. The fixed, native-validated
startup unloads PSReadLine before the first prompt, without adding environment
overrides, personal write roots, network access or profile loading. The actual
MXC gate now includes the exact Desktop PowerShell argv, two consecutive command
lines, absence of personal-history errors and shell termination: all 17 checks
pass. Earlier test-harness timeouts (ConPTY represents a prompt space with a
cursor escape) are retained as failed reports, not silently reclassified.

The previously installed package passed observed Send/Stop/reconnect without
replayed inference and its protected-settings/CLI checks. The user-discovered
PowerShell error prevented releasing that installer. The fixed package was
rebuilt, sealed and installed, with preferences and protected credentials
unchanged and recoverable binary backups retained. Nine installed CLI checks
and 13 synthetic installed settings checks passed.

The human reported the terminal marker appeared without a history error and
the shell stopped. Its transcript was empty at subsequent inspection, so this
is recorded as human confirmation, not automated GUI command verification.
The final human Deny was separately observed in the installed GUI and the
durable native report passed all five checks: clock tool failed with user
rejection, null result, rejection delivered to the offline provider, no fixture
errors and one completed native turn. No paid model was used for these GUI checks.

Both branches' seven CI lanes and redacted secret-audit workflows passed for
`4afbe276d031e7290cabf916e8a17b0edf14d848`. The installed GUI/resources matched
the sealed payloads. GitHub's uploaded installer digest matched the tested local
SHA-256: `5063d9bb3f4c7a9f7db289412be42c07e5484805cb5c63033c2723258cca5cad`.
The remote `v0.1.23` tag points to that exact source commit; publication was
confirmed, with checksum and final capability-matrix assets attached.

[Published release](https://github.com/sujalkherawat25-stack/smara/releases/tag/v0.1.23).

Gmail real-account OAuth/sending, broad Windows selected-app production
certification and hosted-memory quality are not claimed by this release. No
unattended email campaigns, personal-browser access or unrestricted computer
control are enabled. History and protected credentials must survive installation.
