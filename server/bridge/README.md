# Smara Browser Bridge

Lets Smara use **your own browser** (Brave first, then Chrome or Edge) on your PC. Sites see your home connection and
your logins, so shopping, listings and portals that block cloud servers work. Your passwords never leave your PC.

## Set up (once)

```
pip install playwright httpx
python smara_bridge.py pair --code ABCD-EFGH      # send "pair browser" to Smara on WhatsApp/Telegram to get the code
python smara_bridge.py run                        # keep this window open
```

`run` opens Brave (or Chrome/Edge) with a **dedicated Smara profile**. Log in to the sites you want Smara to use once;
the profile remembers them. To drive a browser you already have open instead, start it with
`--remote-debugging-port=9222` and run `python smara_bridge.py run --attach http://127.0.0.1:9222`.

## What you control

- Every page Smara works on shows a blue **"Smara is working on this page" banner with a Stop button**. Stop pauses
  Smara immediately; press Resume to continue.
- Smara asks you to approve clicks and typing in WhatsApp/Telegram. **Approve + this site** lets it work on that one
  site for 15 minutes; anything that pays, sends, submits, deletes or logs in still asks again.
- Smara never types passwords, OTPs, PINs or card numbers. The bridge refuses those fields itself.
- Pages on your own network (localhost, router, office servers) and `file://` pages are blocked.
- Send `unpair browser` in chat (or `python smara_bridge.py unpair`) to disconnect. `browser status` shows if it is online.

## How Smara sees and acts on a page

The same way the leading browser agents do (Playwright MCP, Claude in Chrome, browser-use), with no rules for any
particular site:

- Every control a person could use is listed with a number, its role, its name and its state, across the whole page
  (an open dialog first, frames and shadow DOM included): `[31] button "Add to cart"`, `[12] searchbox "Search" value=""`,
  `[40] button "Buy now" disabled`.
- Smara acts only by those numbers. A guessed selector is refused and the fresh page is sent back instead.
- After every step Smara is told exactly what changed, for example
  `changed: [31] button "Add to cart" -> [31] button "Go to cart"`, new or gone controls, a dialog that opened, or
  the site's own message ("Added to your cart"). If nothing changed it is told so, and does not repeat the step.
- The pointer moves and keys are typed like a person's. Nothing hides that the browser is automated.

## How it connects

The bridge only connects **out** to the Smara server and long-polls for commands. Nothing on your PC is exposed. The
device token is stored in `~/.smara-bridge/config.json` and only a hash of it is kept on the server.

## Hosted remote browser (server side, optional)

Set `BROWSER_CDP_URL` in the server `.env` to a hosted browser provider's CDP endpoint and the server's browser runs
there instead of on the Oracle VM. The same safety checks apply. When a PC bridge is online it is used first.
