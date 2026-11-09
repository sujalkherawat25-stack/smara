"""Owner-trusted import of mapped sheet rows into parties/obligations (Thread A)."""
import logging
from datetime import datetime, time, timezone

from psycopg_pool import AsyncConnectionPool

from src.bridge.continuum import scope_user
from src.bridge.outbox import enqueue_standalone
from src.ingest.sheet_mapper import SheetRow

log = logging.getLogger(__name__)


def _followup(row: SheetRow) -> datetime:
    """First reminder on the due date; overdue rows are due immediately."""
    due = datetime.combine(row.due_date, time.min, tzinfo=timezone.utc)
    return max(due, datetime.now(timezone.utc))


async def _party_id(conn, tenant_id: str, r: SheetRow):
    """A customer is identified by WhatsApp number. Without a number, by name (a number may come later)."""
    if r.phone:
        # a customer first seen without a number gets this number when it arrives
        cur = await conn.execute(
            """UPDATE parties SET whatsapp_phone = %s, gstin = COALESCE(%s, gstin)
                WHERE tenant_id = %s AND whatsapp_phone IS NULL AND lower(canonical_name) = lower(%s)
                  AND NOT EXISTS (SELECT 1 FROM parties x WHERE x.tenant_id = %s AND x.whatsapp_phone = %s)
                RETURNING id""",
            (r.phone, r.gstin, tenant_id, r.party, tenant_id, r.phone))
        row = await cur.fetchone()
        if row:
            return row["id"]
        cur = await conn.execute(
            """INSERT INTO parties (tenant_id, canonical_name, whatsapp_phone, gstin)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (tenant_id, whatsapp_phone) WHERE whatsapp_phone IS NOT NULL
               DO UPDATE SET gstin = COALESCE(EXCLUDED.gstin, parties.gstin)
               RETURNING id""",
            (tenant_id, r.party, r.phone, r.gstin))
        return (await cur.fetchone())["id"]
    cur = await conn.execute(
        """SELECT id FROM parties WHERE tenant_id = %s AND whatsapp_phone IS NULL AND lower(canonical_name) = lower(%s)
           ORDER BY id LIMIT 1""", (tenant_id, r.party))
    row = await cur.fetchone()
    if row:
        return row["id"]
    cur = await conn.execute(
        "INSERT INTO parties (tenant_id, canonical_name, whatsapp_phone, gstin) VALUES (%s, %s, NULL, %s) RETURNING id",
        (tenant_id, r.party, r.gstin))
    return (await cur.fetchone())["id"]


async def import_rows(pool: AsyncConnectionPool, tenant_id: str, rows: list[SheetRow], source: str = "SHEET") -> dict:
    created = updated = 0
    async with pool.connection() as conn:
        async with conn.transaction():
            for r in rows:
                party_id = await _party_id(conn, tenant_id, r)
                cur = await conn.execute(
                    """INSERT INTO obligations
                         (tenant_id, party_id, invoice_ref, amount_paise, due_date, next_followup_at, state, source)
                       VALUES (%s, %s, %s, %s, %s, %s, 'ACTIVE', %s)
                       ON CONFLICT (tenant_id, invoice_ref) DO UPDATE
                         SET amount_paise = EXCLUDED.amount_paise, due_date = EXCLUDED.due_date,
                             source = CASE WHEN EXCLUDED.source = 'TALLY' THEN 'TALLY' ELSE obligations.source END
                       WHERE obligations.state <> 'PAID'
                       RETURNING id, (xmax = 0) AS inserted""",
                    (tenant_id, party_id, r.invoice_ref, r.amount_paise, r.due_date, _followup(r), source))
                row = await cur.fetchone()
                if row and row["inserted"]:
                    created += 1
                    await conn.execute(
                        """INSERT INTO obligation_events (obligation_id, from_state, to_state, actor)
                           VALUES (%s, NULL, 'ACTIVE', 'OWNER')""",
                        (row["id"],),
                    )
                elif row:
                    updated += 1
    return {"created": created, "updated": updated}


async def open_refs(pool: AsyncConnectionPool, tenant_id: str, source: str | None) -> list[dict]:
    """Invoices still open (from `source`, or from any source when None): what a newer snapshot is compared against."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT o.id, o.invoice_ref, o.amount_paise, o.tenant_id, o.party_id, o.state, p.canonical_name
                 FROM obligations o JOIN parties p ON p.id = o.party_id
                WHERE o.tenant_id = %s AND o.state <> 'PAID' AND (%s::text IS NULL OR o.source = %s)""",
            (tenant_id, source, source))
        return await cur.fetchall()


async def close_invoices(pool: AsyncConnectionPool, rows: list[dict]) -> int:
    """Mark invoices PAID because the source no longer lists them as due. Each closure is recorded as evidence."""
    closed = 0
    async with pool.connection() as conn:
        async with conn.transaction():
            for o in rows:
                cur = await conn.execute(
                    """UPDATE obligations SET state = 'PAID', next_followup_at = NULL
                        WHERE id = %s AND state <> 'PAID' RETURNING id""", (o["id"],))
                if await cur.fetchone() is None:
                    continue
                await conn.execute(
                    """INSERT INTO obligation_events (obligation_id, from_state, to_state, actor)
                       VALUES (%s, %s, 'PAID', 'SYSTEM_CRON')""", (o["id"], o["state"]))
                closed += 1
    for o in rows:
        try:
            await enqueue_standalone(
                pool, user_id=scope_user(str(o["tenant_id"]), str(o["party_id"])),
                source_id=f"closed_{o['id']}", trusted=True, stamp_obligation_id=str(o["id"]),
                text=f"Invoice {o['invoice_ref']} is no longer due in the latest Tally export; marked paid",
                metadata={"actor": "SYSTEM_CRON", "obligation_id": str(o["id"])})
        except Exception:
            log.exception("could not queue closure evidence for %s", o["id"])
    return closed
