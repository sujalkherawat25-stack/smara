# Smara Desktop as an agent workforce: audit and research (18 Nov 2026)

Scope: what Smara Desktop already does on the owner's PC, measured against the target
("an army of agent employees on the owner's own PC, under strict control and audit"),
and what outside products and open-source stacks can do.

Read from `origin/main` at `fb21bd33` (beta-1.15 era). Baseline tests run on this PC:
`tests/test_native_computer.py`, `test_native_browser.py`, `test_native_browser_upgrade.py`,
`test_native_agents.py`: 56 passed.

## 1. How Smara Desktop is put together today

```
Tauri shell (apps/desktop, React UI NativeApp.tsx)
  ├─ smara-native  = Smara fork of the OpenAI Codex CLI (vendor/codex, Apache-2.0, commit 14c8b77)
  │    JSON-RPC thread/turn protocol, approvals, sandbox, MCP client, sub-agents
  │    └─ MCP servers configured per chat by src/smara/native_runtime.py:
  │         smara_readers   search/fetch/clock/memory reads
  │         playwright      Microsoft Playwright MCP 0.0.84 (chat browser, persistent profile)
  │         smara_browser   Smara's own DOM browser (fallback when Node.js is missing)
  │         smara_computer  Windows computer use (UIA + SendInput, Excel COM, Tally read)
  └─ smara_bridge.py (helper process, started at login)
       polls the hosted server (/bridge/poll) and runs browser steps / Smara Shared folder
       tools for the hosted Telegram agent, in a dedicated Brave/Chrome/Edge profile
```

Two separate "agent brains" therefore drive this PC:
* **Desktop chat** (local model via the Codex fork) uses Playwright MCP + `smara_computer`.
* **Hosted Smara** (Telegram) uses the bridge helper. Its approvals happen in Telegram;
  policy gates are server-side in `server/src/agent/policy.py`.

## 2. Audit against the target list

Legend: **works** (built and tested), **half** (partly built or not wired to the UI),
**missing**, **broken / conflicts**.

### 2.1 Browser automation on the owner's PC

| Item | Status | Evidence / notes |
|---|---|---|
| Navigate, read (accessibility snapshot with refs), click, type, select, keys, hover, drag | works | Playwright MCP tools in `native_playwright.py`; hosted bridge `ACTIONS` in `smara_bridge.py:785` |
| Re-observe after every action, ref-only actions, change diff | works (bridge), works (Playwright MCP returns a fresh snapshot) | browser v2 redesign note |
| Multi-tab | half | Playwright MCP has `browser_tabs`. The hosted bridge only follows a new tab (`follow_new_tab`); no list/switch/close-tab action |
| Downloads with approval | half | Native fallback browser has `browser_download` behind opt-in + prompt. Playwright MCP saves downloads to its output folder **without** any prompt or record. Hosted bridge: none |
| Uploads with approval | half | Playwright `browser_file_upload` prompts; native fallback prompts. Hosted bridge: none (cannot attach a file the owner sent on Telegram) |
| Owner-only sign-in, never types passwords/OTP/card | works | `secret_field()` guard in bridge; `browser_handoff` in native browser; `SENSITIVE_PAGE` gate on server. Playwright MCP has **no** secret-field guard of its own: approval prompt is the only check |
| CAPTCHA solved by owner, never bypassed | works by policy | handoff only; nothing solves CAPTCHAs |
| Persistent sign-in (owner logs in once) | works | Playwright profile at `<native home>/playwright-profile`; bridge uses a dedicated profile |
| Approvals for outward steps | works but coarse | Desktop chat: **every** click/type prompts (Playwright `ASK_FIRST`), including harmless "Add to cart"/search. Hosted: `OUTWARD_STEP`/`SENSITIVE_PAGE` regexes, good |
| Private / localhost addresses blocked | works | `--blocked-origins`, `url_allowed()` |
| Untrusted page text treated as data | works (prompt framing + policy) | |
| Per-step audit log kept locally | **missing** | the bridge writes one text line per step to `bridge.log` (no values, rotated at 1 MB) and has a Pause flag file; nothing structured, no screenshots, nothing for Desktop chat (Playwright MCP writes nothing Smara reads) |

### 2.2 Computer use (Tally, Excel, File Explorer)

