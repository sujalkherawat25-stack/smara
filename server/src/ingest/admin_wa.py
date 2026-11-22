"""The same admin commands as admin_tg, for the people who run Smara and write from WhatsApp (ADMIN_WHATSAPP_NUMBERS).

    /invite                       a one-use code (20 hours) and a wa.me link, to forward to anyone
    /invite 7877787675 Ramesh     the same, and Smara sends the code to that number itself (it only arrives if they wrote
                                  to Smara in the last 24 hours, as the waitlist does; the code is shown here to forward either way)
    /waitlist                     who asked for access and is not invited yet
    /invites                      codes that can still be used
    /revoke SMA-AB12              switch a code off
    /admin                        this list
Only a message from an admin number is treated as a command; from anyone else the same text is just a message.
"""
import logging
import re
from urllib.parse import quote

import httpx

from src import tenants
from src.config import admin_wa_number
from src.ingest import admin_tg
from src.loop.whatsapp import text_message

log = logging.getLogger(__name__)
COMMANDS = ("invite", "waitlist", "invites", "revoke", "admin")
HELP = ("Smara admin (WhatsApp):\n"
        "• /invite : ek invite code + link banata hai (kisi ko bhi forward kar sakte hain)\n"
        "• /invite 7877787675 naam : code banakar us number par Smara khud bhej deta hai (agar unhone pichhle 24 ghante me likha ho)\n"
        "• /waitlist : jinhone access maanga aur abhi invite nahi hue\n"
        "• /invites : jo codes abhi chal sakte hain\n"
        "• /revoke CODE : ek code band karta hai\n"
        "Har code banne ke 20 ghante baad band ho jata hai aur sirf ek insaan ke liye chalta hai.")


def admin_numbers(settings) -> set[str]:
    return {n for n in (getattr(settings, "admin_whatsapp_numbers", ()) or ()) if n}


def is_admin(settings, sender: str) -> bool:
    return bool(sender) and sender in admin_numbers(settings)


async def _reply(app, to: str, text: str, number_id: str | None = None) -> None:
    await app.state.wa.send(text_message(to, text), number_id=number_id or None)


def _link(settings, code: str) -> str:
    n = getattr(settings, "smara_wa_number", "")
    return f"https://wa.me/{n}?text={quote('JOIN ' + code)}" if n else ""


async def handle(app, msg) -> bool:
    """True if this WhatsApp message was an admin command from an admin number (and was answered)."""
    if msg.channel != "whatsapp" or not is_admin(app.state.settings, msg.sender):
        return False
    cmd, _, rest = (msg.text or "").strip().partition(" ")
    name = cmd.lstrip("/").lower()
    if not cmd.startswith("/") or name not in COMMANDS:
        if await tenants.by_owner_phone(app.state.pool, msg.sender):
            return False                      # an admin who also has a Smara account chats with Smara as usual
        await _reply(app, msg.sender, HELP, msg.number_id)     # any other message from an admin number shows the commands
        return True
    rest = " ".join(rest.split())
    try:
        if name == "invite":
            await _invite(app, msg, rest)
        elif name == "waitlist":
            await _waitlist(app, msg)
        elif name == "invites":
            await _invites(app, msg)
        elif name == "revoke":
            await _revoke(app, msg, rest)
        else:
            await _reply(app, msg.sender, HELP, msg.number_id)
    except Exception:
        log.exception("whatsapp admin command failed")
        await _reply(app, msg.sender, "Ye command nahi chala. Dobara try kijiye, ya server log dekhiye.", msg.number_id)
    return True


