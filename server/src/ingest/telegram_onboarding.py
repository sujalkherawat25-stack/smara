"""A new person joins from Telegram. Nothing is asked.

An invite link (t.me/<bot>?start=<code>), "JOIN <code>", or SIGNUP_MODE=open creates the account on the spot. The name is the
one Telegram already shows for the person; there is no phone number, shop name or UPI ID to type. Smara's first message says
what it can do and asks one easy question, and from there it learns about the person in the normal conversation.

The account is keyed "tg<chat id>" (there is no phone number to key it by). Someone with no invite is put on the waitlist and
the admins can invite them with one tap (admin_tg.py).
"""
import logging

from src import tenants
from src.ingest.onboarding import _code_from, _valid_invite, display_name, welcome_done
from src.loop.wa_format import to_telegram_html
from src.ops_alert import ops_alert

log = logging.getLogger(__name__)
NOT_INVITED = ("Hi! I'm Smara, an AI assistant for anyone who works, studies or runs something: writing, research, files, "
               "plans, reminders and more.\n\nAccess is by invite for now. You're on the waitlist, and I'll message you here "
               "the moment you're in.\n\nIf you have an invite code, send 'JOIN CODE' here.")
STILL_WAITING = "You're still on the waitlist. I'll message you here as soon as you have access. If you have an invite code, send 'JOIN CODE'."
BAD_INVITE = "That invite code no longer works. Please ask for a new one."
LEGACY_STATES = ("ASK_PHONE", "ASK_SHOP", "ASK_UPI")     # people who were in the old question chat are let straight in


def session_key(chat_id: int) -> str:
    return f"tg:{chat_id}"


def owner_key(chat_id: int) -> str:
    """The account's own key (it stands where a phone number stands on WhatsApp)."""
    return f"tg{chat_id}"


async def _say(app, chat_id: int, text: str, keys: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": to_telegram_html(text), "parse_mode": "HTML"}
    if keys is not None:
        payload["reply_markup"] = keys
    await app.state.tg.send(payload)


async def handle(app, m: dict, chat_id: int, text: str) -> None:
    """A private Telegram chat that is not linked to any account wrote to the bot."""
    pool, key = app.state.pool, session_key(chat_id)
    async with pool.connection() as conn:
        sess = await (await conn.execute("SELECT * FROM onboarding_sessions WHERE phone = %s", (key,))).fetchone()
    if sess and sess["state"] in LEGACY_STATES:
        return await _join(app, m, chat_id, sess["invite_code"])

    code = _code_from(text)
    invite = await _valid_invite(pool, code, key) if code else None
    if invite or getattr(app.state.settings, "signup_mode", "invite") == "open":
        return await _join(app, m, chat_id, invite["code"] if invite else None)

    async with pool.connection() as conn:           # kept for whoever decides who to invite
        cur = await conn.execute(
            """INSERT INTO waitlist (phone, first_text, last_text) VALUES (%s, %s, %s)
               ON CONFLICT (phone) DO UPDATE SET last_text = EXCLUDED.last_text, messages = waitlist.messages + 1
               RETURNING (xmax = 0) AS fresh""", (key, text[:300], text[:300]))
        fresh = (await cur.fetchone())["fresh"]
    if fresh:                                       # the first time: the full welcome, and the admins can invite with one tap
        await _say(app, chat_id, NOT_INVITED)
        from src.ingest import admin_tg
        await admin_tg.tell_admins_about_waitlist(app, m, chat_id, text)
    else:
        await _say(app, chat_id, STILL_WAITING)


async def _join(app, m: dict, chat_id: int, code: str | None) -> None:
    pool, key, phone = app.state.pool, session_key(chat_id), owner_key(chat_id)
    who = m.get("from") or {}
    name = display_name(" ".join(p for p in (who.get("first_name"), who.get("last_name")) if p))
    if existing := await tenants.by_owner_phone(pool, phone):      # they left with 'telegram off' and are back: same account
        async with pool.connection() as conn:
            await conn.execute("UPDATE tenants SET telegram_chat_id = %s, owner_last_channel = 'telegram' WHERE id = %s",
                               (chat_id, existing.id))
        return await _say(app, chat_id, f"Welcome back, {existing.name}! Just write to me here.")
    async with pool.connection() as conn:
        async with conn.transaction():
            if code:                                   # take the invite use atomically, as on WhatsApp
                ok = await (await conn.execute(
                    """UPDATE invite_codes SET uses = uses + 1 WHERE code = %s AND revoked_at IS NULL AND uses < max_uses
                          AND (expires_at IS NULL OR expires_at > now()) RETURNING code""", (code,))).fetchone()
                if ok is None:
                    await conn.execute("UPDATE onboarding_sessions SET state = 'DONE', updated_at = now() WHERE phone = %s", (key,))
                    return await _say(app, chat_id, BAD_INVITE)
            await conn.execute(
                """INSERT INTO onboarding_sessions (phone, state, invite_code) VALUES (%s, 'DONE', %s)
                   ON CONFLICT (phone) DO UPDATE SET state = 'DONE', updated_at = now()""", (key, code))
            await conn.execute("UPDATE waitlist SET invited_at = now() WHERE phone = %s", (key,))
    t = await tenants.create(pool, name=name, owner_phone=phone)
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE tenants SET joined_with_code = %s, telegram_chat_id = %s, owner_last_channel = 'telegram' WHERE id = %s",
            (code, chat_id, t.id))
    await _say(app, chat_id, welcome_done(name))
    log.info("new account %s joined on Telegram via %s", t.id, code or "open sign-up")
    await ops_alert(app, f"new-shop-{t.id}", f"Smara: someone new joined on Telegram: {name}.")
