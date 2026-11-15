"""The Smara section of the private Syntarus operator dashboard.

The Syntarus console (MemoryOS) asks for this once a minute at most, with a shared token, and shows it in its own section next
to the Continuum numbers. Metadata only: counts, times, names of accounts, status of runs. Never message text, files, memory
contents, phone numbers (only the last four digits), tokens or keys.
"""
import re


from src import health_watch

MAX_ACCOUNTS = 200
MAX_RUNS = 40


def _tail(phone: str | None) -> str:
    """'telegram' for an account that has no phone number, else the last four digits."""
    if not phone or re.match(r"^tg\d+$", phone):
        return "Telegram only"
    return "…" + re.sub(r"\D", "", phone)[-4:]


async def build(pool, days: int, health: dict) -> dict:
    days = max(1, min(int(days), 90))
    window = f"now() - make_interval(days => {days})"
    async with pool.connection() as conn:
        async def one(sql, *a):
            return await (await conn.execute(sql, a)).fetchone()

        async def many(sql, *a):
            return await (await conn.execute(sql, a)).fetchall()

        totals = await one(f"""
            SELECT (SELECT count(*) FROM tenants) AS accounts,
                   (SELECT count(*) FROM tenants WHERE created_at > {window}) AS new_accounts,
                   (SELECT count(DISTINCT tenant_id) FROM chat_messages WHERE role = 'owner' AND created_at > {window}) AS active_accounts,
                   (SELECT count(*) FROM tenants WHERE telegram_chat_id IS NOT NULL) AS on_telegram,
                   (SELECT count(*) FROM tenants WHERE owner_phone !~ '^tg[0-9]+$') AS with_phone,
                   (SELECT count(*) FROM chat_messages WHERE role = 'owner' AND created_at > {window}) AS owner_messages,
                   (SELECT count(*) FROM chat_messages WHERE role = 'smara' AND created_at > {window}) AS smara_messages,
                   (SELECT count(*) FROM owner_skills) AS skills,
                   (SELECT count(DISTINCT tenant_id) FROM owner_skills) AS accounts_with_skills""")
        llm = await one(f"""SELECT count(*) AS calls, coalesce(sum(prompt_tokens), 0) AS prompt_tokens,
                                   coalesce(sum(completion_tokens), 0) AS completion_tokens,
                                   coalesce(sum(total_tokens), 0) AS total_tokens
                              FROM llm_usage WHERE created_at > {window}""")
        by_model = await many(f"""SELECT model, count(*) AS calls, coalesce(sum(total_tokens), 0) AS tokens
                                    FROM llm_usage WHERE created_at > {window} GROUP BY model ORDER BY tokens DESC LIMIT 8""")
        runs = await many(f"""SELECT status, count(*) AS n FROM agent_sessions
                               WHERE created_at > {window} AND trust = 'TRUSTED' GROUP BY status""")
        waitlist = await one("""SELECT count(*) FILTER (WHERE invited_at IS NULL) AS waiting,
                                       count(*) FILTER (WHERE invited_at IS NOT NULL) AS invited FROM waitlist""")
        invites = await one("""SELECT count(*) FILTER (WHERE revoked_at IS NULL AND uses < max_uses
                                                         AND (expires_at IS NULL OR expires_at > now())) AS open,
                                      count(*) FILTER (WHERE uses >= max_uses) AS used,
                                      count(*) FILTER (WHERE revoked_at IS NOT NULL) AS revoked FROM invite_codes""")
        daily = await many(f"""
            SELECT d::date AS day,
                   coalesce(m.owner_messages, 0) AS owner_messages, coalesce(m.smara_messages, 0) AS smara_messages,
                   coalesce(m.active, 0) AS active_accounts, coalesce(u.tokens, 0) AS tokens, coalesce(u.calls, 0) AS calls
              FROM generate_series(date_trunc('day', now()) - make_interval(days => {days - 1}), date_trunc('day', now()), '1 day') d
              LEFT JOIN (SELECT date_trunc('day', created_at) AS day,
                                count(*) FILTER (WHERE role = 'owner') AS owner_messages,
                                count(*) FILTER (WHERE role = 'smara') AS smara_messages,
                                count(DISTINCT tenant_id) FILTER (WHERE role = 'owner') AS active
                           FROM chat_messages WHERE created_at > {window} GROUP BY 1) m ON m.day = d
              LEFT JOIN (SELECT date_trunc('day', created_at) AS day, sum(total_tokens) AS tokens, count(*) AS calls
                           FROM llm_usage WHERE created_at > {window} GROUP BY 1) u ON u.day = d
             ORDER BY d""")
        accounts = await many(f"""
            SELECT t.id, t.name, t.owner_phone, t.owner_last_channel AS channel, t.joined_with_code, t.created_at,
                   t.limits_exempt,
                   (SELECT max(created_at) FROM chat_messages c WHERE c.tenant_id = t.id AND c.role = 'owner') AS last_active,
                   (SELECT count(*) FROM chat_messages c WHERE c.tenant_id = t.id AND c.role = 'owner' AND c.created_at > {window}) AS messages,
                   (SELECT count(*) FROM chat_messages c WHERE c.tenant_id = t.id AND c.role = 'owner') AS messages_total,
                   (SELECT coalesce(sum(total_tokens), 0) FROM llm_usage l WHERE l.tenant_id = t.id AND l.created_at > {window}) AS tokens,
                   (SELECT count(*) FROM agent_sessions s WHERE s.tenant_id = t.id AND s.trust = 'TRUSTED' AND s.created_at > {window}) AS runs,
                   (SELECT count(*) FROM agent_sessions s WHERE s.tenant_id = t.id AND s.status = 'FAILED' AND s.created_at > {window}) AS failed_runs,
                   (SELECT count(*) FROM owner_skills k WHERE k.tenant_id = t.id) AS skills
              FROM tenants t ORDER BY last_active DESC NULLS LAST, t.created_at DESC LIMIT {MAX_ACCOUNTS}""")
        recent = await many(f"""
            SELECT s.created_at, s.updated_at, s.channel, s.trigger, s.trust, s.status, s.steps, t.name,
                   (SELECT coalesce(sum(total_tokens), 0) FROM llm_usage l WHERE l.session_id = s.id) AS tokens
              FROM agent_sessions s JOIN tenants t ON t.id = s.tenant_id
             WHERE s.trust <> 'UNTRUSTED' ORDER BY s.created_at DESC LIMIT {MAX_RUNS}""")
        events = await many("""
            SELECT o.created_at, o.kind, o.status, t.name FROM outbound_messages o JOIN tenants t ON t.id = o.tenant_id
             WHERE o.status IN ('failed', 'held') ORDER BY o.created_at DESC LIMIT 15""")

    return {
        "scope": "operator_metadata_only",
        "days": days,
        "totals": dict(totals),
        "llm": {**dict(llm), "by_model": [dict(r) for r in by_model]},
        "runs": {r["status"]: r["n"] for r in runs},
        "waitlist": dict(waitlist),
        "invites": dict(invites),
        "daily": [{**dict(r), "day": r["day"].isoformat()} for r in daily],
        "accounts": [{**{k: v for k, v in dict(a).items() if k not in ("id", "owner_phone")},
                      "account": str(a["id"])[:8], "contact": _tail(a["owner_phone"])} for a in accounts],
        "recent_runs": [{**dict(r), "seconds": max(0, round((r["updated_at"] - r["created_at"]).total_seconds()))} for r in recent],
        "delivery_problems": [dict(r) for r in events],
        "errors": health_watch.snapshot(),
        "health": health,
        "privacy": {"message_text_included": False, "files_included": False, "phone_numbers": "last four digits only"},
    }