| Item | Status | Evidence / notes |
|---|---|---|
| Window list, launch fixed apps, observe (UIA tree + screenshot), click/type/keys/scroll/drag/set_value | works | `native_computer.py`, `native_windows.py` (PR from session "real-computer-use") |
| Excel live via COM, Tally read via XML/HTTP | works (Tally only against a fake server) | `native_office.py`, `native_tally.py` |
| Never drive terminals/UAC/password managers, never type secrets, Luhn card check | works | design note §3 |
| Approval for send/pay/submit/delete | works (label heuristics) | `computer_act_confirmed`; known gap: blind coordinate clicks on custom-drawn Tally screens |
| Visible "agent is controlling" indicator | **missing** | nothing on screen while the agent moves the mouse |
| Instant Stop / Pause | half | Stop = revoke the grant in Settings (checked per call and every 40 typed chars). No Pause, no global hotkey, no Stop on screen |
| Per-app allow-list | half | grant is "one window" or "any app for 30 min". No saved list of allowed apps |
| Screenshots + action log per step, kept, viewable, deletable | **missing** | only the last 15 PNGs are kept, no action log, no viewer |

### 2.3 Multi-agent control

| Item | Status | Notes |
|---|---|---|
| Several tasks in parallel | half | Codex fork has sub-agents (`multi_agent_v2`, coding worktree workers). Desktop "agents" (Kiran, Rohan, Scout, Nisha in `native_agents.py`) are **personas for a chat**, not parallel runs |
| Task board (status, current step, approve/stop each, logs) | **missing** | goals view exists (`NativeGoals.tsx`) for background goals only |
| One mouse/keyboard controller at a time | **missing** | two chats with a desktop grant could both drive input; the hosted bridge browser and a desktop chat browser are separate profiles so they do not collide |
| Limits | half | per-grant 400 actions / 30 min; browser 50 actions / 600 s; no global concurrency cap |

### 2.4 UI

| Item | Status | Notes |
|---|---|---|
| Approvals inbox | half | in-chat approval cards only, per conversation. Hosted (Telegram) approvals are not shown; server endpoint planned (`server-approvals-endpoint-plan.md`), not built |
| Audit log viewer | missing | |
| Allowed sites/apps settings | half | "Browse any public website" switch; computer grant buttons; no lists |
| Memory sharing toggle | conflicts | Desktop memory is the owner's own Syntarus key and is **always on by design** (PR #3). Not linked to the hosted shop memory at all |
| Hosted link status | works | `hostedStatus.ts`, Browser tab (pair / on / off) |
| Legacy UI weight | note | `App.tsx` (5,923 lines, old executor UI incl. "GAIA audit") is not mounted (`main.tsx` imports `NativeApp`), dead code |

### 2.5 Memory sharing with hosted Smara (Continuum)

**Missing.** Hosted memory scopes are `<tenant>_<party>` / `<tenant>_shop`; Desktop has no
tenant link to them. Needs a server endpoint (device-token scoped, read-mostly) and an
opt-in, revocable toggle. No secrets, vault values or credentials may be sent.

### 2.6 Broken or risky right now

1. Playwright MCP downloads land on disk with no approval and no record.
2. Every Playwright click asks, so owners learn to approve without reading (approval fatigue
   weakens the gate for the steps that matter). Hosted policy already has the right split.
3. No on-screen indicator during computer use: the owner can not tell that the agent, not
   they, is moving the mouse.
4. No local audit trail of what the agent did on the PC.

## 3. Research

### 3.1 xAI Grok agent products (as of Nov 2026)

