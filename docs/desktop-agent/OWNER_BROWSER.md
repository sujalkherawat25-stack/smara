# Smara in the owner's own browser

Owners already sign in to Gmail, LinkedIn, the GST portal and their bank in Brave, Chrome or Edge. Signing in again inside
Smara's own browser is friction, and copying cookies out of a profile is fragile and unsafe. Since Chromium 136 a browser also
refuses outside automation of its real profile (`--remote-debugging-port` is ignored for the default user data folder).

So Smara works from inside the owner's browser, through the **Smara extension** (added once, Settings > Browser > "Use my own
browser").

## What the owner sees

- Smara opens its own tabs in a purple **"Smara"** tab group and works there in plain sight. The browser shows its own bar
  saying an extension is debugging it.
- Smara never reads or controls the owner's other tabs. A tab a Smara tab opens (a popup or a `target=_blank` link) joins the
  task. When the task ends, its tabs stay open so the owner can see where Smara stopped.
- The extension's toolbar button shows how many tabs Smara is working in, and **Pause Smara in this browser** stops it at once.
- If the browser is closed, Smara opens it. If the extension does not answer within about 25 seconds, the task continues in
  Smara's own browser and tells the model and owner why.

## How it works

```
model -> smara_browser MCP (NativeBrowser) -> ManagedBrowser -> Playwright.connect_over_cdp(ws://127.0.0.1:<port>/cdp/<token>)
                                                                     |
                                                     owner_browser.Relay (in the helper)
                                                                     |  ws://127.0.0.1:<port>/extension/<token>
                                         Smara extension (service worker) -- chrome.debugger --> Smara's tabs only
                                                     ^
                     native messaging host (smara-desktop.exe, started by the browser): "these relays exist"
```

- Each browser helper runs a `Relay`, announced in `%APPDATA%\Smara\browser-relays\<id>.json` (port and random token).
- The browser starts Smara's native messaging host (`com.smara.desktop`, registered under HKCU for Brave, Chrome, Edge and
  Chromium) when the extension loads. The host pushes the relay list to the extension and writes a heartbeat
  (`browser-extension.json`) that Settings uses to show "Connected".
- The relay answers browser-wide CDP commands itself (`Target.createTarget` opens a Smara tab through the extension) and
  forwards page commands to the extension, which runs them with `chrome.debugger` on that tab only.
- Everything above `ManagedBrowser` is unchanged: origin checks, the request filter that blocks page writes outside approved
  actions, approvals for every click or form, saved site logins, OTP fill and hand-off.

## Safety

- Only the extension with Smara's fixed id (`kgbeddkmlpkbokpnifggegjhehlcmkan`, from the manifest's public key) may start the
  host (`allowed_origins`). Relay files are in the owner's own profile folder. Each relay needs its token.
- The extension checks every command against the tabs it opened for that task. One task works in one browser: if the
  extension is in two browsers, the first to answer gets the task and the other is turned away.
- The extension has no host permissions and no content scripts, so it cannot read any page by itself.
- The agent still never types passwords (password fields are refused, and saved logins are typed by the PC), OTPs only come
  from the owner, and money moves only by the owner.
- Downloads in the owner's browser go to that browser's Downloads folder. `browser_download` is refused there, and the model
  is told to click with approval and say so.

## Smara's own browser is shown by default

`browser_control` options default to `visible: true`, so the owner can watch Smara's own browser too. Tests and other callers
that pass no options still run hidden. Reusing the starting blank tab (instead of closing it) keeps a visible Brave window
able to open tabs.
