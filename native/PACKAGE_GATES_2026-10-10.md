# Optimized native candidate follow-up — 2026-10-10

This is a local **0.1.9 migration candidate**, not a new public release or a
claim of complete Codex product parity. Earlier real-model evidence and its
budget remain in `LIVE_QUALITY_GATES_2026-10-09.md`. No additional paid model
requests were made for the package checks below.

## Fixes

- Readers now explicitly override saved native configuration **off or on**
  according to the current CLI/Desktop opt-in. A valid disabled transport
  prevents native config validation failures. No saved reader permission is
  automatically granted or rewritten by this adapter.
- Native CLI config overrides retain literal Unicode. JSON surrogate escapes
  for emoji are invalid TOML; the frozen Desktop check caught that actual
  launch failure in a Unicode workspace. Tests cover frozen/source argv with
  non-Latin and non-BMP characters.
- The offline isolated-worker acceptance fixture now waits on the unfinished
  worker rather than repeatedly observing the first finisher. The first failed
  optimized check is retained: one initial child was genuinely interrupted by
  premature fixture shutdown. Its result was not relabeled as a pass. This
  changes the declared scripted test provider, not the real model or runtime.

## Verified so far

| Check | Result | Evidence inside ignored `build/` |
| --- | --- | --- |
| Optimized source-owned runtime and Windows helpers | Official locked release build passed, 68m 30s | `native/dist/` and source build output |
| Reader opt-out versus saved enabled config, then opt-in versus saved disabled config | Actual native config/inventory and clock pass; deny/allow-once/deny requests remain separate | `optimized-reader-approval-final-20261010.json` |
| Source bootstrap in Unicode workspace | All 13 checks pass, real native patch and denied command, 4 local scripted requests | `optimized-unicode-source-protocol-20261010.json` |
| Optimized isolated workers and cold restore | All 13 checks pass; parent unchanged, 2 workers, both turns completed in original checkouts | `optimized-isolated-workers-wait-fix-20261010.json` |
| Earlier optimized worker failure | Retained, not counted as a pass | `optimized-isolated-workers-20261010.json` |
| Stop and crash recovery | Both pass; delayed child write prevented, no action replay or false completion | `optimized-recovery-20261010.json` |
| Windows unelevated workspace boundary | In-workspace write succeeds, sibling write blocked; no elevated setup | `scripts.run_native_sandbox_acceptance` output |
| Final targeted tests | 59 passed | `reader-worker-final-unit-20261010.xml` |
| Final native-focused regression | 114 passed, 1 symlink-privilege skip, 1,217 deselected | `native-final-package-focused-20261010.xml` |
| Desktop frontend unit tests | 25 passed | `npm test` output |
| Earlier full regression | 1,302 passed, 26 skipped, zero failures; collected before final Unicode/wait cases | `native-package-full-20261010.xml` |
| Final full regression | 1,306 passed, 26 skipped, zero failures, 482.05 seconds | `native-final-package-full-20261010.xml` |
| Final portable CLI build | Passed, source-owned optimized payload, 61,160,469-byte launcher | `native-cli-candidate/` |
| Final portable CLI archive/help | Playwright and Node driver present; pandas, pyarrow, datasets, pytest, Tk, numpy and legacy agent-loop modules absent; offline help passed in isolated home | Actual PyInstaller archive inspection and `--help` |
| Final frozen CLI public DOM browser | All 6 checks pass, no source Python on child path or personal cookies | `frozen-browser-check-898b55eb7c884808a5e4a0f59057a09e/report.json` |
| Final frozen Desktop protocol | All 13 checks pass, including the Unicode workspace and denied command | `optimized-final-frozen-desktop-protocol-20261010.json` |
| Final frozen Desktop public DOM browser | All 6 checks pass with actual frozen driver | `frozen-browser-check-3a4f2f6cea07488c92c7ff992a1d2311/report.json` |
| Native payload consistency | All 6 native/license/provenance files hash-identical in core, CLI and Desktop resources | Pre-install hash matrix |
| Final Desktop/NSIS build | Official build passed; source frontend, Tauri and frozen executor included | `apps/desktop/src-tauri/target/release/bundle/nsis/Smara Desktop_0.1.9_x64-setup.exe` |

The earlier full run skipped 24 Docker-dependent cases because the Linux engine
pipe was absent, and 2 cases because this Windows account cannot create symlinks.
Skipped tests are not passes. The copied native Windows executor does not depend
on Docker; its write-boundary and child-process checks above ran separately.

Native sandbox acceptance is one bounded write-boundary probe, not a complete
network, junction, hardlink, or path-escape certification. Public DOM browsing
does not establish desktop/screenshot computer-use capability.

Native core SHA-256:
`139cb5c554bb6d3add6f47ca37b4cb5ce571e58200c32307deeac381f624a52f`.
Command-runner SHA-256:
`6ae401a231c0864ff24c3ee497c9f72c67ef539349ceba7baddfd280743858b3`.
Windows setup helper SHA-256:
`09334d3b16969897397d1202cb9db45a20dfbf9cad9fe9cffa3fd5d387fa9e54`.
Final frozen CLI launcher SHA-256:
`6d78fde4501f4101bfc50fc9cf1cbf18df11463129ceb8a422cd85c87529241d`.
Final frozen Desktop executor SHA-256:
`d0e7e977695ce14a8ea5d0fafc8d211d8cf8d5aa515adcb4804ca030f41f291b`.

## Remaining before installation

1. Human GUI **Deny** check on the actual packaged candidate, using only the
   isolated, clearly labeled OFFLINE clock fixture. The assistant cannot click
   reader/security opt-ins or permission decisions. Native helper tests above
   do not replace this human visual gate.
2. Commit/push this verified source batch after the redacted secret gate;
   then install the verified local candidate with recoverable Desktop **and
   same-version CLI** backups and preserved preferences/protected credentials.
   Do not terminate active user work or publish a release/tag as a side effect.

The public upstream fixture secret-scanning alert documented in the earlier
gate note remains open. Pattern scans cannot prove absence of every secret,
cover deleted remote objects/release assets, or establish credential validity.
