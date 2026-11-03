"""Owner morning digest. All numbers come from Postgres, never from search."""
from datetime import date

from src.loop.upi import rupees


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

    lines = [f"Good morning. Overdue: {agg['od_n']} invoices, {rupees(agg['od_amt'])}.",
             f"Due in next 7 days: {agg['wk_n']} invoices, {rupees(agg['wk_amt'])}."]
    if top:
        lines.append("\nBiggest overdue:")
        lines += [f"- {r['canonical_name']} {r['invoice_ref']} {rupees(r['amount_paise'])} ({r['days']}d late)" for r in top]
    waiting = agg["waiting"] + pending_adj
    if waiting:
        lines.append(f"\n{waiting} item(s) waiting for your approval above.")
    return "\n".join(lines)