Sources: [xAI Grok Bot docs](https://docs.x.ai/grok-bot) (pages did not load for direct
reading; facts below are from the search snippets of those docs and the secondary write-ups
listed), [Vellum breakdown](https://www.vellum.ai/blog/official-grok-bot-breakdown),
[eesel review](https://www.eesel.ai/blog/grok-bot-review),
[Composio guide](https://composio.dev/blog/guide-to-frok-bot),
[Flavio Copes deep dive](https://flaviocopes.com/grok-bot/),
[Grok Automations, ai-tldr](https://ai-tldr.dev/releases/xai-grok-automations/),
[Grok 4 Heavy, DataCamp](https://www.datacamp.com/blog/grok-4),
[multi-agent guide](https://techjacksolutions.com/ai-tools/grok/grok-multi-agent-architecture/).

* **Grok Bot** (beta since 11 Aug 2026): each account gets one persistent **cloud** VM with a
  browser, file system, terminal, connectors and MCP. It works on sites with no API, keeps
  working when the user's laptop is closed, runs routines on a schedule or on events
  (Slack/GitHub/webhook). All Bots on an account share one computer (cookies, files,
  credentials), and the docs say it is not a security boundary between Bots.
* **Automations** (formerly Tasks, renamed July 2026): scheduled runs (once/daily/weekly…)
  for everyone; email triggers on SuperGrok.
* **Grok 4 Heavy**: several agents work on one problem in parallel (reported 4 by default,
  up to 16 in later releases), SuperGrok Heavy tier.
* Voice mode and DeepSearch exist in the Grok apps (not re-verified in this pass).
* Not found: control of the **user's own PC** or desktop apps; per-step audit for the user.

| Capability | Grok (cloud) | Smara Desktop now | Smara Desktop planned |
|---|---|---|---|
| Browser tasks on real sites | yes, cloud browser | yes, owner's PC (Playwright MCP + bridge) | + multi-tab, downloads/uploads with approval, step log |
| Logins | in the cloud VM | stay on the owner's PC, owner types them | same |
| Desktop apps (Tally, Excel) | no (cloud Linux VM) | yes (UIA/SendInput, Excel COM, Tally read) | + indicator, pause, per-app list, audit |
| Parallel agents | yes (Heavy, many Bots) | coding workers only | task board, N agents, one input controller |
| Scheduled tasks | yes (Automations, routines) | background goals; hosted weekly review | tasks on the board can be scheduled (later) |
| Research | yes | yes (search/fetch readers) | same |
| File work | yes | yes (workspace, PDF, Excel) | same |
| Voice | yes | voice notes (hosted), voice in Desktop | same |
| Approvals before outward steps | partial | yes | one approvals inbox incl. hosted |
| Owner-viewable audit | not found | no | local, viewable, deletable |
| Isolation between agents | none (docs say so) | n/a | per-task profile + single input lock |

Smara's edge is the owner's own PC (Tally, Excel, Indian govt portals where the owner is
already signed in), strict approvals and a local audit trail. Grok's edge is "keeps running
when the PC is off".

### 3.2 Computer-use code: what can be copied

| Stack | Licence | What it is | Verdict |
|---|---|---|---|
| OpenAI Codex CLI (`openai/codex`) | Apache-2.0 ([README](https://github.com/openai/codex/blob/main/README.md)) | Already vendored as the Desktop runtime. Contains only the **config/policy schema** for computer use (`config/src/computer_use.rs`: per-app allow/deny by Windows AUMID or exe publisher/product) and `computer_use` confirmation policies passed to the MCP call | Already in use; we follow its allow-list shape |
| Codex app "computer use" plugin | proprietary | Ships inside the Codex desktop app (`~/.codex/plugins/cache/openai-bundled/computer-use`, no licence file); the Codex app is proprietary ([Wikipedia](https://en.wikipedia.org/wiki/OpenAI_Codex)) | **Do not copy.** Only its public behaviour was studied (design note) |
| Cua / Cua Driver (`trycua/cua`) | MIT ([repo](https://github.com/trycua/cua), [cua.ai](https://cua.ai/)); some extras AGPL/CC-BY | Windows UIA driver used by Codex's plugin, MCP tools, agent cursor overlay | Good reference; could be an optional backend later. Not needed for M1/M2 |
| Microsoft UFO2 (`microsoft/UFO`) | MIT ([repo](https://github.com/microsoft/ufo)) | Windows AgentOS: UIA + vision, pywinauto | Reference for app-specific agents; heavy |
| pywinauto | BSD-3 (since 0.6) ([repo](https://github.com/pywinauto/pywinauto)) | UIA/Win32 automation | Our `native_windows.py` already covers what we need with ctypes/UIA |
| Playwright MCP (`microsoft/playwright-mcp`) | Apache-2.0 | Accessibility-snapshot browser tools | **Keep** as the Desktop chat browser (already used, pinned 0.0.84, run via npx, not copied) |
| browser-use (`browser-use/browser-use`) | MIT ([docs](https://docs.browser-use.com/development/telemetry), PyPI) | Python agent loop over Playwright | Not adopted: we already have the same ideas (indexed elements, re-observe) in our own engine; it would add a second agent loop and its own LLM layer |
| iFurySt/open-computer-use | MIT ([repo](https://github.com/ifuryst/open-codex-computer-use)) | Third-party re-implementation of Codex computer use as MCP | Reference only |

Decision: **no new third-party code is copied in M1**. Smara keeps its own Python engine
(bridge v2 + `native_computer`), Playwright MCP stays a pinned external dependency, and the
Codex fork's licence/NOTICE files stay in place. If code is later copied from Cua or UFO, it
goes under `third_party/<name>/` with its LICENSE and a line in `THIRD_PARTY_NOTICES.md`.

## 4. Build plan (milestones, one PR each)

1. **Browser (M1)**: one local **audit log** (`agent_audit.py`: JSONL per day + screenshots,
   viewable, deletable) used by every PC action; Playwright MCP downloads gated and recorded;
   tab list/switch/close, download and upload in the hosted bridge with approvals on the
   server; the bridge's per-step log (fills the Activity feed). Policy: hosted gates extended
   for `download`/`upload`/`tab`.
2. **Computer use (M2)**: on-screen "Smara is controlling" overlay with Stop/Pause, global
   hotkey, pause flag honoured mid-action, per-app allow-list, audit of every step.
3. **Multi-agent (M3)**: task board, N parallel tasks, a single input lock.
4. **UI (M4)**: task board, approvals inbox (incl. hosted approvals once the server endpoint
   ships), audit viewer, allowed sites/apps, link status.
5. **Memory sharing (M5)**: opt-in per shop, revocable, secret-scrubbed.

## 5. Milestone 1 (this PR): what changed

* **Local audit log** (`src/smara/agent_audit.py`): one JSON line per step in
  `%APPDATA%\Smara\audit\<day>.<desktop|bridge>.jsonl`, screenshots in `shots/<day>/`, 30 days kept,
  secrets/card numbers/secret-named fields scrubbed before writing. Settings operation `audit`
  (days / list / shot / delete a day / delete everything).
* **Desktop chat**: the runtime bootstrap watches the JSON-RPC stream read-only and records every MCP
  tool call (Playwright browser, Smara browser, computer use, other tools) and every approval answer
  (approved / declined). Files that appear in the chat browser's download folder after an approved
  step are recorded as `download_saved`.
* **PC helper (bridge)**: new steps `tabs`, `tab`, `close_tab`, `download` (kept in Smara Shared/Downloads,
  programs and scripts refused, 50 MB cap, never overwrites) and `upload` (one file from the Smara Shared
  folder, 20 MB cap). Downloads/uploads run only in the owner's PC browser, never in the server browser.
  Every step (and every refused-while-paused step) goes into the audit log, with a JPEG screenshot after
  acting steps.
* **Server policy**: `download`/`upload` always need the owner's tap (even on their own PC and even with a
  site grant), are refused for counterparty sessions and in the server browser; `tabs`/`tab` are read-only;
  `close_tab` acts like a click. `browser_step` schema and description list the new steps.
* **UI**: Settings → Activity log (day picker, filter by kind, search, screenshot per step, delete a day,
  delete everything; both deletes ask first).

Not in this PR: Playwright MCP cannot be told to refuse downloads, so in Desktop chat a download still
happens on an approved click without a separate download prompt (it is now recorded). Computer-use
screenshots go to the audit log in M2.

## 6. Milestone 2: computer use under the owner's control

* **The Smara bar.** While any app-control grant is live, the Desktop app shows a small always-on-top
  bar at the top of the screen. It reads "Smara is controlling your PC", shows the last step (for example
  "typing in Excel") or when the grant ends, and has **Pause/Resume** and **Stop**. It does not take focus
  when it appears and it closes when the grant ends or is stopped (`src-tauri/src/control_bar.rs`, `ControlBar.tsx`).
* **Ctrl+Alt+Esc** stops computer control from anywhere (Win32 `RegisterHotKey`; the bar shows the key only
  when registration worked).
* **One shared control file** (`computer-control.json`, `smara.computer_control`). Stop revokes every grant made
  before it (all projects). Pause holds every agent. A damaged file counts as paused. The computer server checks
  it before every step, every few pointer-movement steps and every 5 typed characters, so Pause/Stop cut into a
  running click or typing (the step reports "Outcome may be partial").
* **Allowed apps.** Settings → Browser & computer has a list of apps Smara may control (default: Excel, Tally,
  File Explorer, Notepad, Calculator). "Allow my apps for 30 minutes" grants only those. "Any app" is a separate,
  more strongly worded button. The never-driven list (terminals, security dialogs, password managers, Smara
  itself) still applies to both.
* **Audit.** Every computer step (observe, act, launch, Excel, Tally, refusals) is written to the activity log
  with the window's screenshot after the step, typed text scrubbed. Screenshots stop being kept after 500 MB
  in one day (the steps are still logged).
* Approval before outward/irreversible steps is unchanged (`computer_act_confirmed`, `excel_save`).

Live check on this PC (debug build, throwaway data folder): the bar appeared within half a second of a grant, was
topmost and did not take focus; a real click on Pause wrote `paused: true, by: bar`; Ctrl+Alt+Esc wrote Stop
(`by: hotkey`) and the bar closed.
