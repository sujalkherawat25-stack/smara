# Smara Desktop

Smara Desktop is the Windows-native local companion for Smara. It is a thin
Tauri shell around the existing outbound-only `smara-desktop` executor:

- Smara's hosted service remains the one agent brain, task graph, memory, and
  approval system.
- This PC owns browser sessions, files, terminal access, and local artifacts.
- Nothing local runs because a chat message merely asked for it. The hosted
  task must be leased to this paired device and pass the approval gate first.

## Run from the repository

From `smara/apps/desktop`:

```powershell
npm install
$env:SMARA_REPO_ROOT = (Resolve-Path ../..).Path
npm run tauri dev
```

The desktop shell finds the Python executor at
`$env:SMARA_REPO_ROOT\.venv\Scripts\smara-desktop.exe`. You can point to an
explicit executable instead with `$env:SMARA_DESKTOP_EXECUTABLE`.

Before chat/task history can load, choose **Sign in** in the app's Settings.
The app opens the hosted Smara approval page at `/smara/` and stores the
short-lived device token in the local Smara profile. For headless/CLI workflows, the equivalent
command remains:

```powershell
smara --api https://ai.syntarus.com/smara-api login
```

The CLI token is read from `%APPDATA%\Smara\token.json`; the desktop never
shows or sends it to the UI. Pairing is completed from Smara Web's Desktop
settings, then the one-time code is pasted into the app. Start with only an
approved folder and add terminal/browser allowlists only when needed.

## Build the Windows package

```powershell
..\..\scripts\build-smara-desktop.ps1
```

The release artifacts are written under `src-tauri\target\release\bundle\`
(NSIS installer for the beta). The build script creates a PyInstaller standalone
executor and embeds it as an installer resource, so the packaged app does not
need the repository or a Python installation to run approved local work. A
publisher certificate and signed auto-update channel are still production
hardening tasks; the app intentionally does not silently download code or
dependencies. The NSIS installer creates a `Smara Desktop.lnk` shortcut on the
Windows Desktop and removes that shortcut on uninstall. MSI remains an optional
operator build when WiX is available.

## Hosted provider/model profiles

The model picker exposes `Automatic`, `Grok`, and `Sarvam`. Provider URLs and
API keys stay in the Smara server configuration; never commit an xAI or Sarvam
key. The app forwards only the selected profile name to hosted Smara. The
settings screen also shows whether each local tool credential is configured and
how many file, terminal, and browser allowlist entries are currently enabled.
The Web URL is configured separately from the reverse-proxied API URL so sign
in opens `https://ai.syntarus.com/smara/?cli_device=...`, never the API fallback UI.

## Private desktop model providers

Settings → Model provider → **Add provider** can store a Sarvam, Grok, or
custom OpenAI-compatible endpoint for direct chat from this PC. Sarvam is
pre-filled with `https://api.sarvam.ai/v1/chat/completions`, model
`sarvam-105b`, and the `api-subscription-key` header; Grok is pre-filled with
the xAI endpoint and Bearer authentication. The key is encrypted in the
Windows-account credential vault and is read only by the native desktop when a
private chat is started. It never travels to Smara's hosted API. Hosted task
planning, research, approvals, and task history continue to use the hosted
profile, so choosing a private model is clearly a chat-only local mode.

## Personal local tool credentials

Settings can save tool keys such as `TAVILY_API_KEY` or `GITHUB_TOKEN` on this
PC. On Windows, values are encrypted with DPAPI for the signed-in Windows
account and the UI only lists the alias. An approved `local_terminal` payload
may request selected aliases using `credential_env`; only that child process
receives them, and any value echoed by the process is redacted before output
is returned to hosted Smara. Provider secrets for hosted Grok/Sarvam are not
stored here, and local personal tool keys are not uploaded to the VM.

## Verification

```powershell
npm run build
Push-Location src-tauri; cargo check; Pop-Location
Push-Location ../..; .\.venv\Scripts\python.exe -m pytest -q; Pop-Location
```

The local Activity screen shows executor state, hosted task status, approvals,
expandable final task results, and the bounded local log. It refreshes while
visible, and an expired hosted sign-in is cleared rather than appearing as a
false connected session. Revoke stops the tracked process before invalidating
the server token, preventing a stale runner from polling during revocation.
