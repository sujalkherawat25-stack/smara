# Smara computer use vs Codex computer use (design note)

Scope: `src/smara/native_computer.py` (policy + MCP server), `native_windows.py` (Windows mechanism),
`native_office.py` (Excel COM), `native_tally.py` (Tally HTTP, read-only).

## 1. How Codex does it (read on this PC)

Sources: `~/.codex/plugins/cache/openai-bundled/computer-use/26.1029.61003` (`SKILL.md`, `docs/api.md`,
`guidance.md`, `confirmations.md`) and the Cua driver 0.20.0 (`~/.cua-driver/packages/current`, `cua-driver list-tools`).
The `cua-driver-uia.exe` binary is not executable from this shell (permission denied); nothing was run from it.

| Aspect | Codex plugin (`sky` API) | Cua driver (separate product) |
|---|---|---|
| Tools | `list_apps, list_windows, get_window, launch_app, get_window_state, click, press_key, type_text, scroll, set_value, drag, perform_secondary_action, activate_window` | ~60 tools: per-pid `click/type_text/press_key` via `PostMessage`, `get_window_state` (UIA tree), `invoke_menu`, sessions, recording, agent cursor overlay, browser_* via CDP |
| Window selection | Opaque numeric window ids returned by `list_apps/list_windows`; never reconstructed. Exactly one window must match before acting | pid + window_id |
| Observation | `get_window_state` = screenshot (default) and/or accessibility text with element indexes, `focused_element`, `selected_text`, `document_text`. Works on occluded windows (Windows.Graphics.Capture) | structured `elements` + markdown tree + screenshot |
| Input | `SendInput`; every input call activates its target window itself; point-over-wrong-window check, one re-activate retry | background `PostMessage(WM_CHAR/KEYDOWN)` without foregrounding |
| Loop discipline | One action, then immediately refresh state; indexes/screenshot ids/coordinates valid for that observation only; outcome unknown after failure, so re-observe before retry | n/a |
| Safety | Hard denies: terminals, Run dialog, auth dialogs, password managers, security apps, Windows key, other agent UIs. Confirmation taxonomy: always-confirm for delete, send/communicate, financial, account/permission changes, installs; pre-approval for upload/login etc.; nothing needed for routine navigation/typing | permission modes, session revoke |

## 2. What Smara had before (the "unusable" part)

* Two tools: `computer_observe`, `computer_act` (`invoke` / `set_value` only).
* Window picked in Settings from a **hard allowlist** (Notepad/Paint/Calculator in Windows dirs).
* **No activation**: after a native approval the human had 10 s to bring the app forward; foreground-only.
* Actions were UIA patterns only. No pointer, no key events, no scroll, no drag, no coordinates.
* **Every call** (observe and act) prompted for approval; 40 actions / 15 min; whole-screen fingerprint had to match.
* 2000-element ceiling made Excel-class windows fail outright.

## 3. What this branch changes (smallest set that closes the gap)

Model-facing tools (MCP `smara-computer`):

| Tool | Approval | Notes |
|---|---|---|
| `computer_windows` | auto | list eligible windows (id, app, title) |
| `computer_launch` | auto | fixed set only: notepad, excel, calc, tally, paint (no arbitrary exe) |
| `computer_observe` | auto | **activates the window itself**; numbered UIA tree (BFS, 400 elements, truncated flag, never fails on big apps) + focused element + local PNG; `PrintWindow` with screen-crop fallback for GPU windows |
| `computer_act` | auto | click / double_click / right_click (element index or window coords), type_text, press_key chords, scroll, drag, set_value. Real pointer travel (14 interpolated absolute moves), real key events (VK + scan code; Unicode packets only for characters with no key, e.g. ₹) |
| `computer_act_confirmed` | **prompt** | same arguments; required for critical steps |
| `excel_live` | auto | COM: list/open (workspace only, macros disabled)/read/write/formula_check; writes stay unsaved |
| `excel_save` | **prompt** | persists a file |
| `tally_read` | auto | read-only XML/HTTP, loopback only |

