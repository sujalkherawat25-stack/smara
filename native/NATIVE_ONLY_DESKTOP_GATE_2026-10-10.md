# Native-only Desktop candidate — 2026-10-10

This is the local 0.1.9 migration candidate, not a new public release or a claim
of complete Codex Desktop / computer-use parity.

## What changed

- Desktop compiles the native-only Rust shell and ships a thin transport/settings
  companion. The old execution bridge, panels and primary CLI legacy dispatch
  are retired; historical source, conversations and vault data are retained.
- Projects have their own native conversation lists. Resume verifies the saved
  workspace, and changing projects disconnects and resets optional tool choices.
- Models and search credentials have dedicated settings. Secrets use private
  stdin and Windows DPAPI, never UI storage or returned credential values.
  Edits retain model context metadata, blank-key edits retain the vault, key
  rotation uses the correct alias, and metadata-save failure restores old keys.
- Tavily, Exa, Serper and Brave use provider-bound endpoints. Missing keys,
  authentication, quota and network failures are actionable and redacted; an
  explicitly selected provider is never silently substituted.
- The new GUI provides an expanding composer, readable code/tool output, Stop,
  project navigation, older-conversation pagination and guarded shortcuts.
- Fixed native startup's missing stderr pipe and frozen CLI Unicode settings
  output. Startup/settings work happens off the GUI thread.
- Installation backs up the prior Desktop and same-version CLI and validates
  native payload hashes and unchanged preferences/credential files.

## Verified evidence (local, ignored build directory)

| Check | Result | Evidence |
| --- | --- | --- |
| Full Python regression | 1,332 passed, 26 skipped | `build/native-only-desktop-full-verified-20261010.xml` |
| Final focused regressions, including later credential/startup fixes | 36 passed | `build/native-only-final-focused-20261010.xml` |
| Frontend | 27 tests passed; typecheck/build passed | `npm test`, `npm run build` |
| Native-only Rust shell and NSIS | Check and release packaging passed | `scripts/build-smara-desktop.ps1` |
| Frozen transport | 13 checks passed; real patch, denied command did not execute, Unicode and resume | `build/native-only-final-package-protocol-20261010.json` |
| Frozen settings / real DPAPI | 8 checks passed; synthetic state only, no provider calls | `build/native-settings-check-adc652e84e02401fa89c131be9163bab/report.json` |
| Public search | One actual Tavily request through frozen MCP returned 3 results; no model call | `build/native-only-frozen-search-20261010.json` |
| Native clock permissions | Deny / allow once / deny passed; no persistent grant | `build/native-only-final-mcp-permissions-20261010.json` |
| GUI Connect / Send / Stop | Observed through Windows computer-use; native interruption and early stream closure verified | `build/native-desktop-installed-offline-a1b767cdd9434a21995015ba0889fe6e/stop-report.json` |
| GUI reconnect / resume | Same conversation restored as interrupted; no automatic replay | Windows visual inspection of that isolated fixture |
| Secret gate | 0 unclassified findings and 0 skipped inputs; public/synthetic origins checked | `build/secret-audit-native-only-verified-source-20261010.json` |

The 26 skips are 24 Docker Linux-engine checks (engine unavailable) and 2
Windows symlink-privilege checks. They are not passing isolation evidence.
Unit-test fixtures and offline scripted providers are explicitly distinguished
from actual provider search and real-model quality. No paid AI-model request or
private-code / personal-history / Syntarus upload was made for this batch.

The failed pre-fix clock fixtures are preserved. They recorded **zero requests**,
not successful denials: GUI startup failed with `Native transport stderr is
missing`. Their human responses were not promoted to a passing test result.

## Remaining gate / boundaries

The repaired clock GUI fixture is open for a human Deny decision. Automated
protocol permission checks pass, but they do not replace the GUI security-button
check. Local installation remains pending that check; no new public tag/release
is created by this batch.

Host screenshot/computer control, personal-browser sessions, unrestricted
company-write automation and full proprietary Codex Desktop features are not
included. Legacy research schedules and chat histories are not silently migrated
or executed. Model quality and broader live-task evaluation remain separate gates.
