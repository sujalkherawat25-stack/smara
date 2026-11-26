"""The owner's weekly review (Monday) and daily alerts, built from the ledger with no model. Both go to the owner only.

A reason to open the chat again without Smara messaging anyone else: Monday shows where the money stands and what ageing
this week brought (bills that crossed 60 and 90 days), and asks for the new week's file so Smara can say what changed. The
other days stay quiet unless something needs attention (a bill crossing 30/60/90 days today, a 43B(h) supplier deadline).
The owner can switch both off with 'digest off' (and back on with 'digest on')."""
from datetime import date, timedelta

from src import clock
from src.agent.money import inr

CROSSINGS = (30, 60, 90)
STALE_DAYS = 6              # a ledger older than this gets a nudge to send the new week's file
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def rupees(paise) -> str:
    """Indian grouping, no paise: 21000000 -> ₹2,10,000."""
    return inr(int(paise) / 100, 0)


def _b(n: int) -> str:
    return f"{n} bill" if n == 1 else f"{n} bills"


def short(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


async def opted_out(pool, tenant_id: str) -> bool:
    async with pool.connection() as conn:
        return await (await conn.execute("SELECT 1 FROM digest_optout WHERE tenant_id = %s", (tenant_id,))).fetchone() is not None


async def opted_in(pool, tenant_id: str) -> bool:
    """They asked for the summary with 'digest on' (it then goes out even where Smara starts nothing unless asked)."""
    async with pool.connection() as conn:
        return await (await conn.execute("SELECT 1 FROM digest_optin WHERE tenant_id = %s", (tenant_id,))).fetchone() is not None


async def set_digest(pool, tenant_id: str, on: bool) -> None:
    add, remove = ("digest_optin", "digest_optout") if on else ("digest_optout", "digest_optin")
    async with pool.connection() as conn:
        await conn.execute(f"DELETE FROM {remove} WHERE tenant_id = %s", (tenant_id,))
        await conn.execute(f"INSERT INTO {add} (tenant_id) VALUES (%s) ON CONFLICT DO NOTHING", (tenant_id,))


async def last_file_day(pool, tenant_id: str) -> date | None:
    """The day the owner last sent a file (the ledger can only be as fresh as that)."""
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "SELECT max(created_at) AS at FROM chat_messages WHERE tenant_id = %s AND role = 'owner' AND kind = 'file'",
            (tenant_id,))).fetchone()
    return row["at"].astimezone(clock.IST).date() if row and row["at"] else None


async def _open(pool, tenant_id: str, today: date):
    live = "state NOT IN ('PAID','DRAFT')"
    async with pool.connection() as conn:
        agg = await (await conn.execute(
            f"""SELECT count(*) FILTER (WHERE {live}) AS bills, count(DISTINCT party_id) FILTER (WHERE {live}) AS parties,
                       coalesce(sum(amount_paise) FILTER (WHERE {live}), 0) AS total,
                       coalesce(sum(amount_paise) FILTER (WHERE {live} AND due_date >= %(d)s), 0) AS not_due,
                       coalesce(sum(amount_paise) FILTER (WHERE {live} AND %(d)s - due_date BETWEEN 1 AND 30), 0) AS b30,
                       coalesce(sum(amount_paise) FILTER (WHERE {live} AND %(d)s - due_date BETWEEN 31 AND 60), 0) AS b60,
                       coalesce(sum(amount_paise) FILTER (WHERE {live} AND %(d)s - due_date BETWEEN 61 AND 90), 0) AS b90,
                       coalesce(sum(amount_paise) FILTER (WHERE {live} AND %(d)s - due_date > 90), 0) AS b90p,
                       count(*) FILTER (WHERE {live} AND due_date BETWEEN %(d)s AND %(d)s + 7) AS wk_n,
                       coalesce(sum(amount_paise) FILTER (WHERE {live} AND due_date BETWEEN %(d)s AND %(d)s + 7), 0) AS wk_amt
                  FROM obligations WHERE tenant_id = %(t)s""", {"d": today, "t": tenant_id})).fetchone()
        top = await (await conn.execute(
            f"""SELECT p.canonical_name AS name, sum(o.amount_paise) AS amt, max(%(d)s - o.due_date) AS oldest
                  FROM obligations o JOIN parties p ON p.id = o.party_id
                 WHERE o.tenant_id = %(t)s AND o.{live} AND o.due_date < %(d)s
                 GROUP BY p.id, p.canonical_name ORDER BY sum(o.amount_paise) DESC LIMIT 3""", {"d": today, "t": tenant_id})).fetchall()
    return agg, top


async def _crossed(pool, tenant_id: str, today: date, days: int, window: int):
    """Open bills that passed `days` days overdue within the last `window` days (window 1 = today)."""
    async with pool.connection() as conn:
        return await (await conn.execute(
            """SELECT p.canonical_name AS name, o.amount_paise AS amt
                 FROM obligations o JOIN parties p ON p.id = o.party_id
                WHERE o.tenant_id = %(t)s AND o.state NOT IN ('PAID','DRAFT')
                  AND %(d)s - o.due_date BETWEEN %(lo)s AND %(hi)s
                ORDER BY o.amount_paise DESC""", {"t": tenant_id, "d": today, "lo": days, "hi": days + window - 1})).fetchall()


