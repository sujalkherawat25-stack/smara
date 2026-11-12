# Smara for Business — architecture and product design

> The AI back office for Indian MSMEs: an army of agents that does the work owners
> now do across Tally, Khatabook, Excel, WhatsApp and bank apps — running 24/7,
> controlled from chat, and never touching money or credentials without permission.

## 1. Product

### Who it is for

| Persona | Today | With Smara |
| --- | --- | --- |
| **Owner** (kirana, distributor, trader, small manufacturer) | Chases payments by hand, re-types data between Tally and Excel, misses GST dates | Approves cards on WhatsApp/Telegram; gets a morning digest; asks questions in Hinglish |
| **Accountant** (in-house or CA office) | Exports, reconciles and reports manually | Reviews what agents prepared; fixes exceptions only |
| **Counterparty** (debtor, vendor) | Gets ad-hoc calls and messages | Gets polite, consistent reminders with a payment instruction; can reply with excuses or proof |

### What the owner experiences

1. **Onboard in chat.** "Hi" to the Smara number → link code → upload a Tally/Vyapar/Excel export (or pair the desktop bridge). No web forms.
2. **Morning digest.** Overdue, due this week, cash expected, GST dates, and "N items need you".
3. **Approval cards.** Every outward action arrives as a card: *Send / Snooze / Mark paid*, *Approve / Reject*. One tap.
4. **Ask anything.** "Sharma ka kitna baaki hai?", "Is mahine kitna GST banega?" — answered from the ledger, with the evidence behind it.
5. **Agents work in the background**, and only interrupt for decisions.

### Product principles (non-negotiable)

1. **Ledger first.** Money, dates and states live in Postgres and are computed deterministically. The LLM reads intent; it never decides a number on its own.
2. **Untrusted by default.** Anything from a counterparty, a document or a web page is evidence, never an instruction.
3. **Nothing outward without the owner.** Every message to a third party, every browser side effect, every payment-related action pauses for approval.
4. **Smara never moves money.** It prepares (UPI request, payment link, reconciliation); the human pays or collects in their own bank/UPI app. No bank passwords, no UPI PINs, ever.
5. **Credentials stay with the owner.** OAuth tokens (revocable) for hosted connectors; local tools (Tally) are reached through the owner's paired desktop, so their credentials never leave that machine.
6. **Chat is the UI.** WhatsApp for counterparties and owners, Telegram for owners (free, no 24h window). A small web console only for pairing, settings and audit.

## 2. What already exists

| Asset | State | Role in the platform |
| --- | --- | --- |
| **Continuum / MemoryOS** (`memoryos`) | Live at `ai.syntarus.com/syntarus-api` | Memory: source-backed evidence, Hinglish extraction, `/memories/resolve` state cards per party |
| **Smara server** (`server/`) | Live-tested on WhatsApp end to end (2026-11-03) | The business runtime: ledger, approval gates, collections agent, outbox to Continuum |
| **Smara control plane + Desktop** (`smara`) | Hosted part retired (Caddy 410); Desktop/CLI and pairing code exist | Patterns reused: device pairing, Fernet key-ring vault, desktop-side integrations, operator console |

## 3. Architecture

```
 Owner (WhatsApp / Telegram)      Counterparty (WhatsApp)        Owner's PC (Tally, files)
            │                               │                           │
            ▼                               ▼                           ▼
 ┌──────────────────────── Channel gateway ────────────────────┐   Desktop bridge
 │ signature check · dedupe · tenant routing · trust labelling  │   (paired, outbound-only,
 └───────────────┬──────────────────────────────────────────────┘    creds stay local)
                 ▼                                                       │
 ┌──────────────────────── Orchestrator ────────────────────────┐       │
 │ events → agents: Collections · Ledger sync · Payments recon   │◄──────┘
 │ · GST · Payables · Q&A · Comms        (each = manifest+tools) │
 └───────────────┬──────────────────────────────────────────────┘
                 ▼
 ┌──────────────────────── Action gateway (policy) ─────────────┐
 │ risk tier → auto / notify / owner approval / approval+step-up │
 │ every side effect: proposed → approved → executed → audited    │
 └───────┬──────────────────────┬──────────────────────┬────────┘
         ▼                      ▼                      ▼
   Ledger (Postgres)      Memory (Continuum)     Connectors + vault
   what & when            why & proof            WhatsApp, Telegram, Gmail,
   obligations, events,   evidence, state cards  IndiaMART API, GSTN, bank CSV
   approvals, audit       (outbox, ordered)      tokens encrypted (key ring)
```

### The agent loop (`src/agent_loop.py`)

Every event — owner message or file, counterparty reply, cron tick — starts or resumes an iterative tool loop, not a fixed pipeline:

