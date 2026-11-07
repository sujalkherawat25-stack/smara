"""Transactional outbox for Continuum writes (idempotent on source_id)."""
import json
import logging
from datetime import datetime, timedelta

from psycopg_pool import AsyncConnectionPool

from src.bridge.continuum import ContinuumClient

log = logging.getLogger(__name__)
LEASE = timedelta(minutes=5)
MAX_ATTEMPTS = 20


async def enqueue(conn, *, user_id: str, source_id: str, text: str, trusted: bool,
                  metadata: dict | None = None, stamp_obligation_id: str | None = None) -> None:
    """Queue a write on `conn` so it commits atomically with the caller's change."""
    await conn.execute(
        """INSERT INTO continuum_outbox (user_id, source_id, text, trusted, metadata, stamp_obligation_id)
           VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (source_id) DO NOTHING""",
        (user_id, source_id, text, trusted, json.dumps(metadata or {}), stamp_obligation_id))


async def enqueue_standalone(pool: AsyncConnectionPool, **kw) -> None:
    async with pool.connection() as conn:
        await enqueue(conn, **kw)


def _backoff(attempts: int) -> timedelta:
    return min(timedelta(seconds=30 * 2 ** attempts), timedelta(hours=1))


async def drain(pool: AsyncConnectionPool, continuum: ContinuumClient, now: datetime, limit: int = 20) -> dict:
    """Deliver due rows, in order per party: a row waits while an earlier row for
    the same user_id is still PENDING (retrying or in flight), so Continuum never
    sees an owner decision before the claim it refers to. Rows are leased (next_attempt_at pushed out) before the
    network call, so overlapping workers never double-send and no lock is held
    across HTTP."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """UPDATE continuum_outbox SET next_attempt_at = %s
                WHERE id IN (SELECT o.id FROM continuum_outbox o
                              WHERE o.status = 'PENDING' AND o.next_attempt_at <= %s
                                AND NOT EXISTS (SELECT 1 FROM continuum_outbox e
                                                 WHERE e.user_id = o.user_id AND e.status = 'PENDING' AND e.id < o.id)
                              ORDER BY o.id LIMIT %s FOR UPDATE OF o SKIP LOCKED)
            RETURNING *""", (now + LEASE, now, limit))
        rows = await cur.fetchall()
    done = failed = 0
    for r in rows:
        try:
            await continuum.write_evidence(user_id=r["user_id"], source_id=r["source_id"], text=r["text"],
                                           trusted=r["trusted"], metadata=r["metadata"])
        except Exception as e:
            failed += 1
            attempts = r["attempts"] + 1
            log.warning("continuum outbox %s failed (attempt %s): %s", r["source_id"], attempts, e)
            async with pool.connection() as conn:
                await conn.execute(
                    """UPDATE continuum_outbox SET attempts = %s, last_error = %s, next_attempt_at = %s, status = %s
                        WHERE id = %s""",
                    (attempts, str(e)[:300], now + _backoff(attempts),
                     "DEAD" if attempts >= MAX_ATTEMPTS else "PENDING", r["id"]))
            continue
        done += 1
        async with pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE continuum_outbox SET status = 'DONE', last_error = NULL WHERE id = %s", (r["id"],))
                if r["stamp_obligation_id"]:
                    await conn.execute("UPDATE obligations SET last_continuum_source_id = %s WHERE id = %s",
                                       (r["source_id"], r["stamp_obligation_id"]))
    return {"delivered": done, "failed": failed}


async def stats(pool: AsyncConnectionPool) -> dict:
    """Backlog numbers for /healthz and alerting."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT count(*) FILTER (WHERE status = 'PENDING') AS pending,
                      count(*) FILTER (WHERE status = 'DEAD') AS dead,
                      coalesce(extract(epoch FROM now() - min(created_at) FILTER (WHERE status = 'PENDING')), 0)::int
                        AS oldest_pending_s
                 FROM continuum_outbox""")
        return await cur.fetchone()
