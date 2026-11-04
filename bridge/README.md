# Smara Browser Bridge

Lets Smara use **your own browser** (Brave first, then Chrome or Edge) on your PC. Sites see your home connection and
your logins, so shopping, listings and portals that block cloud servers work. Your passwords never leave your PC.

## Set up (once)

```
pip install playwright httpx
python munim_bridge.py pair --code ABCD-EFGH      # send "pair browser" to Smara on WhatsApp/Telegram to get the code
python munim_bridge.py run                        # keep this window open
```

`run` opens Brave (or Chrome/Edge) with a **dedicated Smara profile**. Log in to the sites you want Smara to use once;
the profile remembers them. To drive a browser you already have open instead, start it with
`--remote-debugging-port=9222` and run `python munim_bridge.py run --attach http://127.0.0.1:9222`.

## What you control

- Every page Smara works on shows a blue **"Smara is working on this page" banner with a Stop button**. Stop pauses
  Smara immediately; press Resume to continue.
- Smara asks you to approve clicks and typing in WhatsApp/Telegram. **Approve + this site** lets it work on that one
  site for 15 minutes; anything that pays, sends, submits, deletes or logs in still asks again.
- Smara never types passwords, OTPs, PINs or card numbers. The bridge refuses those fields itself.
- Pages on your own network (localhost, router, office servers) and `file://` pages are blocked.
- Send `unpair browser` in chat (or `python munim_bridge.py unpair`) to disconnect. `browser status` shows if it is online.

## How it connects

The bridge only connects **out** to the Smara server and long-polls for commands. Nothing on your PC is exposed. The
device token is stored in `~/.munim-bridge/config.json` and only a hash of it is kept on the server.

## Hosted remote browser (server side, optional)

Set `BROWSER_CDP_URL` in the server `.env` to a hosted browser provider's CDP endpoint and the server's browser runs
there instead of on the Oracle VM. The same safety checks apply. When a PC bridge is online it is used first.