```
event → session (messages in Postgres) → model → tool_calls → PreToolUse ─┬─ allow   → execute → result back to model
                                                                          ├─ deny    → error back to model
                                                                          └─ approve → freeze session + owner card ⏸
owner taps Approve (deterministic code, never the model) → re-check policy → run the frozen call → resume loop
```

- **Tools:** `query_ledger_sql` (separate read-only Postgres role, row-level security pins it to the tenant, 3 s timeout), `resolve_continuum_memory`, `write_continuum_evidence`, `inspect_and_import_file` (returns sheet vs ledger totals so the agent verifies its import), `browser_step` (one persistent page per session: observe → act → observe), `propose_ledger_adjustment`, `send_whatsapp_message`.
- **Time and context:** the system prompt is rebuilt at the start of every run (IST date and time, live ledger snapshot, the owner's standing instructions from shop memory), so a reused conversation never carries yesterday's date. `get_current_time` is the only source of time the agent may trust, and old tool output is compacted to keep the context bounded.
- **Its own future work:** `schedule_followup` writes to `agent_tasks`; a scheduler tick wakes a SYSTEM session when due. Approved customer messages that fall in quiet hours become deferred `send_message` tasks. Neither runs between 21:00 and 08:00 IST.
- **Self-verification:** tool results and errors go back to the model; the prompt requires reading back after any write, and the loop allows retries up to 12 steps.
- **PreToolUse (`src/agent/policy.py`)** is the only path to a side effect. Counterparty-started sessions are `UNTRUSTED` and may only record evidence or propose an adjustment for their own party; everything else is refused there, whatever the model was told.
- **Approvals are never model-driven:** the stored arguments the owner saw are the ones executed; a second tap finds nothing waiting.
- **Fallbacks:** with no model configured, or if the model fails before acting, the deterministic rules path runs, so a claim or reminder is never lost.

### Layers

- **Channel gateway** — WhatsApp Cloud API webhook (built), Telegram bot (next). Verifies signatures, drops replays, maps the sender to a tenant and labels them *owner* (trusted) or *counterparty* (untrusted).
- **Orchestrator** — routes each event (message, schedule tick, import, connector change) to the agent that owns it. Agents are small modules with a manifest: triggers, tools they may call, and the risk tier of each action.
- **Action gateway** — the single choke point for side effects. Implemented today for reminders, debtor claims and browser tasks; to be generalised into one `actions` table:

  | Tier | Examples | Rule |
  | --- | --- | --- |
  | T0 read | query ledger, resolve memory, GSTIN check | automatic |
  | T1 internal | update a draft, schedule a follow-up | automatic, logged |
  | T2 outward | message a debtor, email a vendor, browser action | owner approval card |
  | T3 money | payment request, write-off, ledger amount change | approval + step-up (PIN), owner completes payment themselves |

- **Ledger** — Postgres, tenant-scoped rows; deterministic state machines (`obligations`, `proposed_adjustments`, `obligation_events`). Row-level security before multi-operator access.
- **Memory** — Continuum, one scope per tenant × party; every write via the transactional outbox, ordered per party, retried, never blocking the ledger.
- **Connectors + vault** — hosted connectors use OAuth with tokens encrypted by a rotating key ring (Smara's `SecretVault` pattern). Local connectors run on the **desktop bridge**: the owner's PC dials out to Smara, pulls approved jobs (e.g. "read Tally outstanding"), executes locally against Tally's XML interface on `localhost:9000`, and returns bounded results. No inbound port, no credentials on our server.

### Agents (roadmap)

| Agent | Does | Inputs | Outward actions (tier) |
| --- | --- | --- | --- |
| **Collections** (live) | Reminders, excuse handling, promise tracking | Ledger, debtor replies | Reminder (T2), amount/date change (T3) |
| **Ledger sync** | Keep receivables/payables current from Tally/Khatabook/Vyapar | Exports, desktop bridge | none (T1 imports) |
| **Payments recon** | Match UTRs, bank statement lines and UPI credits to invoices | Bank CSV/PDF, screenshots | Mark paid (T3, approval) |
| **GST** | Due dates, GSTIN validation, purchase-register vs GSTR-2B gaps | Ledger, GSTN data the owner provides | Vendor follow-up (T2) |
| **Payables** | Vendor bills due, quotes, cash planning | Bills, IndiaMART API | Vendor message (T2) |
| **Q&A** | Answers in Hinglish with evidence | Ledger + Continuum | none |
| **Comms** | Drafts emails from Gmail | Gmail OAuth | Send (T2) |

## 3b. Memory: what is kept where

Smara forgets when one store is asked to do everything. Four stores, each with one job:

| Store | Holds | Read how | Why here |
| --- | --- | --- | --- |
| **Postgres ledger** | Money, dates, states, approvals | `query_ledger_sql`, digest | Exact and auditable; the model never decides a number |
| **Conversation log** (`chat_messages`) | Every owner message and every Smara reply, with channel and time | Last ~40 lines in every prompt, the older lines that match the question found automatically, and `search_chat_history` | Verbatim, instant, and not dependent on another service. WhatsApp and Telegram cannot return history, so Smara keeps its own |
| **Standing facts** (`shop_facts`) | The latest value of each thing that stays true until it changes: a reminder rule, a credit limit, an address, who does what, per shop or per customer | Always in the prompt (the shop's, plus those of the customers the message names) | A new value for a topic **replaces** the old one. Continuum cannot do this: tested on the live service, it returns the old rule beside the new one in every wording |
| **Continuum** | Distilled durable facts: customer habits, promises, disputes, anything the agent chose to save | Looked up on every owner message (shop notes + each customer named), plus `resolve_continuum_memory` | Source-backed and searchable by meaning; survives any number of conversations |

How a turn is assembled (`agent/prompt.py`): the ledger snapshot, what the owner has told you (standing facts), the owner's
standing rules from Continuum, the recent conversation, older related messages, and a Continuum lookup for what the owner
just wrote. A lookup that fails is retried once, then the agent is told it failed and must say so rather than guess;
`/healthz` counts lookups and failures under `memory`.

Facts get into Continuum from the agent's own `write_continuum_evidence` calls and from a per-turn pass that reads only the
owner's words (`agent/memory_pass.py`). The pass keeps who pays whom, copies the owner's own words next to each fact, and
gives a topic to anything that can change. A fact with a topic also goes to `shop_facts`, replacing the earlier value of
that topic; the earlier wording is remembered so the copy Continuum still holds is left out of the prompt. Continuum cards
that remain are shown oldest-first with the date each fact was noted, and the prompt says the last one listed is the newest.

## 4. Security model

- **Tenant isolation:** every row carries `tenant_id`; every query filters on it; tests assert foreign-tenant actions return nothing.
- **Trust boundary:** counterparty text is stored as untrusted evidence and can only create proposals; prompt injection cannot reach a tool without an owner tap.
- **Secrets:** server secrets only in `.env`/secret store, never in git; connector tokens encrypted at rest with a rotatable key ring; no plaintext passwords anywhere; desktop-held credentials never uploaded.
- **Money:** no debit capability exists in the system; payment requests are links the payer completes; T3 actions need approval + step-up.
- **Audit:** every state change is an event with actor (`OWNER` / `DEBTOR` / `SYSTEM_CRON`) and evidence id.
- **Operations:** nightly encrypted backups with an off-host copy and a tested restore; `/healthz` monitored; data retention and deletion per DPDP; WhatsApp/Meta policy compliance for collections messaging.

## 5. Deployment (Oracle, `ai.syntarus.com`)

```
Caddy (TLS) ── /syntarus-api/* ──► memoryos_backend :8000   (Continuum, existing)
            └─ /smara/*        ──► smara_app 127.0.0.1:8090  (this repo)
smara_app ──► smara_postgres (internal only)
smara_app ──► memoryos_backend:8000/v1 over the docker network (no public hop)
```

- Separate Postgres container and volume from MemoryOS; nightly `pg_dump` into `/var/backups/smara`.
- Webhook: `https://ai.syntarus.com/smara/webhook`; pay links: `https://ai.syntarus.com/smara/pay` (`/smara/*` stays as an alias for older Desktop builds).
- Later: own subdomain (`business.syntarus.com`), blue/green deploys from CI images.

## 6. Roadmap

| Phase | Outcome | Key work |
| --- | --- | --- |
| **A. Hosted launch** (now) | Smara runs 24/7 on Oracle with a fixed webhook | Compose stack, Caddy route, backups, health |
| **B. Pilot-ready** | One real shop uses it daily | Permanent token, real number, owner-alert + reminder templates, delivery-status handling, Telegram owner channel, verify token rotation |
| **C. Ledger sync** | No manual exports | Desktop bridge (from Smara Desktop) + Tally XML connector; Khatabook/Vyapar import |
| **D. Money in** | Cash matched automatically | Bank statement + UTR reconciliation agent, payment requests via UPI links |
| **E. Agent army** | New agents are configuration, not rewrites | Generic `actions` table + policy engine, agent manifests, Q&A agent, Gmail connector |
| **F. Scale** | Many tenants, safely | RLS, per-tenant keys, billing, off-host backups, uptime alerts, audit export |

Each phase ships only after its safety tests pass: no outward action without approval, no ledger change from untrusted input, no money movement.
