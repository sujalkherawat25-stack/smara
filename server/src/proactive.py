"""Messages Smara starts by itself (daily collection cards for overdue invoices, the morning summary).

They go out only for owners who turned them on, and never more than CARDS_PER_DAY collection cards a day for one shop.
Reminders the owner asked for ('remind me at 9') are not affected: they are the owner's own task. Production starts with
every shop off (PROACTIVE_DEFAULT unset); a bare Settings() (tests, local runs) keeps them on."""
CARDS_PER_DAY = 8


async def enabled(app_or_pool, tenant_id: str, settings=None) -> bool:
    if settings is not None and getattr(settings, "proactive_default", True):
        return True
    pool = getattr(getattr(app_or_pool, "state", None), "pool", app_or_pool)
    async with pool.connection() as conn:
        return await (await conn.execute("SELECT 1 FROM proactive_optin WHERE tenant_id = %s", (tenant_id,))).fetchone() is not None


async def set_on(pool, tenant_id: str, on: bool) -> None:
    async with pool.connection() as conn:
        if on:
            await conn.execute("INSERT INTO proactive_optin (tenant_id) VALUES (%s) ON CONFLICT DO NOTHING", (tenant_id,))
        else:
            await conn.execute("DELETE FROM proactive_optin WHERE tenant_id = %s", (tenant_id,))


async def set_proactive_messages(app, ctx, args: dict) -> dict:
    """The agent's tool: the owner asked in chat to turn the daily collection cards and the morning summary on or off."""
    on = args.get("on")
    if not isinstance(on, bool):
        return {"error": "give on = true or false"}
    await set_on(app.state.pool, ctx.tenant_id, on)
    if on:
        return {"saved": True, "on": True, "note_for_you": "Confirm in one line: from the next working morning Smara will send up to "
                f"{CARDS_PER_DAY} reminder cards a day for overdue invoices (each needs their tap) and a morning summary; "
                "they can say 'band karo' any time."}
    return {"saved": True, "on": False, "note_for_you": "Confirm in one line that Smara will not start messages by itself any more "
            "(reminders they ask for still come)."}