def _names(rows, n: int = 3) -> str:
    by: dict[str, int] = {}
    for r in rows:
        by[r["name"]] = by.get(r["name"], 0) + r["amt"]
    best = sorted(by.items(), key=lambda kv: -kv[1])[:n]
    return ", ".join(f"{k} {rupees(v)}" for k, v in best) + (f" aur {len(by) - n} aur" if len(by) > n else "")


async def _supplier_deadlines(pool, tenant_id: str, today: date):
    async with pool.connection() as conn:
        return await (await conn.execute(
            """SELECT supplier, bill_ref, amount_paise, bill_date + CASE WHEN agreement_45 THEN 45 ELSE 15 END AS due
                 FROM payables WHERE tenant_id = %s AND paid_at IS NULL AND msme_class IN ('micro','small')
                ORDER BY due LIMIT 60""", (tenant_id,))).fetchall()


async def weekly_review(pool, tenant_id: str, today: date) -> str:
    """Monday's message. Empty when the shop has no ledger and no supplier bills (a personal user)."""
    agg, top = await _open(pool, tenant_id, today)
    bills = await _supplier_deadlines(pool, tenant_id, today)
    if not agg["bills"] and not bills:
        return ""
    lines = [f"Namaste! Is hafte ka haal ({short(today)}):"]
    if agg["bills"]:
        lines += [f"\n*Kul baaki: {rupees(agg['total'])}* ({_b(agg['bills'])}, {agg['parties']} parties)",
                  f"• Abhi due nahi: {rupees(agg['not_due'])}", f"• 1-30 din late: {rupees(agg['b30'])}",
                  f"• 31-60 din: {rupees(agg['b60'])}", f"• 61-90 din: {rupees(agg['b90'])}",
                  f"• 90 din se upar: {rupees(agg['b90p'])}"]
        c60, c90 = await _crossed(pool, tenant_id, today, 60, 7), await _crossed(pool, tenant_id, today, 90, 7)
        if c90 or c60:
            lines.append("\n*Is hafte aur purane hue:*")
            if c90:
                lines.append(f"• 90 din paar: {_b(len(c90))}, {rupees(sum(r['amt'] for r in c90))} ({_names(c90)})")
            if c60:
                lines.append(f"• 60 din paar: {_b(len(c60))}, {rupees(sum(r['amt'] for r in c60))} ({_names(c60)})")
        if top:
            lines.append("\n*Sabse bade overdue:*")
            lines += [f"• {r['name']}: {rupees(r['amt'])} (sabse purana {r['oldest']} din)" for r in top]
        if agg["wk_n"]:
            lines.append(f"\nAgle 7 din mein due: {_b(agg['wk_n'])}, {rupees(agg['wk_amt'])}")
    late = [b for b in bills if b["due"] < today]
    soon = [b for b in bills if today <= b["due"] <= today + timedelta(days=7)]
    if late or soon:
        lines.append("\n*Supplier payments (43B(h)):*")
        if late:
            lines.append(f"• Deadline nikal gayi: {_b(len(late))}, {rupees(sum(b['amount_paise'] for b in late))}")
        if soon:
            lines.append(f"• Agle 7 din mein deadline: {_b(len(soon))}, {rupees(sum(b['amount_paise'] for b in soon))}")
    seen = await last_file_day(pool, tenant_id)
    if seen is None or (today - seen).days > STALE_DAYS:
        lines.append("\nYe list " + (f"{short(seen)} ki hai ({(today - seen).days} din purani)" if seen else "purani file se hai")
                     + ". Is hafte ki Tally/Excel file bhej dijiye, main bataunga kya badla: kitna paisa aaya, kaunsi nayi bills, "
                       "kaun ruka hua hai.")
    else:
        lines.append(f"\nList {short(seen)} tak ki hai. Nayi file bhejenge to main bataunga kya badla.")
    lines.append("(Ye summary band karna ho to 'digest off' likhiye.)")
    return "\n".join(lines)


async def daily_alerts(pool, tenant_id: str, today: date) -> str:
    """A short note on days when something needs attention; empty (nothing sent) on every other day."""
    notes = []
    for d in CROSSINGS:
        rows = await _crossed(pool, tenant_id, today, d, 1)
        if rows:
            notes.append(f"• {len(rows)} bill(s) aaj {d} din se late ho gaye: {rupees(sum(r['amt'] for r in rows))} ({_names(rows)})")
    for b in await _supplier_deadlines(pool, tenant_id, today):
        gap = (b["due"] - today).days
        if gap in (2, 0, -1):
            when = {2: "2 din mein deadline", 0: "aaj deadline", -1: "kal deadline nikal gayi"}[gap]
            notes.append(f"• Supplier {b['supplier']} {b['bill_ref']} {rupees(b['amount_paise'])}: {when} (43B(h))")
    if not notes:
        return ""
    return "Dhyan dene wali baatein:\n" + "\n".join(notes[:8]) + "\n\n'digest' likhiye poori list ke liye. ('digest off' se ye band ho jayega.)"
