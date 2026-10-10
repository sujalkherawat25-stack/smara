# Conversation-first Desktop UI — 2026-10-10

This updates the local 0.1.9 candidate. It does not publish a new version or
claim complete Codex Desktop / computer-use parity. The user's screenshots were
visual references; no proprietary Desktop source or security bypass was added.

## Changes

- Quiet project header and sidebar, one bottom-left Settings entry, and a model
  picker inside the expanding composer. Removed the normal Connect/Disconnect
  controls, duplicate Manage settings link, sandbox badge and top Tools toolbar.
- The app prepares its selected project automatically. Settings pauses the idle
  owned runtime internally; returning to chat restores the selected conversation
  without replaying a turn. Model changes preserve the draft and explicitly
  override the saved conversation's model on native resume.
- Models and Integrations are the only Settings sections. Optional research,
  memory, workers and public-browser origins remain explicit choices there;
  changing projects still resets them. Action approvals and native sandboxing
  remain unchanged. Credential values are not returned to the UI.
- Configuration writes lock navigation until they settle. Failed startup has
  explicit recovery, not a retry loop. An unknown turn stays locked; a failed
  history restoration cannot silently submit a reply into a new conversation.
- Visual review found an outdated Settings → Tools help location. Displayed
  search-readiness hints now point to Settings → Integrations. Search behavior
  and protected credential handling are unchanged.

## Verification

- `npm test`: 36 passed. Nine new tests run the actual TSX in a declared synthetic
  hook/transport harness; these are not installed-GUI or real-model evidence.
- Focused Python regression: 50 passed; profile/management, native-quality
  runner and desktop build contracts. Evidence:
  `build/ui-refresh-python-final-20261010.xml`.
- TypeScript, Vite, native release compilation and Windows NSIS packaging passed
  from the same final source (`index-DEXWOgq9.js`, `index-CYYnrn6x.css`).
- Windows computer-use inspection of the actual native GUI confirmed the clean
  layout, composer model picker, genuine Send → streaming → Stop, Settings →
  Back with the same interrupted conversation, and alternate-model selection
  retaining an unsubmitted draft. No security controls were operated.
- Isolated loopback provider and real native ledger: five cancellation checks
  passed, one request, early stream closure, one interrupted native turn and no
  false completion. Request count stayed one through Settings and model changes.
  Evidence: `build/native-desktop-installed-offline-14e5271aea8f40b88d036862b36df9a1/stop-report.json`.
  This preview preceded only the final help/recovery wording corrections;
  installed-binary verification follows packaging below.
- Redacted final pre-push scan: 11,885 Git blobs and 11,611 workspace files,
  153 reviewed public/synthetic pattern matches, zero unclassified findings and
  zero skipped inputs. Public fixture origin was checked. Evidence:
  `build/secret-audit-ui-refresh-prepush-20261010.json`.

## Installed verification

The final candidate was installed successfully. Previous installations were
backed up under `Smara-backups/desktop-pre-0.1.9-20261010-104919` and
`Smara-backups/cli-pre-0.1.9-20261010-104922`. Preferences and protected credential
files passed unchanged-hash checks; install-directory skills were retained.

- Installed Desktop SHA-256:
  `1B18116E150C0767A84F27A30C2CF6AC7A81876055F98FAF9BF798DCAD205F8A`.
  It matches the final NSIS payload with the exact upstream bundle-marker patch.
  Companion and native-resource hashes also matched.
- Final installer SHA-256:
  `7EFAF524F65668FA8BF156D551C2A49B20B33842360CDE7D88F72C95AFAFEF69`.
- CLI is unchanged and matches the previously tested candidate:
  `0F2092AC00CCC23A93CC9530A5CF9B89EBE71AA7C2926C68920BC71F4CE2ACAB`.
- Installed native protocol/tool acceptance: 13 checks passed, including real
  scratch-file patching, a denied command that did not execute, Unicode,
  history, resume and transport shutdown. Four scripted loopback requests,
  no paid inference and no claimed OS-isolation test. Evidence:
  `build/ui-refresh-installed-protocol-20261010.json`.
- Exact installed GUI: automatic preparation, composer model picker, Send →
  streamed output → Stop, the two Settings sections, corrected Integrations
  help, and Back → same interrupted conversation were visually observed.
  Five native-ledger/stream checks passed. Request count remained one through
  Settings return; no replay. Evidence:
  `build/native-desktop-installed-offline-b1ccf3dc2ced48108aebc71c19fddbab/stop-report.json`.

No GUI permission or credential controls were operated. The fixture state is
isolated and retained locally; it is not the user's history or a real-model test.
The normal installed app was then reopened. Its preserved project, saved-chat
entry and Sarvam GLM 5.3 composer selection were visually confirmed. No saved
conversation was opened or prompt sent; the clean normal window is left open.

No paid model requests, private-code uploads, personal-history uploads or remote
memory calls were made for this UI batch. Offline streams are explicitly labeled
as fixtures, not AI answers. Unfinished streamed output may not be persisted by
the native ledger; restored interrupted conversations say this honestly.

## Distribution boundaries

Local installation and source pushes are user-authorized. Installation backs up
the previous Desktop and same-version CLI, checks package hashes and unchanged
protected settings, and retains historical files. This UI batch does not change
CLI execution or silently migrate/replay older engine sessions.

The prior human GUI Deny, Docker-isolation and broader live-model public-release
gates are not promoted to passes by these UI checks. The reviewed imported
upstream fixture alert is not dismissed or tested for credential validity. The
pattern scanner cannot prove the absence of every possible secret. No new tag
or public release is created by this batch.
