"""Dual-write coordinator: Postgres (what/when) <-> Continuum (why/proof).

Invariant: a debtor message never mutates obligations. It only creates a
PENDING proposed_adjustments row; obligations change in `decide_adjustment`,
which is reachable only from an owner-authenticated button tap.

Every Continuum write goes through the outbox in the same transaction as the
Postgres change, so a Continuum outage delays proof but never loses a claim.
"""
from datetime import datetime, time, timezone

from psycopg_pool import AsyncConnectionPool

from src.bridge.continuum import make_source_id, scope_user
from src.bridge.outbox import enqueue

TRUSTED_AFTER_APPROVAL = "Owner {verb} {kind} on invoice {ref}: {evidence}"


async def submit_debtor_claim(
    pool: AsyncConnectionPool,
    *,
    tenant_id: str,
    party_id: str,
    obligation_id: str,
    wamid: str,
    adjustment_type: str,
    evidence_text: str,
    proposed_amount_paise: int | None = None,
    proposed_date=None,
) -> tuple[str, bool]:
    """Steps 1-2 of the untrusted-input gate. Returns (adjustment_id, created).

    Evidence is queued (untrusted) and the PENDING row is written atomically.
    A retry of the same wamid hits the UNIQUE source_id and returns
    created=False so the owner is not pinged twice.
    """
    source_id = make_source_id(tenant_id, wamid, evidence_text)
    async with pool.connection() as conn:
        async with conn.transaction():
            await enqueue(
                conn, user_id=scope_user(tenant_id, party_id), source_id=source_id,
                text=evidence_text, trusted=False,
                metadata={"party_id": party_id, "obligation_id": obligation_id, "actor": "DEBTOR"})
            cur = await conn.execute(
                """INSERT INTO proposed_adjustments
                     (obligation_id, adjustment_type, proposed_amount_paise,
                      proposed_date, raw_evidence_text, continuum_source_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (continuum_source_id) DO NOTHING
                   RETURNING id""",
                (obligation_id, adjustment_type, proposed_amount_paise,
                 proposed_date, evidence_text, source_id))
            row = await cur.fetchone()
            if row:
                return str(row["id"]), True
            cur = await conn.execute(
                "SELECT id FROM proposed_adjustments WHERE continuum_source_id = %s", (source_id,))
            return str((await cur.fetchone())["id"]), False


async def decide_adjustment(
    pool: AsyncConnectionPool,
    *,
    tenant_id: str,
    adjustment_id: str,
    approve: bool,
) -> dict | None:
    """Step 4: owner decision. Returns None if unknown, foreign, or already decided."""
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """SELECT a.*, o.state AS ob_state, o.invoice_ref, o.party_id
                     FROM proposed_adjustments a
                     JOIN obligations o ON o.id = a.obligation_id
                    WHERE a.id = %s AND o.tenant_id = %s AND a.status = 'PENDING'
                    FOR UPDATE OF a, o""",
                (adjustment_id, tenant_id))
            adj = await cur.fetchone()
            if adj is None:
                return None
            to_state = await _apply(conn, adj) if approve else adj["ob_state"]
            await conn.execute(
                "UPDATE proposed_adjustments SET status = %s, decided_at = now() WHERE id = %s",
                ("APPROVED" if approve else "REJECTED", adjustment_id))
            await conn.execute(
                """INSERT INTO obligation_events
                     (obligation_id, from_state, to_state, actor, continuum_source_id)
                   VALUES (%s, %s, %s, 'OWNER', %s)""",
                (adj["obligation_id"], adj["ob_state"], to_state, adj["continuum_source_id"]))
            await enqueue(
                conn, user_id=scope_user(tenant_id, str(adj["party_id"])),
                source_id=f"{adj['continuum_source_id']}_owner_{'ok' if approve else 'no'}",
                text=TRUSTED_AFTER_APPROVAL.format(
                    verb="approved" if approve else "rejected", kind=adj["adjustment_type"],
                    ref=adj["invoice_ref"], evidence=adj["raw_evidence_text"]),
                trusted=True, stamp_obligation_id=str(adj["obligation_id"]),
                metadata={"actor": "OWNER", "adjustment_id": str(adj["id"])})
    return {"adjustment_id": adjustment_id, "approved": approve, "invoice_ref": adj["invoice_ref"]}


async def _apply(conn, adj: dict) -> str:
    """Mutate the obligation per adjustment type; returns the resulting state."""
    oid, kind = adj["obligation_id"], adj["adjustment_type"]
    if kind == "DISCOUNT_CLAIM" and adj["proposed_amount_paise"] is not None:
        await conn.execute(
            "UPDATE obligations SET amount_paise = %s WHERE id = %s",
            (adj["proposed_amount_paise"], oid))
    elif kind == "PROMISE_DATE_CHANGE" and adj["proposed_date"] is not None:
        followup = datetime.combine(adj["proposed_date"], time.min, tzinfo=timezone.utc)
        await conn.execute(
            "UPDATE obligations SET due_date = %s, next_followup_at = %s, state = 'ACTIVE' WHERE id = %s",
            (adj["proposed_date"], followup, oid))
        return "ACTIVE"
    elif kind == "UTR_PROOF":
        await conn.execute(
            "UPDATE obligations SET state = 'PAID', next_followup_at = NULL WHERE id = %s", (oid,))
        return "PAID"
    return adj["ob_state"]
