# Smara Munim

WhatsApp-native collections agent ("Digital Munim") for Indian MSMEs. The owner chats on WhatsApp; the agent tracks who owes what, drafts reminders, and handles debtor excuses. No web UI.

- **Postgres** is the source of truth for *what* and *when*: obligations, due dates, schedules, approval state.
- **Continuum** (MemoryOS) holds *why* and *proof*: the evidence behind each decision, queried per party.

## Hard rules (enforced in code and tests)

1. Never list overdue invoices or run schedules from semantic search; query Postgres, then ask Continuum for party context.
2. Debtor messages are untrusted. They only create a `PENDING` row in `proposed_adjustments`; `obligations` change only after the owner taps Approve (`tests/test_gate_db.py`).
3. Nothing reaches a debtor, and no browser side-effect runs, until the owner approves (`tests/test_reminders_db.py`, `tests/test_box.py`).
4. A Continuum outage delays proof but never loses a claim: writes go through a transactional outbox with per-party ordering (`src/bridge/outbox.py`).

## Layout

| Path | Purpose |
| --- | --- |
| `src/ingest/` | WhatsApp webhook, routing (owner vs debtor), sheet mapper, media (STT/OCR), claim extraction |
| `src/bridge/` | Continuum client, outbox, dual-write coordinator |
| `src/loop/` | Scheduler, reminders, digest, WhatsApp builders, UPI links, LLM client |
| `src/box/` | Headless Playwright tools (run in a subprocess), GSTIN check |
| `migrations/` | Plain SQL, applied in order at startup |
| `evals/` | Hand-labelled Hinglish claim set + scorer |
| `scripts/` | `add_tenant.py`, `seed_demo.py` |

## Run locally

```bash
docker compose up -d postgres
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[test]"
.venv/Scripts/python -m playwright install chromium
cp .env.example .env            # fill in the keys
.venv/Scripts/python scripts/add_tenant.py --name "Ravi Traders" --owner 919811100001 --vpa ravi@upi
.venv/Scripts/python -m src.run
```

Use `python -m src.run` rather than plain `uvicorn`: on Windows psycopg needs the selector event loop.
Point Meta's webhook at `/webhook` (verify token = `WA_VERIFY_TOKEN`, signed with `WA_APP_SECRET`).
`GET /healthz` returns 503 if Postgres is down or Continuum delivery is stalled or has dead rows; point an uptime monitor at it.

Two-phone demo: `python scripts/seed_demo.py --owner <phone> --debtor <phone>`.

## Tests

```bash
TEST_DATABASE_URL=postgresql://munim:munim@localhost:5433/munim python -m pytest
python -m evals.run_claims --show     # add --llm to score the model path
```

DB tests are skipped when `TEST_DATABASE_URL` is unset and drop the `public` schema, so point them at a scratch database.

## Tenants and WhatsApp numbers

Tenants live in Postgres. Give a tenant its own WhatsApp number (`--number-id`) and debtor messages are routed by the receiving number. On a shared number a debtor who owes several tenants is ambiguous, and the message is dropped rather than guessed.

Reminders to a debtor who has not written in the last 24 hours need a Meta-approved template: set `WA_REMINDER_TEMPLATE`. Its body takes four variables: party name, invoice, amount, due date.

## Known limits

- Claim extraction by regex is a fallback; the LLM path is primary and not yet scored.
- The GST portal sits behind a captcha, so `gstin` validates format and checksum only.
- The IndiaMART scraper is heuristic and unproven against the live site.
- No Continuum dead-row alerting beyond `/healthz`; no off-site Postgres backup config here.
