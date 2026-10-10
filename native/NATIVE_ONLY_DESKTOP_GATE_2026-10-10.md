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

## Local installation and installed-binary verification

The user renewed the request to finish local replacement. The source-verified
candidate was installed as **0.1.9**, without publishing a new release. This
does not promote the unverified human GUI Deny check to a pass. Local candidate
installation now rests on the passing native permission checks and observed GUI
lifecycle checks; public distribution remains gated separately below.

- The idle old Desktop was closed normally, and its owned processes exited.
- The installer backed up both prior installations under the local
  `Smara-backups` directory (`desktop-pre-0.1.9-20261010-094948` and
  `cli-pre-0.1.9-20261010-094950`). Install-directory skills were preserved.
- Existing preferences and protected credential files passed unchanged-hash
  checks. Native sessions were not migrated or replayed.
- Installed Desktop SHA-256 is
  `4D89ADAA446D5CC2350A668C7E36F1114CCCE230EFE6EBEA65AC8305A5588D6A`.
  It matches the tested NSIS payload after the exact upstream Tauri bundle-marker
  patch; the installed companion and all six native payloads also match.
- Installed CLI matches the tested frozen candidate and is first in the user
  PATH. New terminals pick up that PATH; no unrelated entries were removed.

| Installed check | Result | Evidence |
| --- | --- | --- |
| Native transport and tools | 13 checks passed; real scratch patch, command denial, Unicode and durable resume | `build/native-only-installed-protocol-20261010.json` |
| Desktop / CLI shared settings and real DPAPI | 8 checks passed using synthetic isolated state; no provider calls | `build/native-settings-check-f9d168dd7e194b96a2fa097152ee00b7/report.json` |
| Installed GUI Connect / Send / Stop | Visually observed; 5 stream/ledger checks passed, with one local request closed early and no false completion | `build/native-desktop-installed-offline-85eab2cd588248f285b77c9b73080672/stop-report.json` |
| Installed GUI reconnect / same-conversation resume | Interrupted history restored, no replay; provider request count remained one | Windows computer-use inspection of that isolated installed-app fixture |
| Installed GUI settings | Models and tools screens inspected; configuration edits disabled while connected, keys never displayed | Windows computer-use inspection of the isolated installed-app fixture |
| Installed CLI entry point | `smara --help` launched the source-native CLI and integration-command help | Installed frozen CLI, no prompt sent |
| Final redacted repository/history scan | 11,884 blobs and 11,609 workspace files; 153 reviewed public/synthetic matches, 0 unclassified and 0 skipped inputs | `build/secret-audit-native-only-deployed-20261010.json` |

The installed-app stream is an explicitly scripted offline provider, not a
real-model answer benchmark. GUI actions were observed separately; the runner's
`gui_actions_machine_verified` field correctly remains false.

The normal installed app was reopened after closing the test fixture. Visual
inspection showed the preserved Sarvam GLM profile, imported project and readable
Tavily credential readiness. No prompt was sent, no permission option was changed
and no secret value was displayed. The real app is left open and disconnected.

GitHub's redacted secret-audit workflows passed for source commit `657a509` on
both pushed branches. GitHub still reports one open AWS-format secret alert in
the byte-identical imported upstream doctor test fixture. That alert was neither
dismissed nor tested for credential validity; no claim of zero open alerts is made.

The 26 skips are 24 Docker Linux-engine checks (engine unavailable) and 2
Windows symlink-privilege checks. They are not passing isolation evidence.
Unit-test fixtures and offline scripted providers are explicitly distinguished
from actual provider search and real-model quality. No paid AI-model request or
private-code / personal-history / Syntarus upload was made for this batch.

The failed pre-fix clock fixtures are preserved. They recorded **zero requests**,
not successful denials: GUI startup failed with `Native transport stderr is
missing`. Their human responses were not promoted to a passing test result.

## Remaining gate / boundaries

The human GUI Deny check remains **unverified**. The user's latest screenshot
showed the older real-model interface, not the isolated clock fixture, and no
passing clock-fixture evidence was obtained. Automated protocol permission checks
pass, but do not replace the human security-button check. The computer-use skill
does not permit the assistant to click permission controls. This check, Docker
isolation checks and broader real-model quality gates remain outstanding before
a new public release; no new public tag/release is created by this batch.

Host screenshot/computer control, personal-browser sessions, unrestricted
company-write automation and full proprietary Codex Desktop features are not
included. Legacy research schedules and chat histories are not silently migrated
or executed. Model quality and broader live-task evaluation remain separate gates.
