"""Admin commands in Telegram, for the people who run Smara (ADMIN_TELEGRAM_IDS; if unset, the ops chat OPS_TELEGRAM_CHAT_ID).

An admin invites people from the chat, without a server login:
    /invite                       a one-use code (14 days) and its link, to forward to anyone
    /invite 6765688446 Ramesh     the same, and the bot sends the link to that Telegram user itself
                                  (works for anyone who has written to the bot, e.g. someone on the waitlist)
    /waitlist                     who asked for access and is not invited yet
    /invites                      codes that can still be used
    /revoke SMA-AB12              switch a code off
    /admin                        this list
Anyone else who types these gets the normal behaviour (a stranger lands on the waitlist), so nothing here is reachable by them.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from src.ingest.onboarding import new_code
from src.loop.wa_format import to_telegram_html

log = logging.getLogger(__name__)
COMMANDS = ("invite", "waitlist", "invites", "revoke", "admin")
CODE_DAYS = 14
HELP = ("Smara admin:\n"
        "• /invite : ek invite code + link banata hai (aap kisi ko bhi forward kar sakte hain)\n"
        "• /invite 6765688446 naam : code banakar us Telegram user ko bot khud bhej deta hai\n"
        "• /waitlist : jinhone access maanga aur abhi invite nahi hue\n"
        "• /invites : jo codes abhi chal sakte hain\n"
        "• /revoke CODE : ek code band karta hai")


def admin_ids(settings) -> set[int]:
    ids = set(getattr(settings, "admin_telegram_ids", ()) or ())
    if not ids and getattr(settings, "ops_telegram_chat_id", None):
        ids.add(int(settings.ops_telegram_chat_id))
    return ids


def is_admin(settings, chat_id: int) -> bool:
    return chat_id in admin_ids(settings)


async def _reply(app, chat_id: int, text: str) -> None:
    await app.state.tg.send({"chat_id": chat_id, "text": to_telegram_html(text), "parse_mode": "HTML"})


async def handle(app, chat_id: int, text: str) -> bool:
    """True if `text` was an admin command from an admin (and was answered). Anything else is left to the normal flow."""
    cmd, _, rest = text.strip().partition(" ")
    name = cmd.lstrip("/").split("@")[0].lower()
    if not cmd.startswith("/") or name not in COMMANDS or not is_admin(app.state.settings, chat_id):
        return False
    rest = " ".join(rest.split())
    try:
        if name == "invite":
            await _invite(app, chat_id, rest)
        elif name == "waitlist":
            await _waitlist(app, chat_id)
        elif name == "invites":
            await _invites(app, chat_id)
        elif name == "revoke":
            await _revoke(app, chat_id, rest)
        else:
            await _reply(app, chat_id, HELP)
    except Exception:
        log.exception("admin command failed")
        await _reply(app, chat_id, "Ye command nahi chala. Dobara try kijiye, ya server log dekhiye.")
    return True


async def _make_code(pool, label: str) -> str:
    code = new_code("SMA")
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO invite_codes (code, label, max_uses, expires_at) VALUES (%s, %s, 1, %s)",
                           (code, label[:80], datetime.now(timezone.utc) + timedelta(days=CODE_DAYS)))
    return code


async def _invite(app, chat_id: int, rest: str) -> None:
    target, label = None, rest
    first, _, tail = rest.partition(" ")
    if re.fullmatch(r"\d{5,15}", first):
        target, label = int(first), tail
    code = await _make_code(app.state.pool, label or (f"tg {target}" if target else "admin invite"))
    link = f"https://t.me/{await app.state.tg.username()}?start={code}"
    if target is None:
        return await _reply(app, chat_id, f"Invite ready (ek istemaal, {CODE_DAYS} din):\n{link}\nYa unhe 'JOIN {code}' bhejne ko kahiye.")
    try:
        await app.state.tg.send({"chat_id": target, "disable_web_page_preview": True, "text": (
            "Hi! I'm Smara, an AI assistant. You now have access. To begin, open this link and tap Start: "
            f"\n{link}")})
    except httpx.HTTPError:
        return await _reply(app, chat_id, f"Code {code} ban gaya, lekin Telegram ne us user ko message nahi jaane diya "
                                          f"(unhone bot ko kabhi message nahi kiya, ya bot ko block kiya hai). Ye link unhe bhejiye:\n{link}")
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE waitlist SET invited_at = now() WHERE phone = %s", (f"tg:{target}",))
    await _reply(app, chat_id, f"Invite {target} ko bhej diya ({code}).")


async def _waitlist(app, chat_id: int) -> None:
    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT phone, first_text, messages, created_at FROM waitlist WHERE invited_at IS NULL
                ORDER BY created_at DESC LIMIT 15""")).fetchall()
    if not rows:
        return await _reply(app, chat_id, "Waitlist khali hai.")
    lines = []
    for r in rows:
        who = r["phone"].removeprefix("tg:")
        lines.append(f"• {who}: {r['messages']} msg, \"{(r['first_text'] or '')[:50]}\"" +
                     (f"  -> /invite {who}" if r["phone"].startswith("tg:") else "  (WhatsApp)"))
    await _reply(app, chat_id, "Waitlist (nayi pehle):\n" + "\n".join(lines))


async def _invites(app, chat_id: int) -> None:
    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT code, label, uses, max_uses, expires_at FROM invite_codes
                WHERE revoked_at IS NULL AND uses < max_uses AND (expires_at IS NULL OR expires_at > now())
                ORDER BY created_at DESC LIMIT 15""")).fetchall()
    if not rows:
        return await _reply(app, chat_id, "Abhi koi chalne wala invite code nahi hai.")
    await _reply(app, chat_id, "Chalne wale codes:\n" + "\n".join(
        f"• {r['code']} ({r['label'] or 'no label'}), {r['uses']}/{r['max_uses']} used, {r['expires_at']:%d %b} tak" for r in rows))


async def _revoke(app, chat_id: int, rest: str) -> None:
    code = rest.strip().upper()
    if not re.fullmatch(r"[A-Z]{2,5}-[A-Z0-9]{4,6}", code):
        return await _reply(app, chat_id, "Aise likhiye: /revoke SMA-AB12")
    async with app.state.pool.connection() as conn:
        cur = await conn.execute("UPDATE invite_codes SET revoked_at = now() WHERE code = %s AND revoked_at IS NULL RETURNING code",
                                 (code,))
        done = await cur.fetchone()
    await _reply(app, chat_id, f"{code} band kar diya." if done else f"{code} nahi mila ya pehle se band hai.")


async def tell_admins_about_waitlist(app, m: dict, chat_id: int, text: str) -> None:
    """A stranger asked for access: the admins get one message with what is needed to invite them."""
    frm = m.get("from") or {}
    name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or "?"
    handle = f" (@{frm['username']})" if frm.get("username") else ""
    for admin in admin_ids(app.state.settings):
        try:
            await _reply(app, admin, f"Naya waitlist request: {name}{handle}, Telegram id {chat_id}.\n"
                                     f"Unhone likha: \"{text[:120]}\"\nAccess dene ke liye: /invite {chat_id}")
        except Exception:
            log.warning("could not tell admin %s about the waitlist", admin)
