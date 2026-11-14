# Smara scorecard: measured seconds for the daily shop tasks

Measured 14 Nov 2026 on the live image (real model glm5.3-flash, real loop, scratch database with the demo shop, fake WhatsApp,
sandbox in its own container like production). Each task was run 3 times; the table gives the range. The time is from the owner's
message arriving to Smara's answer or approval card being sent. It does NOT include the owner's own time to speak, read or tap.
Rerun with `evals/demo_scenarios.py` (see `server/docs/demo/DEMO_SCRIPT.md`).

| Task the owner does today | Smara (measured) | Result checked in the ledger or the file |
| --- | --- | --- |
| Record a credit sale: "Sharma Store ko 12000 ka maal udhaar diya, 15 din mein dega" | 4–10 s to the approval card | invoice written only after the owner taps Approve; amount and due date checked |
| Record cash received: "Gupta Traders ne 5000 cash diya" | 5–7 s to the card | oldest open invoice reduced by exactly Rs 5,000 |
| "Aaj kisko call karun? Top 3 overdue" | 12–17 s | right three customers, right amounts, ranked |
| Reminder with UPI for one customer | 16–19 s to the approval card | message shows amount, due date and UPI; nothing sent until Approve |
| Customer replies "parso shaam tak pakka bhej dunga" | 7–12 s | promise saved as a follow-up task |
| First messy Excel/Tally sheet in | 9 s | all rows imported, totals match the file |
| Business summary (total, overdue) | 10–23 s | totals match the ledger |
| Customer statement as PDF | 27 s | PDF sent, figures match |
| Whole ledger or one customer as Excel | 9–35 s (6 of 6 runs) | every invoice and amount present in the file |
| Purchase register + GSTR-2B match | 14–21 s for the match itself; about 42 s when both files are sent as two separate messages | ITC at risk, missing and mismatched invoices, PDF report |

Voice notes add the speech-to-text time (about 2–4 s) and the owner's talking time. 51 of 55 spoken test runs passed on the live
image; the misses were mostly speech heard wrong, which Smara now refuses or reads back instead of saving.

## What is not measured here

- The manual side (Khatabook taps, Tally voucher entry, Excel filtering, writing WhatsApp messages by hand). Time those with a
  stopwatch on the same demo shop, a few runs each, before quoting any "X times faster" figure. Do not quote ours without it.
- Real human voices and real shop noise (the voice tests use synthetic voices).
- Real WhatsApp delivery (waits for Meta registration), real Tally, photos of khata pages.
- Expenses, stock and supplier bills typed in chat: not in the ledger yet.
- Tokens: a simple question costs 15k–45k tokens, a GST match about 65k.
