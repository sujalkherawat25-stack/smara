---
name: owner-daily-report
description: Giving the owner a clear picture of their money - receivables, overdue, collections, payables - as a short WhatsApp summary or a PDF report. Use for "how is business", "summary", "report", "kitna baaki hai", or any scheduled morning/weekly briefing.
---

# Owner report and daily brief

## Principles
- Numbers come from the ledger via `query_ledger_sql` or from `make_document` (which reads the database itself). Never from memory of an earlier chat.
- Lead with what needs a decision, then the picture. An owner reading on a phone gives you ten seconds.
- State the data's limits: the figures include only what has been synced from Tally or uploaded. If the last Tally sync is old or never happened, say that first.

## The WhatsApp brief (what to send)
Keep to roughly 8 lines, in the owner's language:
1. **Money owed to you**: total, and how much is overdue (separate numbers, never added together).
2. **Collected recently**: amount and number of invoices cleared in the last 7 or 30 days.
3. **Top 3 overdue customers** with amount and days late.
4. **Owed to suppliers**: bills past their 43B(h) deadline and those due within 7 days (if payables exist).
5. **Waiting for you**: approvals and proposals pending.
6. One suggested next action, e.g. "Ravi Traders is 45 days late on Rs 1,20,000; want me to draft a reminder?"

## The PDF report
When the owner asks for a report, a PDF, or "send me this month's picture": `make_document` kind `owner_report` (set `days` to 7, 30, 90...), check the `figures` in the result, then `send_file_to_owner` with a one-line caption stating the period. If they want an editable file for the CA, use `format: docx`.

For anything the template does not cover (sales by item, a bank statement summary, a GST workings sheet), build blocks yourself with kind `custom`, from numbers you computed and printed with `run_python`. See the `making-documents` skill.

## Checks before you send
- Do the stats add up to the table below them? (Total overdue = sum of the overdue invoices.)
- Is the period what the owner asked for? If they gave none, say which you chose.
- Is anything labelled "collected" actually confirmed paid (state PAID), not just promised?

## Scheduled briefs
If the owner asks for a daily or weekly brief, use `schedule_followup` with a clear instruction and a time; remember that nothing is sent between 9 PM and 8 AM IST. Ask what time and what they care about (collections only, or payables too) and save the preference with `write_continuum_evidence` (party_id `shop`).

## Mistakes to avoid
- A long wall of numbers. Short first; offer the PDF for detail.
- Reporting rupees without Indian grouping or without the rupee sign.
- Reassuring when the data is stale. Say how old it is.
