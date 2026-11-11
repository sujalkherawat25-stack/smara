# Pilot runbook: onboarding a shop, running it, and handling requests

Written for a closed pilot with 1 to 3 shops focused on receivables (dues, reminders, imports, statements).
Not legal advice: have the privacy and consent wording reviewed before real customer data goes in.

## 1. Before a shop goes live (checklist)

- [ ] WhatsApp number: a real number on the WhatsApp Business account with an approved display name. The Meta test number
      only messages up to 5 pre-registered recipients, so it cannot reach real customers.
- [ ] Templates approved by Meta (`smara_payment_reminder`, `smara_owner_waiting`); set `WA_REMINDER_TEMPLATE`
      and `WA_OWNER_TEMPLATE` in the server `.env` and restart `smara_app`.
- [ ] Business verification started (raises the daily limit above 250 customers).
- [ ] Dedicated model key with enough requests per minute (the shared test key allows about 10 per minute).
      To add capacity, put every key in `LLM_API_KEYS=key1,key2,...` in the server `.env` and restart `smara_app`
      (each key adds about 10 calls per minute; keys are never logged, the logs say `key#1`, `key#2`).
- [ ] `OPS_ALERT_PHONE` set so the watchdog can reach you (see section 4).
- [ ] The shop owner has read and agreed to the consent text (section 5).

## 2. Onboarding steps

1. Create the shop:
   `docker exec smara_app python scripts/add_tenant.py --name "Ravi Traders" --owner 919811100001 --vpa ravi@upi`
2. The owner messages the Smara number from that phone. Anything from an unknown number is treated as a customer,
   so the owner must write first from the registered number.
3. Owner sends their outstanding sheet (Excel/CSV) or Tally export. Smara imports it and reports the sheet total
   against the ledger total. **Check both numbers with the owner before enabling reminders.**
4. Owner links Telegram (optional): sends `telegram`.
5. Owner pairs their PC browser (optional): sends `pair browser`.
6. First week: read the shop's sessions daily (section 3) and turn every wrong answer into a regression task in
   `evals/agent_harness.py`.

## 3. Daily checks (2 minutes)

```
docker exec smara_app python scripts/usage_report.py 7        # tokens per shop, fallback share
curl -s localhost:8090/healthz                                 # outbox dead = 0, failed sessions low
```

Look at failed or odd conversations:
`select created_at, status, steps, left(messages->1->>'content',120) from agent_sessions order by created_at desc limit 20;`

## 4. Monitoring

`scripts/watchdog.py` runs every minute from cron on the host. It checks the public webhook route (a 302 or 5xx
means the proxy route was lost), `/healthz`, dead Continuum writes, failed sessions, the sandbox and disk space.
It messages `OPS_ALERT_PHONE` once per incident, every 6 hours while it lasts, and once on recovery. WhatsApp only
delivers free text to a number that wrote to the business in the last 24 hours, so message the Smara number once a
day from the ops phone, or set `TELEGRAM_BOT_TOKEN` and `OPS_TELEGRAM_CHAT_ID` for alerts that always arrive.
Install on the server: `sudo cp deploy/smara-watchdog.cron /etc/cron.d/smara-watchdog`.

## 5. Privacy and consent (starting points)

Tell the shop owner plainly, before they upload anything:
- What we store: their customer list, invoices and dues, message text between Smara and them, and notes the agent saves
  as memory. We do not store bank credentials and never move money.
- Customer messages: Smara only messages a shop's customers after the owner approves each message, and only about
  dues. Customers can ask the owner to stop; the owner tells Smara and that customer is no longer messaged.
- Where it is processed: our server in India (Oracle) and a model provider (Sarvam). Name any other processor.
- How long: until the shop leaves; nightly backups are kept 7 days.
- Their rights: see their data and have it deleted on request.

Export a shop's data: `docker exec smara_app python scripts/tenant_data.py export --tenant "Ravi Traders"`
Erase (dry run first, then for real):
```
docker exec smara_app python scripts/tenant_data.py erase --tenant "Ravi Traders"
docker exec smara_app python scripts/tenant_data.py erase --tenant "Ravi Traders" --execute --confirm "Ravi Traders"
```
Erase removes the shop's database rows and its Continuum memory scopes. Tell the shop that nightly backups
age out within 7 days.

## 6. Known limits to say out loud to pilot shops

- Receivables only: no invoicing, GST filing, inventory or payroll.
- Needs the owner's tap for anything outward; quiet hours are 9 PM to 8 AM IST.
- Reminders to customers outside WhatsApp's 24-hour window need the approved template.
- Browser tasks run on the owner's own PC only while it is on and the helper is running.
