# Signing in to sites for the owner (post-beta design, not built)

## Today
- Smara's server browser keeps a private profile per account (`/data/browser-profiles/<tenant_id>`) but the owner cannot see or use it.
  At a login, OTP, CAPTCHA or payment step Smara now stops, says so, and reports what it did get done.
- On the owner's own PC browser, `hand_off_to_owner` already works: the owner signs in themselves, taps Done, Smara carries on.

## The idea: "Connect a site" on a Smara web page
The owner opens a one-time link Smara sends in chat (`/smara/connect/<token>`), picks a site, and sees **that account's own browser window live in the page**
(remote view of the server browser, like a remote desktop in a tab). They type their password, OTP or CAPTCHA themselves.
**Smara never sees or stores the password.** The site's login cookies stay in the owner's private browser profile, and Smara uses them later.

```
chat: "Amazon par sign-in chahiye" -> Smara sends link (valid 10 min, one use, bound to the account)
page: live browser view  -> owner signs in -> taps "Done" -> view closes, Smara continues the task
```

### Pieces to build
1. **Remote view**: Xvfb already exists. Add a screen stream (CDP screencast or noVNC over WebSocket) behind the existing proxy path `/smara/*`.
   Mouse and keyboard go back to that one session only.
2. **Link tokens**: signed, short life, one use, tied to account and site; revoked when the owner taps Done or after 10 minutes. Rate-limited.
3. **Account page** (same web page, signed in with a Telegram/WhatsApp magic link):
   - Connected sites: list with last-used date, **Disconnect** (clears that site's cookies).
   - **Delete my activity**: conversations, files, memory, browser history. **Delete my account**: everything, with a 7-day grace.
   - **Data use choice**: "Use my anonymised data to improve Smara" yes/no. No = nothing leaves their account for development, and the sessions are excluded from eval sets and logs.
   - Download my data (DPDP access right).
4. **Cookie vault**: encrypt each profile at rest (per-account key, kept off the data disk). Today the cookies are plain files.
5. **Agent rules** (already partly in place): Smara may use a signed-in site only for what the owner asked, stops before **any payment or irreversible step** (order, transfer, send) and asks for a tap; never reads password fields; logs every site action to the owner-visible activity list.

### Risks to settle first
- **Bot blocking and account locks**: sites such as Amazon may flag a login from a data-centre IP and lock the account. Test with throwaway accounts first; tell owners before they connect a main account. Possible fix: a fixed residential/India egress IP per account later.
- **Terms of service**: some sites forbid automated access even with the owner's consent. Keep a per-site allow list; start with shopping and reading, not banking or government portals (no net banking, GST, income-tax, UPI).
- **Shared screen risk**: the owner's typing passes through our server in the remote view. Say so plainly, do not log keystrokes, TLS only, and hide the stream from ops tools.
- **Payments**: never complete a payment for the owner in the first version. Smara fills the cart and the owner pays on their own phone or PC.

### Order of work
1. Account page with delete-everything, data-use switch, download (needed for legal review anyway; no browser work).
2. Cookie encryption at rest and the "Connected sites / Disconnect" list.
3. Remote view for sign-in, behind a per-site allow list, tested with throwaway accounts.
4. Later: owner-approved checkout and an India egress IP.

Alternative kept for sensitive sites: the PC-browser hand-off (already built), where the password never leaves the owner's machine.
