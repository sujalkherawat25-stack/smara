"""The owner undoes a wrong closure: "reopen <invoice number>" puts a PAID invoice back to ACTIVE.

Deterministic on purpose: exact invoice number, this shop's invoices only, and only invoices that are PAID. The
source becomes REOPENED, so a Tally sync that still does not list the bill will not close it again. If Tally lists
it as due on a later sync, the normal import sets the source back to TALLY. Nothing is sent to anyone by this."""
import logging
import re

log = logging.getLogger(__name__)

PATTERN = re.compile(r"^\s*reopen\s+(?P<ref>\S{1,60})\s*$", re.IGNORECASE)


def parse_reopen_command(text: str) -> str | None:
    """The invoice number when the message is exactly 'reopen <invoice number>', else None."""
    m = PATTERN.match(text or "")
    return m["ref"] if m else None


async def reopen_invoice(pool, tenant_id: str, invoice_ref: str) -> str | None:
    """The reply for the owner, or None when this shop has no paid invoice with that number (agent handles it)."""
    async with pool.connection() as conn:
        async with conn.transaction():
            row = await (await conn.execute(
                """SELECT o.id, o.state, o.invoice_ref, o.amount_paise, p.canonical_name
                     FROM obligations o JOIN parties p ON p.id = o.party_id
                    WHERE o.tenant_id = %s AND o.invoice_ref = %s AND o.state = 'PAID'
                    FOR UPDATE OF o""", (tenant_id, invoice_ref))).fetchone()
            if row is None:
                return None
            await conn.execute(
                """UPDATE obligations SET state = 'ACTIVE', next_followup_at = now(), source = 'REOPENED'
                    WHERE id = %s""", (row["id"],))
            await conn.execute(
                """INSERT INTO obligation_events (obligation_id, from_state, to_state, actor)
                   VALUES (%s, 'PAID', 'ACTIVE', 'OWNER')""", (row["id"],))
    log.info("invoice %s reopened by owner", row["id"])
    return (f"Invoice {row['invoice_ref']} ({row['canonical_name']}) wapas open kar diya. "
            "Ab ye reminders ke liye eligible hai.")
