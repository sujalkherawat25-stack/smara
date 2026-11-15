"""The public Smara number: how a stranger becomes a shop, and how an invite-only launch stays under control.

A message from a number that is neither a shop owner nor a known customer used to be dropped. Now:
  - "JOIN <code>" with a valid invite creates the account at once (nothing is asked: the name WhatsApp shows is used);
  - with SIGNUP_MODE=open any first message does the same;
  - otherwise the person is put on the waitlist with one polite reply (never more than one a day, so nobody can run up
    traffic or loop with us).
Everything is plain WhatsApp text, in Hinglish.
"""
import logging
import re
import secrets

from src import tenants
from src.config import normalize_phone
from src.loop.whatsapp import text_message

log = logging.getLogger(__name__)
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
JOIN = re.compile(r"^\s*(?:join|code|invite|start)?\s*[:\-]?\s*([A-Za-z]{2,5}-?[A-Za-z0-9]{4,6})\s*$", re.I)
JOIN_WORD = re.compile(r"^\s*(?:join|code|invite)\b[\s:\-]*([A-Za-z0-9\-]{4,14})\s*$", re.I)

INVITE_ONLY = ("Hi! I'm Smara, an AI assistant for anyone who works, studies or runs something: writing, research, files, "
               "plans, reminders and more.\n\nAccess is by invite for now. You're on the waitlist, and I'll message you here "
               "as soon as you're in.\n\nIf you have an invite code, send 'JOIN CODE'.")
WAITLISTED = "You're on the waitlist. I'll message you here as soon as you have access."


def new_code(prefix: str = "SMA") -> str:
    return f"{prefix.upper()[:4]}-" + "".join(secrets.choice(ALPHABET) for _ in range(4))


def _code_from(text: str) -> str | None:
    m = JOIN_WORD.match(text or "") or JOIN.match(text or "")
    return m.group(1).upper().replace(" ", "") if m else None


async def _send(app, to: str, text: str, number_id: str | None = None) -> None:
    await app.state.wa.send(text_message(to, text), number_id=number_id or None)


async def _valid_invite(pool, code: str) -> dict | None:
    async with pool.connection() as conn:
        return await (await conn.execute(
            """SELECT code, label FROM invite_codes WHERE code = %s AND revoked_at IS NULL AND uses < max_uses
                  AND (expires_at IS NULL OR expires_at > now())""", (code,))).fetchone()


async def handle_stranger(app, msg) -> None:
    """A message from a number we do not know as an owner or a customer."""
    pool, s = app.state.pool, app.state.settings
    text = (msg.text or msg.caption or "").strip()
    async with pool.connection() as conn:
        sess = await (await conn.execute("SELECT * FROM onboarding_sessions WHERE phone = %s", (msg.sender,))).fetchone()

    if sess and sess["state"] in ("ASK_SHOP", "ASK_UPI"):     # someone who was in the old question chat: let them straight in
        return await create_shop(app, msg.sender, msg.profile_name, sess["invite_code"], msg.number_id)

    code = _code_from(text)
    invite = await _valid_invite(pool, code) if code else None
    if invite:
        return await create_shop(app, msg.sender, msg.profile_name, invite["code"], msg.number_id)
    if getattr(s, "signup_mode", "invite") == "open":
        return await create_shop(app, msg.sender, msg.profile_name, None, msg.number_id)

    # invite-only: one reply a day, and everything they write is kept for whoever decides who to invite
    async with pool.connection() as conn:
        row = await (await conn.execute(
            """INSERT INTO waitlist (phone, first_text, last_text) VALUES (%s, %s, %s)
               ON CONFLICT (phone) DO UPDATE SET last_text = EXCLUDED.last_text, messages = waitlist.messages + 1
               RETURNING messages, (replied_at IS NULL OR replied_at < now() - interval '1 day') AS due""",
            (msg.sender, text[:300], text[:300]))).fetchone()
        if row["due"]:
            await conn.execute("UPDATE waitlist SET replied_at = now() WHERE phone = %s", (msg.sender,))
    if row["due"]:
        first = row["messages"] <= 1 or not text or code is not None
        await _send(app, msg.sender, INVITE_ONLY if first else WAITLISTED, msg.number_id)
    if code and not invite:
        log.info("waitlisted %s: invalid or used code", msg.sender[-4:])


def display_name(raw: str | None) -> str:
    """The name Telegram or WhatsApp already shows for the person (nothing is asked), cleaned up; 'Friend' if there is none."""
    name = re.sub(r"\s+", " ", re.sub(r"[<>&\x00-\x1f]", "", raw or "")).strip()[:40]
    return name or "Friend"


def welcome_done(name: str | None = None) -> str:
    """The first message to someone new. It asks for nothing: it says what Smara can do, how it stays safe, and opens
    with one easy question so the person tells Smara about themselves in their own words."""
    first = (name or "").split(" ")[0] if name and name != "Friend" else ""
    hi = f"Hi {first}! " if first else "Hi! "
    return (
        f"{hi}I'm Smara, an AI assistant. You can write to me like you would to a sharp colleague, in English, Hindi or "
        "Hinglish, by text or voice note.\n\n"
        "Here is some of what I can do:\n"
        "• Write, fix and translate: emails, messages, reports, CVs, posts\n"
        "• Read what you send: PDFs, photos, spreadsheets, voice notes\n"
        "• Make files for you: Excel sheets, PDFs, Word documents, charts\n"
        "• Research and plan: look things up on the web, compare options, make study or trip plans\n"
        "• Remember things and remind you when it matters\n"
        "• Keep a business running: customer dues, payment reminders, Tally or Excel lists ('export' shows how, 'sample' gives a sample file)\n"
        "• Learn your routines: when something works well, I can save it as a skill and do it faster next time\n\n"
        "I only act with your OK: nothing goes to anyone, and nothing changes in your records, until you approve it. "
        "I never move money and I never need your passwords.\n\n"
        "To start, tell me a little about yourself: what do you do, and what takes up most of your time these days? "
        "I'll suggest where I can help.")


async def create_shop(app, phone: str, name: str | None, code: str | None, number_id: str | None = None) -> None:
    pool = app.state.pool
    async with pool.connection() as conn:
        async with conn.transaction():
            if code:                                   # take the invite use atomically: two people cannot share one single-use code
                ok = await (await conn.execute(
                    """UPDATE invite_codes SET uses = uses + 1 WHERE code = %s AND revoked_at IS NULL AND uses < max_uses
                          AND (expires_at IS NULL OR expires_at > now()) RETURNING code""", (code,))).fetchone()
                if ok is None:
                    await conn.execute("UPDATE onboarding_sessions SET state = 'DONE' WHERE phone = %s", (phone,))
                    return await _send(app, phone, "Ye invite code ab kaam nahi kar raha. Naya code mangwa lijiye.", number_id)
            await conn.execute("""INSERT INTO onboarding_sessions (phone, state, invite_code) VALUES (%s, 'DONE', %s)
                                  ON CONFLICT (phone) DO UPDATE SET state = 'DONE', updated_at = now()""", (phone, code))
            await conn.execute("UPDATE waitlist SET invited_at = now() WHERE phone = %s", (phone,))
    name = display_name(name)
    t = await tenants.create(pool, name=name, owner_phone=normalize_phone(phone))
    async with pool.connection() as conn:
        await conn.execute("UPDATE tenants SET joined_with_code = %s WHERE id = %s", (code, t.id))
    await _send(app, phone, welcome_done(name), number_id)
    log.info("new account %s joined via %s", t.id, code or "open sign-up")
