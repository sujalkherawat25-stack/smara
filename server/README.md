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
TEST_DATABASE_URL=postgresql://munim:munim@localhost:5433/munim_test python -m pytest
python -m evals.run_claims --show     # add --llm to score the model path
```

DB tests are skipped when `TEST_DATABASE_URL` is unset and they drop the `public` schema, so use a scratch database (`createdb munim_test`), never your dev one.

## What the hosted agent can do

| Tool | Tier | Purpose |
| --- | --- | --- |
| `query_ledger_sql` | read | Read-only SQL as a locked-down Postgres role, pinned to the shop by row-level security |
| `get_current_time` | read | IST date, time, weekday, business hours (the prompt is also rebuilt fresh every run) |
| `run_python` | read | Isolated sandbox for Excel/CSV/Tally XML/PDF work; helpers for Indian amounts, Tally, PDF statements, XLSX |
| `inspect_and_import_file` | read / internal | Preview or import a sheet and compare sheet vs ledger totals |
| `resolve_continuum_memory` / `write_continuum_evidence` | read / internal | Per-customer memory, and shop-wide notes and standing instructions (`party_id='shop'`) |
| `schedule_followup` / `list_followups` / `cancel_followup` | internal | The agent plans its own future work (25 pending, 50 per day per shop) |
| `send_file_to_owner` | internal | Sends a PDF/XLSX/CSV the sandbox produced to the owner (WhatsApp or Telegram) |
| `check_gstin`, `build_payment_request` | read | GSTIN checksum, UPI payment text and tap-to-pay link |
| `browser_step` | read / gated | One interactive page per session; navigate and read run, click/fill/press need approval |
| `propose_ledger_adjustment` | gated | Discount, part-payment, new date, paid-with-proof; the server computes the arithmetic |
| `send_whatsapp_message` | owner: internal, customer: gated | Customers are messaged only after the owner approves the exact text |

Owner voice notes are transcribed (Sarvam) and handled like text. Long conversations are compacted
(old tool output truncated, oldest turns dropped whole) so the context stays bounded.

**Quiet hours** (`QUIET_HOURS_IST`, default 21-8): nothing proactive is sent. Reminders do not start,
cards and agent notes wait and are released at opening (as one template nudge if the owner's WhatsApp window
is closed), the digest only goes out 9:00-12:00, and a customer message the owner approved at night is
sent automatically when the shop opens. Replies to something the owner just wrote are never delayed.

## Agent harness

`evals/agent_harness.py` runs 22 end-to-end tasks through the real loop, policy and sandbox with a real model
(Q&A, analysis, Excel and Tally XML imports, GST analysis, bank reconciliation, partial payment, debtor
injection, UTR proof, chatter, gated reminder, mark-paid, cron with memory, tenant isolation). Tasks are
scored from ledger state and outbound messages, never from the model's own claims.

```bash
python -m evals.agent_harness --db postgresql://munim:munim@localhost:5433/munim_eval --model glm5.3
```

## Python sandbox

`run_python` executes the agent's code on attached files in the `munim_sandbox` container: no network,
read-only root, tmpfs scratch, 2 GB / 1.5 CPU / 256 pids, each job as `nobody` with CPU, memory and file
limits, and job directories private to root. `sandbox/munim_helpers.py` parses Tally XML, finds real header
rows in Excel reports, and reads Indian amounts and dates. Output files become file refs the verifying
importer can load. Set `SANDBOX_MODE=local` for development only (no isolation).
`scripts/continuum_check.py` does a live Continuum round trip (write, ingest, resolve, isolation, delete) on a throwaway scope.

## Tenants and WhatsApp numbers

Tenants live in Postgres. Give a tenant its own WhatsApp number (`--number-id`) and debtor messages are routed by the receiving number. On a shared number a debtor who owes several tenants is ambiguous, and the message is dropped rather than guessed.

Reminders to a debtor who has not written in the last 24 hours need a Meta-approved template: set `WA_REMINDER_TEMPLATE`. Its body takes four variables: party name, invoice, amount, due date.

## Known limits

- The regex claim extractor is only a fallback when no model is configured or the model fails.
- The GST portal sits behind a captcha, so `gstin` validates format and checksum only.
- The IndiaMART scraper is heuristic and unproven against the live site.
- No Continuum dead-row alerting beyond `/healthz`; no off-site Postgres backup config here.
