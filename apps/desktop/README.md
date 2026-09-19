# Smara Desktop

Smara Desktop is the Windows-native local companion for Smara. It is a local
first Tauri shell around the bundled `smara-desktop` executor:

- This PC owns browser sessions, files, model connections, terminal access,
  and local artifacts.
- Nothing runs because a chat message merely asked for it. Local tasks still
  pass the same capability and approval gates before execution.
- Hosted pairing is explicit opt-in; a fresh install never contacts a legacy
  hosted endpoint or reuses a legacy beta token.

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

For local chat, add a private model under Settings → Models. Hosted sign-in and
pairing are optional and require an operator-supplied API URL; the local
runtime does not need an account or hosted token. Start with only an approved
folder and add terminal/browser allowlists only when needed.

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

## Optional hosted provider/model profiles

The local model picker stores only encrypted credentials on this PC. If an
operator explicitly switches to hosted mode, the API and Web URLs are supplied
by that operator; no public Syntarus URL is hard-coded into a new install.

## Private desktop model providers

Settings → Model provider → **Add provider** can store a Sarvam 105B, Sarvam
GLM, Grok, or custom OpenAI-compatible endpoint for direct chat from this PC.
The Sarvam presets use `https://api.sarvam.ai/v2` and the
`api-subscription-key` header: Sarvam 105B uses `sarvam-105b`, while the GLM
preset uses the current `glm5.3` model. Sarvam V2 GLM access is beta-gated;
the key must be enabled for V2 by Sarvam. Grok is pre-filled with the xAI
endpoint and Bearer authentication. The key is encrypted in the
Windows-account credential vault and is read only by the native desktop when a
private chat is started. It never travels to a hosted API. Local task planning,
research, approvals, and task history stay on this PC; a hosted profile is
never selected implicitly.

## Personal local tool credentials

Settings can save tool keys such as `TAVILY_API_KEY` or `GITHUB_TOKEN` on this
PC. On Windows, values are encrypted with DPAPI for the signed-in Windows
account and the UI only lists the alias. An approved `local_terminal` payload
may request selected aliases using `credential_env`; only that child process
receives them, and any value echoed by the process is redacted before output
is returned to the local task record. Provider secrets are encrypted locally
and are not uploaded to a VM implicitly.

## Verification

```powershell
npm run build
Push-Location src-tauri; cargo check; Pop-Location
Push-Location ../..; .\.venv\Scripts\python.exe -m pytest -q; Pop-Location
```

The local Activity screen shows executor state, local task status, approvals,
expandable final task results, and the bounded local log. It refreshes while
visible. Cleanup stops any tracked process before removing its local state,
preventing a stale runner from polling an old endpoint.
