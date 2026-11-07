"""The public Smara number: how a stranger becomes a shop, and how an invite-only launch stays under control.

A message from a number that is neither a shop owner nor a known customer used to be dropped. Now:
  - "JOIN <code>" with a valid invite starts a short sign-up chat (shop name, optional UPI ID) and creates the shop;
  - with SIGNUP_MODE=open any first message starts that chat;
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
UPI = re.compile(r"^[a-z0-9.\-_]{2,40}@[a-z][a-z0-9]{1,20}$", re.I)
SKIP = {"skip", "nahi", "no", "none", "baad mein", "later", "-"}

WELCOME_ASK_SHOP = ("Namaste! Main Smara hoon, aapke business ka digital saathi.\n\nShuru karne ke liye bataiye: "
                    "aapki dukaan ya business ka naam kya hai?")
ASK_UPI = ("Dhanyavaad! Ab apna UPI ID bhejiye (jaise ramesh@upi) taaki customers ko payment link ke saath bhej sakun. "
           "Abhi nahi dena ho to 'skip' likhiye.")
INVITE_ONLY = ("Namaste! Main Smara hoon. Abhi Smara sirf invite wale business owners ke liye khula hai.\n\n"
               "Agar aapke paas invite code hai to 'JOIN CODE' bhejiye. Nahi hai to apna naam aur business ka naam bhej dijiye, "
               "hum aapko jaldi invite karenge.")
WAITLISTED = "Dhanyavaad, aapka naam list mein jod diya hai. Invite milte hi hum yahin message karenge."


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

    if sess and sess["state"] in ("ASK_SHOP", "ASK_UPI"):
        return await _continue(app, msg, sess, text)

    code = _code_from(text)
    invite = await _valid_invite(pool, code) if code else None
    if invite:
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO onboarding_sessions (phone, state, invite_code) VALUES (%s, 'ASK_SHOP', %s)
                   ON CONFLICT (phone) DO UPDATE SET state = 'ASK_SHOP', invite_code = EXCLUDED.invite_code, updated_at = now()""",
                (msg.sender, invite["code"]))
        return await _send(app, msg.sender, WELCOME_ASK_SHOP, msg.number_id)

    if getattr(s, "signup_mode", "invite") == "open":
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO onboarding_sessions (phone, state) VALUES (%s, 'ASK_SHOP')
                   ON CONFLICT (phone) DO UPDATE SET state = 'ASK_SHOP', updated_at = now()""", (msg.sender,))
        return await _send(app, msg.sender, WELCOME_ASK_SHOP, msg.number_id)

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


async def _continue(app, msg, sess: dict, text: str) -> None:
    pool = app.state.pool
    if sess["state"] == "ASK_SHOP":
        name = re.sub(r"\s+", " ", text).strip()
        if len(name) < 2 or len(name) > 60 or _code_from(name) and name.upper().replace(" ", "") == _code_from(name):
            return await _send(app, msg.sender, "Business ka naam bhejiye (2 se 60 akshar), jaise 'Ramesh Hardware'.", msg.number_id)
        async with pool.connection() as conn:
            await conn.execute("UPDATE onboarding_sessions SET shop_name = %s, state = 'ASK_UPI', updated_at = now() WHERE phone = %s",
                               (name, msg.sender))
        return await _send(app, msg.sender, ASK_UPI, msg.number_id)

    upi = None
    t = text.strip().lower()
    if t not in SKIP:
        if not UPI.match(text.strip()):
            return await _send(app, msg.sender, "UPI ID aise hota hai: naam@upi. Dobara bhejiye, ya 'skip' likhiye.", msg.number_id)
        upi = text.strip().lower()
    await create_shop(app, msg.sender, sess["shop_name"], upi, sess["invite_code"], msg.number_id)


async def create_shop(app, phone: str, name: str, upi: str | None, code: str | None, number_id: str | None = None) -> None:
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
            await conn.execute("UPDATE onboarding_sessions SET state = 'DONE', updated_at = now() WHERE phone = %s", (phone,))
            await conn.execute("UPDATE waitlist SET invited_at = now() WHERE phone = %s", (phone,))
    t = await tenants.create(pool, name=name, owner_phone=normalize_phone(phone), upi_vpa=upi, upi_name=name if upi else None)
    async with pool.connection() as conn:
        await conn.execute("UPDATE tenants SET joined_with_code = %s WHERE id = %s", (code, t.id))
    await _send(app, phone, (
        f"Swagat hai, {name}! Aapka Smara ready hai.\n\n"
        "Ab aap ye kar sakte hain:\n"
        "1) Apni outstanding list (Excel/CSV ya Tally file) yahin bhej dijiye. Main customers aur baaki rakam padh lunga.\n"
        "2) Poochiye: 'Ravi ka kitna baaki hai?' ya voice note bhejiye.\n"
        "3) 'Ravi ka statement PDF bhejo'.\n\n"
        "Customers ko koi bhi message aapke tap ke bina nahi jata. Madad ke liye kabhi bhi 'help' likhiye."), number_id)
    log.info("new shop %s joined via %s", t.id, code or "open sign-up")
