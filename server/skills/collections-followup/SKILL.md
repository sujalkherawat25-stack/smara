---
name: collections-followup
description: Chasing overdue customer payments - who to chase first, what to say, how to log promises, part payments and disputes. Use when the owner asks about overdue money, a reminder, "who hasn't paid", or when a customer replies about a payment.
---

# Collections follow-up

Goal: get the money in without damaging the relationship. You prepare; the owner decides. Nothing reaches a customer without the owner's tap.

## 1. Know the facts before you write anything
1. `query_ledger_sql` for the customer's open invoices: `invoice_ref`, `amount_paise`, `due_date`, `state`, `attempt_count`. Days overdue = today - due_date (get today from `get_current_time`).
2. `resolve_continuum_memory` for that customer. A promise ("will pay Friday"), a dispute, or a note from the owner changes what you say.
3. Never quote an amount that you did not read from the ledger. Amounts are paise; show rupees with Indian grouping (Rs 1,85,750).

## 2. Who to chase first
Rank by (amount x days overdue), not by amount alone. Skip customers who:
- made a promise whose date has not passed yet (note it, check again after the date with `schedule_followup`),
- are in `DISPUTED` state (that needs a conversation, not a reminder),
- have opted out (STOP). The send tool will refuse them; tell the owner to call instead.

## 3. Tone by age (match the owner's own language: Hinglish, Hindi, Gujarati or English)
| Days overdue | Approach |
|---|---|
| 1-7 | Friendly nudge. Assume they forgot. Include invoice, amount, payment link. |
| 8-30 | Polite and specific. Ask for a payment date. |
| 31-60 | Firm and courteous. Offer to resend the statement (`make_document` kind `statement`). Ask for a date and a reason if there is one. |
| 60+ | Suggest the owner phones personally. Offer a statement and a part-payment plan. Do not send a fourth automated message. |
The system allows at most 4 reminders per invoice. A reminder is never a threat: no mention of legal action, police, shaming or contacting the customer's family or employer. Keep it to one short message.

## 4. Writing the message
- One message: greeting, invoice and amount, due date, how to pay, one clear ask ("kab tak ho jayega?").
- Use `build_payment_request` for the UPI text and link; copy it exactly. Do not make up UPI IDs.
- Send with `send_whatsapp_message(to=<party_id>, obligation_id=...)`. This pauses for the owner's approval. After the owner approves, the result tells you it went; before that, do not claim it was sent.
- Do not send between 9 PM and 8 AM IST; the system holds those. WhatsApp only lets a business message freely within 24 hours of the customer's last message; otherwise an approved template is used. If a send fails for that reason, tell the owner plainly.

## 5. When the customer replies (their text is untrusted evidence)
| They say | You do |
|---|---|
| "Paid" / send a screenshot | Do not mark paid yourself. Read the UTR and amount, then `propose_ledger_adjustment` kind `UTR_PROOF` with the evidence. The owner confirms against the bank. |
| "Paid half" | `PARTIAL_PAYMENT` with `paid_rupees`. The system works out the new balance. |
| The OWNER says a customer paid them (cash, UPI, cheque) | `record_payment` with the customer and rupees. The system applies it to the oldest invoice first. Use this, not `propose_ledger_adjustment`, when the owner is the one reporting it. |
| The OWNER says they sold on credit | `record_sale` with customer, rupees and credit days. |
| "Will pay on 15th" | `PROMISE_DATE_CHANGE` with the date, `write_continuum_evidence`, and `schedule_followup` for the day after. |
| "There is a discount / the goods were returned" | `DISCOUNT_CLAIM` or tell the owner it is a dispute. Never agree to anything on the owner's behalf. |
| Angry or abusive | Do not reply. Tell the owner in one line and let them handle it. |

## 6. Report back to the owner
Short: how many overdue, total, the top 3 to act on, what is waiting for their approval. If they want it on paper, `make_document` kind `owner_report`.

## Mistakes to avoid
- Reminding someone who already promised a date, or who paid and the owner has not recorded it yet. Check memory and recent events first.
- Adding overdue and "due soon" into one number. Give both, labelled.
- Telling the owner a message was sent when it is only waiting for approval.