async def _invite(app, msg, rest: str) -> None:
    s = app.state.settings
    target, label = None, rest
    first, _, tail = rest.partition(" ")
    if re.fullmatch(r"\+?\d{10,15}", first):
        target, label = admin_wa_number(first), tail
    code, expires = await admin_tg._make_code(app.state.pool, label or (f"wa {target}" if target else "admin invite"))
    link = _link(s, code)
    how = f"Link: {link}\n" if link else ""
    if target is None:
        return await _reply(app, msg.sender, f"Naya invite (sirf ek insaan ke liye, {admin_tg.CODE_HOURS} ghante, {admin_tg._till(expires)} tak):\n"
                                             f"Code: {code}\n{how}Unhe kahiye Smara ko WhatsApp par 'JOIN {code}' bhejein.", msg.number_id)
    try:
        await _reply(app, target, "Hi! I'm Smara, an AI assistant. You now have access. To begin, send me this message: "
                                  f"JOIN {code}")
    except httpx.HTTPError:
        return await _reply(app, msg.sender, f"Code {code} ban gaya, lekin WhatsApp ne {target} ko message nahi jaane diya "
                                             f"(24 ghante se unhone Smara ko nahi likha). Ye unhe bhejiye:\n{how}ya 'JOIN {code}'.", msg.number_id)
    async with app.state.pool.connection() as conn:
        await conn.execute("UPDATE waitlist SET invited_at = now() WHERE phone = %s", (target,))
    await _reply(app, msg.sender, f"Invite {target} ko bhej diya ({code}, {admin_tg._till(expires)} tak). "
                                  f"Agar unhe na mile to ye bhejiye: {link or 'JOIN ' + code}", msg.number_id)


async def _waitlist(app, msg) -> None:
    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT phone, first_text, messages FROM waitlist WHERE invited_at IS NULL
                ORDER BY created_at DESC LIMIT 15""")).fetchall()
    if not rows:
        return await _reply(app, msg.sender, "Waitlist khali hai.", msg.number_id)
    lines = []
    for r in rows:
        tg = r["phone"].startswith("tg:")
        lines.append(f"• {r['phone'].removeprefix('tg:')}{' (Telegram)' if tg else ''}: {r['messages']} msg, "
                     f"\"{(r['first_text'] or '')[:50]}\"" + ("" if tg else f"  -> /invite {r['phone']}"))
    await _reply(app, msg.sender, "Waitlist (nayi pehle):\n" + "\n".join(lines), msg.number_id)


async def _invites(app, msg) -> None:
    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT code, label, uses, max_uses, expires_at FROM invite_codes
                WHERE revoked_at IS NULL AND uses < max_uses AND (expires_at IS NULL OR expires_at > now())
                ORDER BY created_at DESC LIMIT 15""")).fetchall()
    if not rows:
        return await _reply(app, msg.sender, "Abhi koi chalne wala invite code nahi hai.", msg.number_id)
    await _reply(app, msg.sender, "Chalne wale codes:\n" + "\n".join(
        f"• {r['code']} ({r['label'] or 'no label'}), {r['uses']}/{r['max_uses']} used, "
        f"{admin_tg._till(r['expires_at']) if r['expires_at'] else 'kabhi nahi'} tak" for r in rows), msg.number_id)


async def _revoke(app, msg, rest: str) -> None:
    code = rest.strip().upper()
    if not re.fullmatch(r"[A-Z]{2,5}-[A-Z0-9]{4,6}", code):
        return await _reply(app, msg.sender, "Aise likhiye: /revoke SMA-AB12", msg.number_id)
    async with app.state.pool.connection() as conn:
        cur = await conn.execute("UPDATE invite_codes SET revoked_at = now() WHERE code = %s AND revoked_at IS NULL RETURNING code", (code,))
        done = await cur.fetchone()
    await _reply(app, msg.sender, f"{code} band kar diya." if done else f"{code} nahi mila ya pehle se band hai.", msg.number_id)


async def tell_admins_about_waitlist(app, phone: str, name: str, text: str) -> None:
    """Someone wrote to Smara's WhatsApp number without an invite: every admin (Telegram, and WhatsApp numbers that
    wrote in the last 24 hours) gets one message with what is needed to invite them."""
    note = (f"Naya WhatsApp waitlist request: {name or '?'}, +{phone}.\nUnhone likha: \"{text[:120]}\"\n"
            f"Access dene ke liye: /invite {phone}")
    if getattr(app.state, "tg", None) is not None:
        for admin in admin_tg.admin_ids(app.state.settings):
            try:
                await admin_tg._reply(app, admin, note)
            except Exception:
                log.warning("could not tell admin %s about the WhatsApp waitlist", admin)
    for number in admin_numbers(app.state.settings):
        try:
            await _reply(app, number, note)
        except Exception:
            log.info("WhatsApp admin %s not reachable now (outside the 24h window)", number[-4:])
