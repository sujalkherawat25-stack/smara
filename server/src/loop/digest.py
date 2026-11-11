"""Owner morning digest. All numbers come from Postgres, never from search."""
from datetime import date, timedelta

from src.loop.upi import rupees

SOON_DAYS = 5   # warn this many days before a 43B(h) deadline


async def build_digest(pool, tenant_id: str, today: date) -> str:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT
                 count(*) FILTER (WHERE state NOT IN ('PAID','DRAFT') AND due_date < %(d)s) AS od_n,
                 coalesce(sum(amount_paise) FILTER (WHERE state NOT IN ('PAID','DRAFT') AND due_date < %(d)s), 0) AS od_amt,
                 count(*) FILTER (WHERE state NOT IN ('PAID','DRAFT') AND due_date BETWEEN %(d)s AND %(d)s + 7) AS wk_n,
                 coalesce(sum(amount_paise) FILTER (WHERE state NOT IN ('PAID','DRAFT') AND due_date BETWEEN %(d)s AND %(d)s + 7), 0) AS wk_amt,
                 count(*) FILTER (WHERE state = 'WAITING_OWNER_APPROVAL') AS waiting
               FROM obligations WHERE tenant_id = %(t)s""", {"d": today, "t": tenant_id})
        agg = await cur.fetchone()
        cur = await conn.execute(
            """SELECT p.canonical_name, o.invoice_ref, o.amount_paise, (%(d)s - o.due_date) AS days
                 FROM obligations o JOIN parties p ON p.id = o.party_id
                WHERE o.tenant_id = %(t)s AND o.state NOT IN ('PAID','DRAFT') AND o.due_date < %(d)s
                ORDER BY o.amount_paise DESC LIMIT 5""", {"d": today, "t": tenant_id})
        top = await cur.fetchall()
        cur = await conn.execute(
            """SELECT count(*) AS n FROM proposed_adjustments a JOIN obligations o ON o.id = a.obligation_id
                WHERE o.tenant_id = %s AND a.status = 'PENDING'""", (tenant_id,))
        pending_adj = (await cur.fetchone())["n"]

        cur = await conn.execute(
            """SELECT supplier, bill_ref, amount_paise,
                      bill_date + CASE WHEN agreement_45 THEN 45 ELSE 15 END AS due
                 FROM payables WHERE tenant_id = %s AND paid_at IS NULL AND msme_class IN ('micro','small')
                ORDER BY due LIMIT 40""", (tenant_id,))
        bills = await cur.fetchall()

    lines = [f"Good morning. Overdue: {agg['od_n']} invoices, {rupees(agg['od_amt'])}.",
             f"Due in next 7 days: {agg['wk_n']} invoices, {rupees(agg['wk_amt'])}."]
    if top:
        lines.append("\nBiggest overdue:")
        lines += [f"- {r['canonical_name']} {r['invoice_ref']} {rupees(r['amount_paise'])} ({r['days']}d late)" for r in top]
    late = [b for b in bills if b["due"] < today]
    soon = [b for b in bills if today <= b["due"] <= today + timedelta(days=SOON_DAYS)]
    if late or soon:
        lines.append("\nSupplier payments (tax deduction at risk if paid late, Section 43B(h)):")
        lines += [f"- PAST DEADLINE: {b['supplier']} {b['bill_ref']} {rupees(b['amount_paise'])} (was due {b['due']})" for b in late[:5]]
        lines += [f"- Pay by {b['due']}: {b['supplier']} {b['bill_ref']} {rupees(b['amount_paise'])}" for b in soon[:5]]
    if today.weekday() == 0:            # Monday: what last week's follow-up brought in
        lines += await weekly_result(pool, tenant_id)
    waiting = agg["waiting"] + pending_adj
    if waiting:
        lines.append(f"\n{waiting} item(s) waiting for your approval above.")
    from src.ingest.customer_number import missing_numbers
    no_number = await missing_numbers(pool, tenant_id)
    if no_number:   # these customers can only be reminded once the owner gives a number
        lines.append("\nNumber chahiye (reminder ke liye): " + ", ".join(no_number)
                     + ". Likh dijiye, jaise: 'Ravi Traders 98xxxxxxxx'.")
    return "\n".join(lines)


async def weekly_result(pool, tenant_id: str) -> list[str]:
    """The last 7 days in rupees: invoices that were paid, how much of that came after a reminder, reminders sent and
    promises recorded. Empty when nothing happened, so a quiet shop is not shown a row of zeros."""
    async with pool.connection() as conn:
        paid = await (await conn.execute(
            """SELECT count(*) AS n, coalesce(sum(o.amount_paise), 0) AS amt,
                      coalesce(sum(o.amount_paise) FILTER (WHERE o.attempt_count > 0), 0) AS after_reminder
                 FROM obligations o
                WHERE o.tenant_id = %s AND o.state = 'PAID'
                  AND EXISTS (SELECT 1 FROM obligation_events e WHERE e.obligation_id = o.id AND e.to_state = 'PAID'
                                 AND e.created_at > now() - interval '7 days')""", (tenant_id,))).fetchone()
        work = await (await conn.execute(
            """SELECT (SELECT count(*) FROM obligation_events e JOIN obligations o ON o.id = e.obligation_id
                        WHERE o.tenant_id = %(t)s AND e.to_state = 'DISPATCHED' AND e.created_at > now() - interval '7 days') AS reminders,
                      (SELECT count(*) FROM proposed_adjustments a JOIN obligations o ON o.id = a.obligation_id
                        WHERE o.tenant_id = %(t)s AND a.adjustment_type = 'PROMISE_DATE_CHANGE'
                          AND a.created_at > now() - interval '7 days') AS promises""", {"t": tenant_id})).fetchone()
    if not (paid["n"] or work["reminders"] or work["promises"]):
        return []
    line = f"\nLast 7 days: {paid['n']} invoice(s) paid, {rupees(paid['amt'])}"
    if paid["after_reminder"]:
        line += f" ({rupees(paid['after_reminder'])} of it after a reminder)"
    return [f"{line}. {work['reminders']} reminder(s) sent, {work['promises']} promise(s) recorded."]
