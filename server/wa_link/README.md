# Smara WhatsApp link (Baileys sidecar)

A small Node service that lets a shop send its customer reminders from **its own SIM**, through WhatsApp Web's
multi-device protocol, using [Baileys](https://github.com/WhiskeySockets/Baileys). It exists only until the official
WhatsApp Cloud API is available for the shop.

> **Unofficial.** This is not an approved WhatsApp integration. WhatsApp can restrict, limit or ban a number that
> behaves like this, and the number is lost with it. The owner accepted that risk. Do not use a number you cannot lose.

## What it does, and what it never does

- Holds one device per shop, pairs it by QR code, and reconnects it after a restart from the credentials the app stores.
- Sends **one text message to one number** when the Smara app asks. Nothing else: no templates, no media, no groups,
  no broadcast.
- Reports inbound text from customers back to the app, which runs the normal debtor flow (STOP, payment replies).
- **Never** writes files. Credentials are kept in memory here and stored, encrypted with the vault, by the Smara app.
- **Never** logs keys, QR codes, phone numbers or message text.
- **Never** sends on its own: the Smara app decides, after the owner has approved each message. The guardrails
  (owner approval, STOP, quiet hours, daily cap, pause between sends, pause on logout) are enforced in the app
  (`server/src/loop/wa_link.py`). This service also paces sends (`WA_LINK_SEND_GAP_MS`) as a second line of defence.

## Pinned versions

| Package | Version | Why |
|---|---|---|
| `@whiskeysockets/baileys` | **6.7.24** (exact) | Latest stable release. The 7.0.0 line is still release candidates (`7.0.0-rc*`); we do not run release candidates in production. |
| `pino` | 9.14.0 | Baileys' logger dependency, pinned to a known version. Logs are silenced. |
| `qrcode` | 1.5.4 | Renders the pairing QR as SVG for the owner's page. |

`package-lock.json` pins every transitive dependency. Baileys depends on `libsignal`, which npm fetches from a git
commit; the lock file records that exact commit (`bcea72df…`) and uses an HTTPS URL so `npm ci` works in CI and in
Docker. Check `package-lock.json` again whenever Baileys is upgraded.

## Interfaces

Internal HTTP API on port 8090 (no port is published; only the Smara app can reach it). Every call except `/healthz`
needs `Authorization: Bearer <WA_LINK_TOKEN>`.

| Method and path | Purpose |
|---|---|
| `GET /healthz` | Liveness (no token). |
| `GET /sessions/{shop}` | `{status, phone, qr_svg}` for one shop. `status` is `absent`, `pairing`, `connecting`, `connected`, `reconnecting`, `disconnected` or `logged_out`. |
| `POST /sessions/{shop}/pair` | Body `{auth}`. `auth: null` starts a new pairing (the QR appears on the status). A stored auth string resumes a linked device. |
| `POST /sessions/{shop}/send` | Body `{to, text}`: one message to one number (digits only, 8-15), 1-4000 characters. |
| `POST /sessions/{shop}/disconnect` | Stops the device and keeps its credentials for a later resume. |

The sidecar calls the app back at `WA_LINK_CALLBACK_URL` (`/state` with the encrypted-at-rest credentials, `/status`,
`/inbound`). It retries transient failures and never retries a send.

## Configuration

| Variable | Where | Meaning |
|---|---|---|
| `WA_LINK_TOKEN` or `WA_LINK_TOKEN_FILE` | sidecar and app | Shared secret, at least 32 characters. Use the file form in production (a Docker secret). |
| `WA_LINK_CALLBACK_URL` | sidecar | The app's internal base URL, e.g. `http://munim_app:8080/internal/wa-link`. |
| `WA_LINK_PORT` | sidecar | Default 8090. |
| `WA_LINK_SEND_GAP_MS` | sidecar | Minimum gap between sends from one device. Default 3000. |
| `WA_LINK_URL` | app | Where the sidecar is. Empty = the link is off. |
| `WA_LINK_DAILY_CAP` | app | Messages per shop per IST day, customers included. Default 40. |
| `WA_LINK_SEND_GAP_S` | app | Pause between two sends from one shop's SIM. Default 6. |

Generate the token once and keep it out of git: `openssl rand -base64 48 > secrets/wa_link_token` (production) and
give the same value to both containers.

## Running

```bash
cd server
docker compose -f deploy/docker-compose.prod.yml build munim_wa_link   # production
npm ci && npm test                                                     # here, in server/wa_link
```

Tests use a fake device (`node --test`), so they need no WhatsApp account and no network beyond npm.

## Owner steps (after deploy)

1. The owner writes `connect whatsapp` in their Smara chat and opens the link within 24 hours.
2. On the **SIM phone** (not the Business app), open WhatsApp → Linked devices → Link a device, and scan the code.
3. The page shows "Connected". Owner's chat gets a confirmation.
4. Keep the SIM phone switched on and online. Sends pause if it is offline for long.
5. Check the status any time with `whatsapp status`. To stop all sends, send `disconnect whatsapp`.

If WhatsApp logs the number out, every send pauses, the owner is told, and the owner re-pairs with
`connect whatsapp`. Nothing is sent from the SIM until that happens.
