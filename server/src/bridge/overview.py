"""What Smara Desktop shows about the shop it is paired with: the same numbers the owner sees in chat.

Read-only, and for the paired shop only (the caller is authenticated by its device token). Counts and totals, no customer
names or numbers: the helper keeps a copy in a file on the owner's PC.
"""
from datetime import datetime, timezone

from src import clock, tenants
from src.loop.owner_channel import window_open


async def shop_overview(app, tenant_id: str) -> dict | None:
    pool = app.state.pool
    tenant = await tenants.by_id(pool, tenant_id)
    if tenant is None:
        return None
    today = clock.today()
    async with pool.connection() as conn:
        dues = await (await conn.execute(
            """SELECT count(*) FILTER (WHERE state NOT IN ('PAID','DRAFT')) AS open_n,
                      coalesce(sum(amount_paise) FILTER (WHERE state NOT IN ('PAID','DRAFT')), 0) AS open_paise,
                      count(*) FILTER (WHERE state NOT IN ('PAID','DRAFT') AND due_date < %(d)s) AS overdue_n,
                      coalesce(sum(amount_paise) FILTER (WHERE state NOT IN ('PAID','DRAFT') AND due_date < %(d)s), 0) AS overdue_paise,
                      count(*) FILTER (WHERE state = 'WAITING_OWNER_APPROVAL') AS cards
                 FROM obligations WHERE tenant_id = %(t)s""", {"d": today, "t": tenant_id})).fetchone()
        waiting = await (await conn.execute(
            """SELECT (SELECT count(*) FROM proposed_adjustments a JOIN obligations o ON o.id = a.obligation_id
                        WHERE o.tenant_id = %(t)s AND a.status = 'PENDING')
                    + (SELECT count(*) FROM agent_sessions WHERE tenant_id = %(t)s AND status = 'WAITING_APPROVAL') AS n""",
            {"t": tenant_id})).fetchone()
        pay = await (await conn.execute(
            "SELECT count(*) AS n, coalesce(sum(amount_paise), 0) AS paise FROM payables WHERE tenant_id = %s AND paid_at IS NULL",
            (tenant_id,))).fetchone()
        sync = await (await conn.execute(
            "SELECT max(created_at) AS at FROM tally_syncs WHERE tenant_id = %s", (tenant_id,))).fetchone()
    now = datetime.now(timezone.utc)
    return {
        "shop": tenant.name,
        "channels": {"telegram_linked": tenant.telegram_chat_id is not None and getattr(app.state, "tg", None) is not None,
                     "whatsapp_window_open": window_open(tenant, now),
                     "last_used": tenant.owner_last_channel or ""},
        "dues": {"open": dues["open_n"], "open_paise": int(dues["open_paise"]),
                 "overdue": dues["overdue_n"], "overdue_paise": int(dues["overdue_paise"])},
        "payables": {"open": pay["n"], "open_paise": int(pay["paise"])},
        "waiting_for_owner": dues["cards"] + waiting["n"],
        "tally_last_sync_at": sync["at"].isoformat() if sync["at"] else None,
        "as_of": now.isoformat(),
    }