Policy (server side, independent of the backend, unit-tested with a fake):

* **Grant**: per workspace, expiry (30 min default, max 120), 400-action allowance, scope `desktop` (any non-denied window) or `window` (one process). Settings has "Grant any app for 30 minutes" and the existing **Stop** button (still revokes instantly; every tool re-reads the grant, typing re-checks it every 40 characters).
* **Never driven**: terminals, Run, UAC/credential/security UI, password managers, regedit/mmc/taskmgr, agent hosts, Smara itself; Windows key rejected.
* **Never typed**: focused field is `IsPassword` or named like password/OTP/PIN/CVV/card/secret/token; Luhn-valid card numbers anywhere in text. Not overridable by approval. Secret-looking strings (keys, `secret=`, 40+ char tokens) are redacted from tree text sent to the model.
* **Critical (needs `computer_act_confirmed`)**: clicking elements/dialogs whose label matches send, submit, pay, purchase, delete, remove, confirm, post, publish, accept, approve, install, upload, e-mail, etc.; Alt+F4/Ctrl+W/Ctrl+Q; Delete outside text fields; Enter on a critical default button; **Tally** Ctrl+A / Alt+D / Alt+X / Enter-or-Y while Accept/Delete/Cancel text is visible. Routine navigation, typing, scrolling, cell edits need no approval.
* **Observe-act-observe**: an observation id is valid for exactly one action; each action returns a fresh observation plus `effect.changed_ui_text`. Failure or uncertainty consumes the observation and an action, never auto-retried. Window moved or resized since observation is refused.

Limits (honest): critical detection is label heuristics on UIA text. A blind coordinate click on a button UIA doesn't expose (custom-drawn Tally screens) is only caught if the dialog *title* is critical. This is not an OS sandbox.

## 4. Tally: HTTP/XML vs driving the UI

TallyPrime serves XML requests on port 9000 (enable in F1 > Settings > Connectivity) and ODBC.

| | UI driving | XML/HTTP |
|---|---|---|
| Read ledgers/vouchers/reports | slow, needs focus, Tally is custom-drawn so UIA tree is poor | structured, deterministic, no keystrokes |
| Risk | a stray Enter/Ctrl+A accepts or deletes a voucher | export requests cannot modify books |
| Write | only route for some interactive screens | Import (`TALLYMESSAGE`) works but changes books irrevocably and Tally reports errors per-message |

Decision: **read path through HTTP is implemented** (`tally_read`: companies, ledgers, vouchers by date/type, trial balance, day book). Write path is *not* implemented. Proposal for phase 2: a `tally_import_preview` that renders the XML + diff, then an `tally_import` tool with `prompt` approval, one voucher per call, create-only (never Alter/Delete), then read back and compare. UI driving remains the fallback for screens HTTP cannot reach, with Accept/Delete gated as above.

## 5. Excel

Existing file tools cover offline read/create. Live control is COM (not UI clicks): open a workspace workbook with macros disabled, read ranges (values + formulas), write cells/formulas (external-data/exec functions refused), `formula_check` recalculates, lists `#REF!`-type errors and compares expected values. UI-level `computer_*` still works on Excel for anything COM cannot do (verified key entry).

## 6. Not done / follow-ups

* `tally_read` is verified only against a fake server producing Tally-style XML. Never run against real Tally (not running here).
* Tally write path, ODBC.
* No vision loop: screenshots are saved locally; `share_screenshots` grant flag returns the PNG inline to the model but Settings has no toggle yet.
* Codex-style `perform_secondary_action`, menu invoke and `list_apps` for not-running installed apps.
* Runtime `approve` mode string for `default_tools_approval_mode` follows Codex's `AppToolApproval` (`auto|prompt|approve`); not exercised against the bundled Codex binary in this session.
