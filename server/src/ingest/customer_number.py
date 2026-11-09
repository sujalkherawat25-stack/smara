"""The owner gives the WhatsApp number of a customer Smara has without one: "<customer name> <mobile>".

Deterministic on purpose: only an exact match against a customer who has no number is taken. Anything else is left
to the agent. Nothing is sent to anyone by this."""
import re

from src.ingest.sheet_mapper import parse_phone

PATTERN = re.compile(r"^\s*(?P<name>[^\d+]{2,60}?)\s+(?P<num>\+?\d[\d\s-]{8,16}\d)\s*$")


def parse_number_command(text: str) -> tuple[str, str] | None:
    """(customer name, 91-prefixed number) when the message is exactly 'name number', else None."""
    m = PATTERN.match(text or "")
    if not m:
        return None
    phone = parse_phone(m["num"])
    if not phone:
        return None
    return m["name"].strip(), phone


async def add_number(pool, tenant_id: str, name: str, phone: str) -> str | None:
    """The reply for the owner, or None when no customer without a number has that name (the message goes to the agent)."""
    async with pool.connection() as conn:
        async with conn.transaction():
            row = await (await conn.execute(
                """SELECT id, canonical_name FROM parties
                    WHERE tenant_id = %s AND whatsapp_phone IS NULL AND lower(canonical_name) = lower(%s)
                    ORDER BY id LIMIT 1 FOR UPDATE""", (tenant_id, name))).fetchone()
            if row is None:
                return None
            clash = await (await conn.execute(
                "SELECT canonical_name FROM parties WHERE tenant_id = %s AND whatsapp_phone = %s", (tenant_id, phone))).fetchone()
            if clash:
                return f"Ye number pehle se {clash['canonical_name']} ke naam se saved hai. Naam check karke dobara bhejiye."
            await conn.execute("UPDATE parties SET whatsapp_phone = %s WHERE id = %s", (phone, row["id"]))
    return f"{row['canonical_name']} ka number save ho gaya ({phone[-10:]}). Ab unke reminders ban sakte hain."


async def missing_numbers(pool, tenant_id: str, limit: int = 10) -> list[str]:
    """Customers with open bills and no number: the owner is asked for these."""
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT DISTINCT p.canonical_name FROM obligations o JOIN parties p ON p.id = o.party_id
                WHERE o.tenant_id = %s AND p.whatsapp_phone IS NULL AND o.state <> 'PAID'
                ORDER BY p.canonical_name LIMIT %s""", (tenant_id, limit))).fetchall()
    return [r["canonical_name"] for r in rows]
