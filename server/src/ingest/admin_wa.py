"""The same admin commands as admin_tg, for the people who run Smara and write from WhatsApp (ADMIN_WHATSAPP_NUMBERS).

    /approve 9000000001           let someone on the waitlist in, with no code: the account is made at once and Smara tells
                                  them the wait is over (if WhatsApp will not carry that yet, when they next write).
                                  The same by tapping "Approve" on a new request, or a name in the /waitlist list
    /invite                       a one-use code (20 hours) and a wa.me link, to forward to anyone
    /invite 7877787675 Ramesh     the same, and Smara sends the code to that number itself (it only arrives if they wrote
                                  to Smara in the last 24 hours, as the waitlist does; the code is shown here to forward either way)
    /waitlist                     who asked for access and is not invited yet
    /invites                      codes that can still be used
    /revoke SMA-AB12              switch a code off
    /limit 9000000001             what that account used in the last 24 hours, with buttons to give it more
    /limit 9000000001 3 7         3 times the normal daily limits for 7 days (2 to 10 times, 1 to 90 days; "normal" ends it).
                                  Smara sends such a button by itself when someone is stopped by a limit
    /admin                        this list
Only a message from an admin number is treated as a command; from anyone else the same text is just a message.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4

import httpx

from src import limits, tenants
from src.config import admin_wa_number
from src.ingest import admin_tg, onboarding
from src.loop.outbound import record
from src.loop.owner_channel import send_to_owner
from src.loop.whatsapp import template_message, text_message
from src.ops_alert import ops_alert

log = logging.getLogger(__name__)
COMMANDS = ("approve", "invite", "waitlist", "invites", "revoke", "limit", "admin")
APPROVE_BUTTON = "adm:approve:"      # followed by the number; only ever acted on when an admin number sends it
LIMIT_BUTTON = "adm:limit:"          # followed by <account>:<times>:<days>; the same rule
LIMIT_NAMES = {"msgs": "messages", "files": "files", "tokens": "AI tokens"}
WINDOW = timedelta(hours=23, minutes=50)      # a little inside WhatsApp's 24 hours
HELP = ("Smara admin (WhatsApp):\n"
        "• /waitlist : jinhone access maanga; list me naam chun kar approve kijiye\n"
        "• /approve 9000000001 : waitlist wale ko bina code turant access (Smara unhe khud bata deta hai)\n"
        "• /invite : ek invite code + link banata hai (kisi ko bhi forward kar sakte hain)\n"
        "• /invite 7877787675 naam : code banakar us number par Smara khud bhej deta hai (agar unhone pichhle 24 ghante me likha ho)\n"
        "• /invites : jo codes abhi chal sakte hain\n"
        "• /revoke CODE : ek code band karta hai\n"
        "• /limit 9000000001 : us user ka 24 ghante ka istemaal, aur limit badhane ke button\n"
        "• /limit 9000000001 3 7 : 7 din ke liye 3 guna limit ('/limit 9000000001 normal' se wapas)\n"
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
    if (msg.button_id or "").startswith(APPROVE_BUTTON):
        cmd, name, rest = "/approve", "approve", msg.button_id.removeprefix(APPROVE_BUTTON)
    elif (msg.button_id or "").startswith(LIMIT_BUTTON):
        cmd, name, rest = "/limit", "limit", " ".join(msg.button_id.removeprefix(LIMIT_BUTTON).rsplit(":", 2))
    if not cmd.startswith("/") or name not in COMMANDS:
        if await tenants.by_owner_phone(app.state.pool, msg.sender):
            return False                      # an admin who also has a Smara account chats with Smara as usual
        await _reply(app, msg.sender, HELP, msg.number_id)     # any other message from an admin number shows the commands
        return True
    rest = " ".join(rest.split())
    try:
        if name == "approve":
            await _approve(app, msg, rest)
        elif name == "invite":
            await _invite(app, msg, rest)
        elif name == "waitlist":
            await _waitlist(app, msg)
        elif name == "invites":
            await _invites(app, msg)
        elif name == "revoke":
            await _revoke(app, msg, rest)
        elif name == "limit":
            await _limit(app, msg, rest)
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


def _number(text: str) -> str | None:
    """A phone number typed with or without +, spaces or dashes (10 digits are taken as Indian); None if it is not one."""
    if not re.fullmatch(r"\+?[\d\s\-()]+", text.strip()):
        return None
    number = admin_wa_number(text)
    return number if 10 <= len(number) <= 15 else None


def approved_message(name: str) -> str:
    """What a person reads when their wait ends. Everything in it is something Smara does today."""
    first = name.split(" ")[0] if name and name != "Friend" else ""
    return (f"🎉 Hi{' ' + first if first else ''}! Your wait is over: you now have access to Smara.\n\n"
            "I'm your AI assistant here on WhatsApp. Write to me like you would to a sharp colleague, in English, Hindi or "
            "Hinglish, by text or voice note. I can:\n"
            "• Write, fix and translate messages, emails and documents\n"
            "• Read the PDFs, photos, spreadsheets and voice notes you send\n"
            "• Make Excel sheets, PDFs, Word files and charts\n"
            "• Research, plan, and remind you when it matters\n\n"
            "I only act with your OK. I never move money and I never need your passwords.\n\n"
            "No code needed. Just reply with the first thing you'd like help with.")


async def _tell_approved(app, tenant, phone: str, name: str, wrote_recently: bool) -> str:
    """'now' if the message went out; otherwise it is kept and goes out when they next write ('template' if an approved
    template told them to in the meantime, else 'later')."""
    payload = text_message(phone, approved_message(name))
    if wrote_recently:
        try:
            # kept like a note: if WhatsApp refuses it later, it is sent again when they next write
            await send_to_owner(app, tenant, payload, kind="owner_note", reply_channel="whatsapp")
            return "now"
        except httpx.HTTPError:
            log.warning("approval message not accepted for %s", phone[-4:])
    await record(app.state.pool, f"held:{uuid4()}", tenant_id=tenant.id, kind="owner_note", to_phone=tenant.owner_phone,
                 status="held", payload=payload)                  # sent by resend_undelivered on their next message
    template = getattr(app.state.settings, "wa_approved_template", "")
    if template:
        try:
            await app.state.wa.send(template_message(phone, template, app.state.settings.wa_reminder_lang, [name]))
            return "template"
        except httpx.HTTPError:
            log.warning("approval template not accepted for %s", phone[-4:])
    return "later"


async def _approve(app, msg, rest: str) -> None:
    phone = _number(rest)
    if phone is None:
        return await _reply(app, msg.sender, "Aise likhiye: /approve 9000000001 (ya /waitlist bhej kar naam chuniye)", msg.number_id)
    pool = app.state.pool
    if await tenants.by_owner_phone(pool, phone):
        return await _reply(app, msg.sender, f"{phone} ka account pehle se hai.", msg.number_id)
    async with pool.connection() as conn:
        waiting = await (await conn.execute("SELECT name, last_seen_at FROM waitlist WHERE phone = %s", (phone,))).fetchone()
    if waiting is None:             # only people who asked: a mistyped number must not get an account
        return await _reply(app, msg.sender, f"{phone} waitlist me nahi hai. Naye insaan ke liye: /invite {phone}", msg.number_id)
    name = onboarding.display_name(waiting["name"])
    tenant = await tenants.create(pool, name=name, owner_phone=phone)
    async with pool.connection() as conn:
        await conn.execute("UPDATE waitlist SET approved_at = now(), invited_at = now() WHERE phone = %s", (phone,))
        await conn.execute("DELETE FROM onboarding_sessions WHERE phone = %s", (phone,))
    seen = waiting["last_seen_at"]
    how = await _tell_approved(app, tenant, phone, name, bool(seen) and datetime.now(timezone.utc) - seen < WINDOW)
    log.info("waitlist approval: account %s, message %s", tenant.id, how)
    who = f"{name} ({phone})" if waiting["name"] else phone
    number = getattr(app.state.settings, "smara_wa_number", "")
    link = f"\nAbhi batana ho to ye link bhej dijiye: https://wa.me/{number}?text=Hi" if number and how != "now" else ""
    told = {"now": "Unhe 'wait khatam' wala message bhej diya.",
            "template": "Unhone 24 ghante se nahi likha, isliye WhatsApp template bheja; poora message unke reply par jayega.",
            "later": "Unhone 24 ghante se nahi likha, isliye WhatsApp abhi message nahi jaane deta; jab wo likhenge tab jayega."}[how]
    await _reply(app, msg.sender, f"✅ {who} approved. Account ban gaya, code ki zaroorat nahi. {told}{link}", msg.number_id)


async def _waitlist(app, msg) -> None:
    async with app.state.pool.connection() as conn:
        rows = await (await conn.execute(
            """SELECT phone, name, first_text, messages FROM waitlist WHERE invited_at IS NULL
                ORDER BY created_at DESC LIMIT 15""")).fetchall()
        total = (await (await conn.execute("SELECT count(*) AS n FROM waitlist WHERE invited_at IS NULL")).fetchone())["n"]
    if not rows:
        return await _reply(app, msg.sender, "Waitlist khali hai.", msg.number_id)
    lines = []
    for r in rows:
        tg = r["phone"].startswith("tg:")
        who = r["phone"].removeprefix("tg:") + (" (Telegram)" if tg else f" {r['name']}" if r["name"] else "")
        lines.append(f"• {who}: {r['messages']} msg, \"{(r['first_text'] or '')[:50]}\"")
    more = f"\n(aur {total - len(rows)}; approve karne ke baad dobara /waitlist)" if total > len(rows) else ""
    await _reply(app, msg.sender, f"Waitlist (nayi pehle, {total}):\n" + "\n".join(lines) + more
                 + "\nApprove: neeche list se chuniye, ya /approve <number>", msg.number_id)
    choices = [{"id": APPROVE_BUTTON + r["phone"], "title": (r["name"] or f"+{r['phone']}")[:24],
                "description": f"+{r['phone']}: {r['first_text'] or ''}"[:72]}
               for r in rows if not r["phone"].startswith("tg:")][:10]           # WhatsApp lists hold at most 10
    if choices:
        await app.state.wa.send(
            {"messaging_product": "whatsapp", "to": msg.sender, "type": "interactive", "interactive": {
                "type": "list", "body": {"text": "Kise approve karna hai? Chunte hi unhe access mil jata hai."},
                "action": {"button": "Approve karein", "sections": [{"title": "Waitlist", "rows": choices}]}}},
            number_id=msg.number_id or None)


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


# ---------------------------------------------------------------- daily limits

LIMIT_HELP = ("Aise likhiye:\n/limit 9000000001 : istemaal dekhiye\n"
              f"/limit 9000000001 3 7 : 7 din ke liye 3 guna (2 se {limits.MAX_TIMES} guna, 1 se {limits.MAX_DAYS} din)\n"
              "/limit 9000000001 normal : wapas normal")


def _limit_args(rest: str) -> tuple[str, int | None, int] | None:
    """(account, times or None to only look, days) from '9000000001', '+91 90000 00001 3 7' or 'tg123 normal'; None if unclear."""
    tokens = rest.split()
    for k in (2, 1, 0):
        if k >= len(tokens):
            continue
        head, opts = tokens[:len(tokens) - k], [t.lower() for t in tokens[len(tokens) - k:]]
        if k and not re.fullmatch(r"\d{1,2}x?|normal", opts[0]):
            continue
        if k == 2 and not re.fullmatch(r"\d{1,2}d?", opts[1]):
            continue
        account = head[0].lower() if len(head) == 1 and re.fullmatch(r"tg\d+", head[0].lower()) else _number(" ".join(head))
        if account is None:
            continue
        times = None if not k else 1 if opts[0] == "normal" else int(opts[0].rstrip("x"))
        return account, times, int(opts[1].rstrip("d")) if k == 2 else 1
    return None


def _lakh(n: int) -> str:
    return f"{n / 100_000:.1f} lakh"


def _who(tenant) -> str:
    phone = tenant.owner_phone
    return f"{tenant.name} ({'+' + phone if phone.isdigit() else phone})"


def _usage_line(used: dict, allowed: dict) -> str:
    return (f"messages {used['msgs']}/{allowed['msgs']}, files {used['files']}/{allowed['files']}, "
            f"AI tokens {_lakh(used['tokens'])}/{_lakh(allowed['tokens'])}")


def _button_message(to: str, body: str, buttons: list[tuple[str, str]]) -> dict:
    return {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {
        "type": "button", "body": {"text": body[:1024]},
        "action": {"buttons": [{"type": "reply", "reply": {"id": i, "title": t[:20]}} for i, t in buttons[:3]]}}}


async def _limit(app, msg, rest: str) -> None:
    parsed = _limit_args(rest)
    if parsed is None:
        return await _reply(app, msg.sender, LIMIT_HELP, msg.number_id)
    account, times, days = parsed
    pool, s = app.state.pool, app.state.settings
    tenant = await tenants.by_owner_phone(pool, account)
    if tenant is None:
        return await _reply(app, msg.sender, f"{account} ka koi account nahi hai.", msg.number_id)
    if times is None:
        now_has = (f"{limits.times(tenant)}x, {admin_tg._till(tenant.limit_until)} tak" if limits.times(tenant) > 1
                   else "normal limit")
        if tenant.limits_exempt:
            now_has = "is account par koi limit nahi hai"
        body = (f"{_who(tenant)}\nPichhle 24 ghante: "
                f"{_usage_line(await limits.usage(pool, tenant.id), limits.allowance(s, tenant))}\nAbhi: {now_has}.")
        choices = [(f"{LIMIT_BUTTON}{account}:2:1", "2x, 24 ghante"), (f"{LIMIT_BUTTON}{account}:2:30", "2x, 30 din"),
                   (f"{LIMIT_BUTTON}{account}:1:0", "Normal limit")]
        return await app.state.wa.send(_button_message(msg.sender, body, choices), number_id=msg.number_id or None)
    if not 1 <= times <= limits.MAX_TIMES or (times > 1 and not 1 <= days <= limits.MAX_DAYS):
        return await _reply(app, msg.sender, LIMIT_HELP, msg.number_id)
    stopped = bool(tenant.limit_alerted_at) and datetime.now(timezone.utc) - tenant.limit_alerted_at < timedelta(hours=24)
    until = datetime.now(timezone.utc) + timedelta(days=days) if times > 1 else None
    async with pool.connection() as conn:       # after a change, the next time they are stopped is news again
        await conn.execute("UPDATE tenants SET limit_times = %s, limit_until = %s, limit_alerted_at = NULL WHERE id = %s",
                           (times, until, tenant.id))
    tenant = await tenants.by_id(pool, tenant.id)
    if times == 1:
        return await _reply(app, msg.sender, f"{_who(tenant)}: wapas normal limit.", msg.number_id)
    allowed, still = limits.allowance(s, tenant), await limits.reached(pool, s, tenant)
    told = ""
    if stopped and still is None:                # they were turned back in the last day: let them know they can carry on
        try:
            await send_to_owner(app, tenant, text_message(tenant.owner_phone, "Your Smara limit has been raised. You can carry on."),
                                kind="owner_note", reply_channel=tenant.owner_last_channel or "whatsapp")
            told = " Unhe bata diya hai."
        except Exception:
            log.warning("could not tell account %s that its limit was raised", tenant.id)
    warn = (f"\n⚠️ Ye allowance bhi poora ho chuka hai ({LIMIT_NAMES[still.what]} {still.used}/{still.limit}). Zyada guna dijiye."
            if still else "")
    await _reply(app, msg.sender, f"✅ {_who(tenant)}: {times}x limit {admin_tg._till(until)} tak. Har 24 ghante me "
                                  f"{allowed['msgs']} messages, {allowed['files']} files, {_lakh(allowed['tokens'])} AI tokens."
                                  f"{told}{warn}", msg.number_id)


async def tell_admins_about_limit(app, tenant, hit) -> bool:
    """Someone was stopped by a daily limit. True if at least one admin channel took the message."""
    used, cap = (_lakh(hit.used), _lakh(hit.limit)) if hit.what == "tokens" else (hit.used, hit.limit)
    about = (f"⚠️ {_who(tenant)} ki daily limit poori ho gayi: {LIMIT_NAMES[hit.what]} {used}/{cap}. "
             f"Lagbhag {limits.when_text(hit.free_at, hinglish=True)} IST tak ruke rahenge.")
    offer = min(max(limits.times(tenant) * 2, 2), limits.MAX_TIMES)
    told = await ops_alert(app, f"limit-{tenant.id}", f"{about}\nBadhane ke liye admin WhatsApp par: /limit {tenant.owner_phone}")
    for number in admin_numbers(app.state.settings):
        try:
            await app.state.wa.send(_button_message(number, f"{about}\nButton dabaiye, ya /limit {tenant.owner_phone}",
                                                    [(f"{LIMIT_BUTTON}{tenant.owner_phone}:{offer}:1", f"{offer}x, 24 ghante")]))
            told = True
        except Exception:
            log.info("WhatsApp admin %s not reachable now (outside the 24h window)", number[-4:])
    return told


async def tell_admins_about_waitlist(app, phone: str, name: str, text: str) -> None:
    """Someone wrote to Smara's WhatsApp number without an invite: every admin (Telegram, and WhatsApp numbers that
    wrote in the last 24 hours) gets one message with what is needed to invite them."""
    about = f"Naya WhatsApp waitlist request: {name or '?'}, +{phone}.\nUnhone likha: \"{text[:120]}\""
    note = f"{about}\nAccess dene ke liye: /invite {phone}"           # Telegram has no approve command yet
    if getattr(app.state, "tg", None) is not None:
        for admin in admin_tg.admin_ids(app.state.settings):
            try:
                await admin_tg._reply(app, admin, note)
            except Exception:
                log.warning("could not tell admin %s about the WhatsApp waitlist", admin)
    for number in admin_numbers(app.state.settings):
        try:
            await app.state.wa.send({"messaging_product": "whatsapp", "to": number, "type": "interactive", "interactive": {
                "type": "button", "body": {"text": f"{about}\nTap Approve, ya /approve {phone}"[:1024]},
                "action": {"buttons": [{"type": "reply", "reply": {"id": APPROVE_BUTTON + phone, "title": "Approve"}}]}}})
        except Exception:
            log.info("WhatsApp admin %s not reachable now (outside the 24h window)", number[-4:])
