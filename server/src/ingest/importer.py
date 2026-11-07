"""Owner-trusted import of mapped sheet rows into parties/obligations (Thread A)."""
from datetime import datetime, time, timezone

from psycopg_pool import AsyncConnectionPool

from src.ingest.sheet_mapper import SheetRow


def _followup(row: SheetRow) -> datetime:
    """First reminder on the due date; overdue rows are due immediately."""
    due = datetime.combine(row.due_date, time.min, tzinfo=timezone.utc)
    return max(due, datetime.now(timezone.utc))


async def import_rows(pool: AsyncConnectionPool, tenant_id: str, rows: list[SheetRow]) -> dict:
    created = updated = 0
    async with pool.connection() as conn:
        async with conn.transaction():
            for r in rows:
                cur = await conn.execute(
                    """INSERT INTO parties (tenant_id, canonical_name, whatsapp_phone, gstin)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (tenant_id, whatsapp_phone)
                       DO UPDATE SET gstin = COALESCE(EXCLUDED.gstin, parties.gstin)
                       RETURNING id""",
                    (tenant_id, r.party, r.phone, r.gstin),
                )
                party_id = (await cur.fetchone())["id"]
                cur = await conn.execute(
                    """INSERT INTO obligations
                         (tenant_id, party_id, invoice_ref, amount_paise, due_date, next_followup_at, state)
                       VALUES (%s, %s, %s, %s, %s, %s, 'ACTIVE')
                       ON CONFLICT (tenant_id, invoice_ref) DO UPDATE
                         SET amount_paise = EXCLUDED.amount_paise, due_date = EXCLUDED.due_date
                       WHERE obligations.state <> 'PAID'
                       RETURNING id, (xmax = 0) AS inserted""",
                    (tenant_id, party_id, r.invoice_ref, r.amount_paise, r.due_date, _followup(r)),
                )
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
