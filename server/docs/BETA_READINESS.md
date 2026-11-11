# Smara beta readiness: audit of 11 November 2026

What was checked: the production server, the hosted app code, Smara Desktop and its PC helper, the Telegram and
WhatsApp channels, the public pages, the runbook and the business plan. Each item says whether it is done, and who
has to do what is left.

## 1. Verdict

Smara can start a supervised beta **on Telegram** as soon as this release is live. An owner can now join, upload
dues, get digests and approval cards, and pair a PC without WhatsApp. What still needs WhatsApp is the part that
sells: reminders sent to a shop's customers from Smara's number. That waits on the Meta registration.

## 2. Found and fixed in this release

| # | Problem found | Fix |
| --- | --- | --- |
| 1 | **Nobody new could join.** A shop could only be created from the WhatsApp number, and Telegram could only be linked from WhatsApp. With WhatsApp down, onboarding was closed. | A new owner signs up on Telegram with an invite link. Telegram confirms their phone number, so the same shop is recognised on WhatsApp later. A number that already has a shop cannot be claimed this way. |
| 2 | **Telegram had no daily limits.** Message, file and token limits were only applied on WhatsApp, so one Telegram user could use up the shared model key. | The same limits now apply on Telegram. Approval taps are never limited. |
| 3 | **Desktop showed the connection, not the shop.** The Hosted panel said "connected" but none of the shop's numbers. | New `/bridge/overview` endpoint. The PC helper fetches it once a minute and the Hosted panel shows dues, overdue, bills to pay, approvals waiting, whether Telegram is linked, and the last Tally sync. Totals only, no customer names. |
| 4 | **Public pages said "WhatsApp" only.** Privacy policy did not name Telegram as a processor. | Home, privacy, terms and data-deletion pages now cover Telegram. |
| 5 | **No weekly result.** The business plan calls the weekly "what Smara brought in" card the feature that sells. It was not built. | Monday's digest now adds the last 7 days: invoices paid, rupees, how much came after a reminder, reminders sent, promises recorded. |
| 6 | **Stale version pin.** `.env` on the server named an older image, so a plain restart could start old code. | `release.sh` now writes the running version into `.env` after every healthy release. |
| 7 | **Team was not told about new shops.** | An ops alert goes to the team's Telegram when a shop joins on Telegram, and when someone tries to join with a number that already has a shop. |
| 8 | Runbook described WhatsApp-only onboarding. | Runbook rewritten: Telegram-first and WhatsApp paths. `invites.py create` prints the Telegram invite link. |

## 3. How the three surfaces stay in sync

- **One shop, one ledger.** The server database is the only place money, dates and states live. Telegram, WhatsApp
  and Desktop are views of it.
- **One identity.** A shop is known by the owner's phone number. Telegram sign-up collects that number, WhatsApp
  has it by nature, and Desktop is paired with a one-time code the owner asks for in chat.
- **Approvals follow the owner.** Cards go to the channel the owner last used. If WhatsApp's 24-hour window is
  closed or the link is off, they go to Telegram.
- **Desktop mirrors the chat.** The Hosted panel shows the same totals as the digest, refreshed every minute while
  the PC is on. Approving is still done in chat, so there is one place where a decision is made.

Not built, on purpose: approving cards inside Desktop. It adds a second place to make decisions and a second
security surface. Worth doing after the beta shows owners want it.

## 4. Still open: technical

| Priority | Item | Who | Notes |
| --- | --- | --- | --- |
| P0 | Official WhatsApp number returns 401 | You (Meta) | Tomorrow's registration. After it: set the token and number id in `.env`, run a release, send one test message. The app already alerts the team if Meta refuses a send. |
| P0 | Reminder and owner templates approved by Meta | You (Meta) | `WA_REMINDER_TEMPLATE` is set; `WA_OWNER_TEMPLATE` is not. Without it, an owner whose WhatsApp window is closed and who has no Telegram gets nothing. |
| P1 | Model capacity | You | One key, about 10 calls a minute. Fine for 1 to 3 shops. Before 5 or more, add keys in `LLM_API_KEYS`. |
| P1 | Off-server backups | You, then me | Nightly dumps stay on the same machine. Needs a storage bucket and an age key; the script already supports both. |
| P1 | External uptime monitor | You | Needs an account. URL `https://ai.syntarus.com/smara/healthz`, expect 200 and `"ok":true`. |
| P1 | Desktop needs Python on the owner's PC | Me (next) | The PC helper runs on the owner's own Python. A shop owner will not install Python. Bundle the helper as an exe before giving Desktop to non-technical owners. |
| P2 | Desktop installer is unsigned | You | Windows shows a warning on install. The signing work is ready in PR 18 and waits for a certificate. |
| P2 | Desktop build is not reproducible | Me | The 0.2.x builds reuse native binaries from an earlier build. |

Server housekeeping done during this audit: three old `.env` backups (they held secrets that are still valid) and an
old copy of the health script were deleted. Disk is at 61%, backups run nightly and age out after 7 days, no app
errors in the last 24 hours, MemoryOS untouched.

## 5. Still open: business and compliance

| Priority | Item | Notes |
| --- | --- | --- |
| P0 | Meta business verification | Needs a legal entity document. GST certificate or Udyam registration both work. The legal name, address and website must match across the document, the Meta account and the site. |
| P0 | Website match for Meta | `www.syntarus.com/smara` returns "temporarily unavailable" (410), and the main site is about Continuum. Use `https://ai.syntarus.com/smara/` as the product and privacy URL in Meta, or redirect the www address to it. The page footer should carry the registered business name and address once you have them. |
| P0 | GST registration | Needed to invoice paying shops, and it is the simplest document for Meta. Udyam (free, same day) is a faster first document if GST takes time. |
| P1 | Legal review of privacy, terms and the pilot letter | Voice notes and message text go to a model provider (Sarvam). Shops share their customers' phone numbers. A lawyer should confirm the consent wording under the DPDP Act before more than a few shops join. |
| P1 | Support address works | The pages give `hello@syntarus.com`. Confirm it is monitored. |
| P1 | Pricing decision | Pages say free during early access. The plan tests ₹999 a month, or ₹1,999 or 1% of extra money collected. Decide what you tell the first owners about what comes after free. |
| P2 | Startup India and iStart | Recognition is needed for the seed fund; the pilot numbers are the application. |

## 6. Beta launch checklist

1. This release is live and `/healthz` is green.
2. Create an invite: `docker exec smara_app python scripts/invites.py create --label "<shop>"`. Send the Telegram link.
3. Sit with the first owner for the import. Check the sheet total against the ledger total together.
4. Pair their PC only if they use Tally and can install Desktop with your help.
5. Read their sessions every day for the first week (`PILOT_RUNBOOK.md`, section 3).
6. When Meta is done: set the token, release, send one reminder to your own number, then turn reminders on for the shop.
