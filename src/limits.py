"""Daily limits per shop, so one heavy or abusive user cannot stall everyone on the shared model key.

Counted from the last 24 hours. Shops created before limits existed, and any shop flagged limits_exempt, are not limited.
Button taps (approvals) are never limited: an owner must always be able to approve or reject what is waiting.
"""
import logging

log = logging.getLogger(__name__)
DEFAULTS = {"msgs": 300, "files": 30, "tokens": 4_000_000}


def kind_of(msg) -> str:
    if msg.kind in ("document", "image"):
        return "file"
    if msg.kind == "audio":
        return "audio"
    return "text"


async def record(pool, tenant_id: str, kind: str) -> None:
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO owner_inbound_log (tenant_id, kind) VALUES (%s, %s)", (tenant_id, kind))


async def check(app, tenant, msg) -> str | None:
    """None if the owner may go on, else the polite sentence to send instead of running the agent.
    The current message is already in the log, so "more than the limit" means this one is over it."""
    if msg.button_id or getattr(tenant, "limits_exempt", False):
        return None
    s = app.state.settings
    lim = {"msgs": getattr(s, "owner_daily_msgs", DEFAULTS["msgs"]), "files": getattr(s, "owner_daily_files", DEFAULTS["files"]),
           "tokens": getattr(s, "owner_daily_tokens", DEFAULTS["tokens"])}
    async with app.state.pool.connection() as conn:
        row = await (await conn.execute(
            """SELECT (SELECT count(*) FROM owner_inbound_log WHERE tenant_id = %(t)s AND created_at > now() - interval '24 hours') AS msgs,
                      (SELECT count(*) FROM owner_inbound_log WHERE tenant_id = %(t)s AND kind = 'file'
                          AND created_at > now() - interval '24 hours') AS files,
                      (SELECT coalesce(sum(total_tokens), 0) FROM llm_usage WHERE tenant_id = %(t)s
                          AND created_at > now() - interval '24 hours') AS tokens""", {"t": tenant.id})).fetchone()
    if kind_of(msg) == "file" and row["files"] > lim["files"]:
        return "Aaj ke liye files ki limit poori ho gayi. Kal phir bhej dijiye, ya zaroori ho to hume bataiye."
    if row["msgs"] > lim["msgs"] or row["tokens"] >= lim["tokens"]:
        return ("Aaj ki limit poori ho gayi. Kal subah se phir pehle jaisa chalega. Zaroori kaam ho to 'help' likhiye "
                "ya hume bataiye, hum limit badha denge.")
    return None
