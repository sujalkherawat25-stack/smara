# Persistent goals and Telegram

Goals use the same native Rust runtime as chat. One background supervisor opens
or resumes a saved native session for each bounded cycle. Desktop, CLI and
Telegram read and control one SQLite ledger under Smara's native runtime home.

## Desktop

Open **Goals**, choose **New goal**, describe the outcome and success criteria,
then queue it. Read-only access is the default; opt into project editing for
coding tasks. Project readers, coding workers and browser origins are captured
from the project's saved options. Select **Start background work** to enable
continuation. Closing the Desktop window does not stop this explicitly started
supervisor. The laptop must remain awake and online.

Review progress, evidence, output previews and native approvals in the goal
view. Feedback is retained and supplied at the next cycle. Pause/Stop interrupt
the active native turn; Resume waits until that turn has released its lease.
Model-reported success becomes **Ready for review**. Only **Accept result** marks
the goal completed. Coding checks require observed native test exits after edits.
Research checks require fetched pages and exact citation excerpts; mixed checks
require both. These are mechanical evidence gates, not a semantic correctness
score or exhaustive answer-coverage evaluation. Native turn completion alone
cannot complete a goal. Changed output files invalidate acceptance.

The supervisor pauses abandoned work after interruption. It preserves the
session and already reserved request/token usage. Resume is explicit. Failed
or malformed checkpoints block with a visible explanation. Two cycles with
unchanged recorded progress block further automatic continuation. Cumulative
cycle/request/token estimate limits apply across restarts. The token estimate
uses request UTF-8 bytes plus maximum output reservations, not billed tokens
or an INR price estimate.

Active foreground turns, shells and local Git write actions exclude goal cycles
in the same project. A background goal owns its project until native cleanup
releases the cycle lease. New work in other projects remains available.

## CLI

```powershell
smara goals add --workspace C:\Projects\Example --profile YOUR_PROFILE --objective "Inspect this project" --success-criteria "Provide observed findings and supporting evidence"
smara goals start
smara goals list
smara goals show GOAL_ID
smara goals feedback GOAL_ID "Also check the documented setup"
smara goals pause GOAL_ID
smara goals resume GOAL_ID
smara goals accept GOAL_ID
smara goals shutdown
```

Use `--sandbox workspace-write` for an explicitly authorized coding goal.
Use `--task-kind coding`, `research` or `mixed` to choose evidence checks;
general goals remain user-reviewed without domain-specific gates.
`smara goals extend GOAL_ID --max-cycles 12 --max-requests 50` updates cumulative
limits while the goal is idle. `smara goals run` runs the supervisor in the
foreground. All commands remain available without the retired Python engine.

## Telegram setup

1. Create a dedicated bot with Telegram's **@BotFather**.
2. In **Settings → Integrations → Telegram**, save the bot token, select the
   current project/model as defaults, and explicitly choose whether new remote
   goals can edit that project. The token is saved with Windows DPAPI.
3. Start background work and create a pairing code. Send `/link CODE` to the bot
   in a private chat within ten minutes. The code can be used only once.

CLI setup is also available:

```powershell
smara goals telegram key
smara goals telegram configure --workspace C:\Projects\Example --profile YOUR_PROFILE
smara goals start
smara goals telegram pair
smara goals telegram status
```

The key command prompts without echoing the token. Do not put the token in a
goal, chat, command argument, or repository file.

Telegram commands:

```text
/goal objective | success criteria
/goal research: objective | success criteria
/status
/status GOAL_ID
/feedback GOAL_ID additional information
/pause GOAL_ID
/resume GOAL_ID
/stop GOAL_ID
/result GOAL_ID
/result GOAL_ID 1
/project
/help
```

## Explicit voice input and output

Desktop's **Voice** button records locally for up to 25 seconds. **Transcribe
with Sarvam** uploads only that selected clip, then places the transcript in
the draft; nothing is sent to the chat automatically. **Discard** clears it.
Microphone permission is granted by the user in Windows/WebView. **Read aloud**
asks before sending response text to Sarvam and plays sequential chunks; Stop
prevents further requests. Changing chats cancels pending recording/playback.

`smara voice status`, `smara voice transcribe recording.wav`, and
`smara voice speak "Your chosen text" --out new-audio.wav` provide the same
adapter in CLI. Input supports WAV/WebM/Ogg, at most 2 MB; WAV duration is
validated (30 seconds). Desktop limits recording to 25 seconds; container
duration for externally supplied WebM/Ogg is enforced by Sarvam's REST endpoint.
Output is WAV. Existing output files are not overwritten.

The protected `SMARA_VOICE_API_KEY` alias takes priority, otherwise the active
Sarvam profile supplies the key. The older dedicated OCR key is not reused.
Speech billing is separate from chat estimates. No automatic retries occur.
This implementation pins the stable **Saaras v3 / Bulbul v3** contract, as
documented in [Sarvam STT](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe)
and [TTS](https://docs.sarvam.ai/api-reference/text-to-speech/convert).
Telegram remains text control; voice messages there are not enabled.

Goal ID prefixes must be unique. Plain text supplies feedback to the last
unfinished Telegram goal, or starts a new goal. The bot notifies the paired
owner of checkpoints, blockers, approvals and result readiness. `/result ID 1`
explicitly requests upload of the first recorded project output (up to 25 MB).
Approvals and final result acceptance are local Desktop actions.

The connector uses outbound long polling; no public listener or hosted account
is required. Only the exact paired numeric user and private chat can control
goals. Telegram update receipts, goal mutations, offsets and response queues
commit together. Duplicate updates cannot repeat a command. Old start/resume
requests queued while the laptop was offline require a fresh command. Edited
messages and group/channel commands do not execute. Delivery with a lost
response is recorded as uncertain and is not automatically repeated.

API references: [Telegram Bot API](https://core.telegram.org/bots/api) and
[polling FAQ](https://core.telegram.org/bots/faq#how-do-i-get-updates).

## Verification

`tests/test_native_goals.py` and `tests/test_native_telegram.py` exercise ledger
and authorization invariants. Telegram uses explicitly labeled HTTP fixtures;
they do not certify live delivery. `scripts/run_native_goals_acceptance.py`
uses an actual native executable with a local scripted provider to verify clock
execution, cold session continuation, feedback, cumulative reservations and a
scoped native Deny round trip. It also verifies active-cycle pause, approval
expiry, explicit resume into the same session, and background service startup
and shutdown. It is not a paid-model quality evaluation.

The source UI and CLI are wired, including the frozen-build module list. An
already installed older build will not gain these features until a new package
is built and installed; source verification is not installed-app verification.

Live Telegram setup requires a bot token and human pairing. A real work pilot
and research/coding outcome evaluations remain separate from these protocol
checks. The bounded synthetic voice/goal runner is
`scripts/check_goals_voice_live.py`; it persists request reservations before
sending and never silently re-runs a spent check. Speech has a separate one-job
reservation for each direction. Its report retains failures, not only passes.
