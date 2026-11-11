"""A new owner joins from Telegram, with no WhatsApp step.

Until now a shop could only be created from the WhatsApp number, and Telegram could only be linked from WhatsApp. So with
WhatsApp unavailable nobody new could start. Here a Telegram chat that is not linked to a shop can sign up:
  - "JOIN <code>" (or an invite link, t.me/<bot>?start=<code>) with a valid invite starts the chat; SIGNUP_MODE=open
    starts it for anyone; otherwise the person is put on the waitlist;
  - the owner shares their phone number with Telegram's own contact button. Telegram fills in whose contact it is, and
    only the sender's own contact is accepted, so nobody can claim another person's number;
  - then shop name and optional UPI ID, the same questions as on WhatsApp.
The phone number makes the shop the same one on every channel: when that number later writes to Smara on WhatsApp it is
this shop's owner. A number that already owns a shop is never linked from here (that would hand a shop to whoever
typed its number); that owner links Telegram from WhatsApp, or the Smara team does it for them.
"""
import logging
import re

from src import tenants
from src.config import normalize_phone
from src.ingest.onboarding import ASK_UPI, SKIP, UPI, WELCOME_ASK_SHOP, _code_from, _valid_invite, welcome_done
from src.loop.wa_format import to_telegram_html
from src.ops_alert import ops_alert

log = logging.getLogger(__name__)
SHARE_BUTTON = "Share my phone number"
ASK_PHONE = ("Namaste! Main Smara hoon, aapke business ka digital saathi.\n\nShuru karne ke liye neeche "
             f"'{SHARE_BUTTON}' button dabaiye. Isi number se aapki shop pehchani jayegi, WhatsApp par bhi.")
PHONE_AGAIN = (f"Kripya neeche '{SHARE_BUTTON}' button dabaiye (number type mat kijiye). Rokna ho to 'cancel' likhiye.")
NOT_INVITED = ("Namaste! Main Smara hoon. Abhi Smara sirf invite wale business owners ke liye khula hai.\n\n"
               "Invite code hai to yahin 'JOIN CODE' bhejiye. Pehle se WhatsApp par Smara use karte hain to wahan "
               "'telegram' likhiye. Nahi to apna naam aur business ka naam bhej dijiye, hum aapko jaldi invite karenge.")
ALREADY_A_SHOP = ("Is number par pehle se ek Smara shop hai. Usse is Telegram se jodne ke liye WhatsApp par Smara ko "
                  "'telegram' bhejiye, ya hume likhiye: hello@syntarus.com")
CANCELLED = "Theek hai, sign-up rok diya. Dobara shuru karne ke liye 'JOIN CODE' bhejiye."
BAD_INVITE = "Ye invite code ab kaam nahi kar raha. Naya code mangwa lijiye."
ACTIVE = ("ASK_PHONE", "ASK_SHOP", "ASK_UPI")
_SHARE_KEYS = {"keyboard": [[{"text": SHARE_BUTTON, "request_contact": True}]], "resize_keyboard": True,
               "one_time_keyboard": True}
_NO_KEYS = {"remove_keyboard": True}


def session_key(chat_id: int) -> str:
    return f"tg:{chat_id}"


async def _say(app, chat_id: int, text: str, keys: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": to_telegram_html(text), "parse_mode": "HTML"}
    if keys is not None:
        payload["reply_markup"] = keys
    await app.state.tg.send(payload)


async def handle(app, m: dict, chat_id: int, text: str) -> None:
    """A private Telegram chat that is not linked to any shop wrote to the bot."""
    pool, key = app.state.pool, session_key(chat_id)
    async with pool.connection() as conn:
        sess = await (await conn.execute("SELECT * FROM onboarding_sessions WHERE phone = %s", (key,))).fetchone()
    if sess and sess["state"] in ACTIVE:
        return await _continue(app, m, chat_id, sess, text)

    code = _code_from(text)
    invite = await _valid_invite(pool, code) if code else None
    if invite or getattr(app.state.settings, "signup_mode", "invite") == "open":
        async with pool.connection() as conn:
            await conn.execute(
                """INSERT INTO onboarding_sessions (phone, state, invite_code) VALUES (%s, 'ASK_PHONE', %s)
                   ON CONFLICT (phone) DO UPDATE SET state = 'ASK_PHONE', invite_code = EXCLUDED.invite_code,
                     shop_name = NULL, owner_phone = NULL, updated_at = now()""", (key, invite["code"] if invite else None))
        return await _say(app, chat_id, ASK_PHONE, _SHARE_KEYS)

    async with pool.connection() as conn:           # kept for whoever decides who to invite
        await conn.execute(
            """INSERT INTO waitlist (phone, first_text, last_text) VALUES (%s, %s, %s)
               ON CONFLICT (phone) DO UPDATE SET last_text = EXCLUDED.last_text, messages = waitlist.messages + 1""",
            (key, text[:300], text[:300]))
    await _say(app, chat_id, NOT_INVITED)


async def _continue(app, m: dict, chat_id: int, sess: dict, text: str) -> None:
    pool, key = app.state.pool, session_key(chat_id)
    if text.strip().lower() in ("cancel", "/cancel"):
        async with pool.connection() as conn:
            await conn.execute("UPDATE onboarding_sessions SET state = 'DONE', updated_at = now() WHERE phone = %s", (key,))
        return await _say(app, chat_id, CANCELLED, _NO_KEYS)

    if sess["state"] == "ASK_PHONE":
        contact = m.get("contact") or {}
        sender_id = (m.get("from") or {}).get("id")
        phone = normalize_phone(contact.get("phone_number", ""))
        if not sender_id or contact.get("user_id") != sender_id or len(phone) < 10:
            return await _say(app, chat_id, PHONE_AGAIN, _SHARE_KEYS)       # typed, or someone else's contact
        if len(phone) == 10:
            phone = "91" + phone
        if await tenants.by_owner_phone(pool, phone):
            async with pool.connection() as conn:
                await conn.execute("UPDATE onboarding_sessions SET state = 'DONE', updated_at = now() WHERE phone = %s", (key,))
            await ops_alert(app, f"tg-signup-existing-{phone[-4:]}",
                            f"Smara: an owner whose number ends {phone[-4:]} tried to join from Telegram, but that number "
                            "already has a shop. They may need help linking Telegram.")
            return await _say(app, chat_id, ALREADY_A_SHOP, _NO_KEYS)
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE onboarding_sessions SET owner_phone = %s, state = 'ASK_SHOP', updated_at = now() WHERE phone = %s",
                (phone, key))
        return await _say(app, chat_id, WELCOME_ASK_SHOP.partition("\n\n")[2] or WELCOME_ASK_SHOP, _NO_KEYS)

    if sess["state"] == "ASK_SHOP":
        name = re.sub(r"\s+", " ", text).strip()
        if len(name) < 2 or len(name) > 60 or name.startswith("/"):
            return await _say(app, chat_id, "Business ka naam bhejiye (2 se 60 akshar), jaise 'Ramesh Hardware'.")
        async with pool.connection() as conn:
            await conn.execute("UPDATE onboarding_sessions SET shop_name = %s, state = 'ASK_UPI', updated_at = now() WHERE phone = %s",
                               (name, key))
        return await _say(app, chat_id, ASK_UPI)

    upi = None
    if text.strip().lower() not in SKIP:
        if not UPI.match(text.strip()):
            return await _say(app, chat_id, "UPI ID aise hota hai: naam@upi. Dobara bhejiye, ya 'skip' likhiye.")
        upi = text.strip().lower()
    await _create_shop(app, chat_id, sess, upi)


async def _create_shop(app, chat_id: int, sess: dict, upi: str | None) -> None:
    pool, key, code, phone, name = app.state.pool, session_key(chat_id), sess["invite_code"], sess["owner_phone"], sess["shop_name"]
    if not phone or not name or await tenants.by_owner_phone(pool, phone):     # the number became a shop meanwhile
        async with pool.connection() as conn:
            await conn.execute("UPDATE onboarding_sessions SET state = 'DONE', updated_at = now() WHERE phone = %s", (key,))
        return await _say(app, chat_id, ALREADY_A_SHOP)
    async with pool.connection() as conn:
        async with conn.transaction():
            if code:                                   # take the invite use atomically, as on WhatsApp
                ok = await (await conn.execute(
                    """UPDATE invite_codes SET uses = uses + 1 WHERE code = %s AND revoked_at IS NULL AND uses < max_uses
                          AND (expires_at IS NULL OR expires_at > now()) RETURNING code""", (code,))).fetchone()
                if ok is None:
                    await conn.execute("UPDATE onboarding_sessions SET state = 'DONE', updated_at = now() WHERE phone = %s", (key,))
                    return await _say(app, chat_id, BAD_INVITE)
            await conn.execute("UPDATE onboarding_sessions SET state = 'DONE', updated_at = now() WHERE phone = %s", (key,))
            await conn.execute("UPDATE waitlist SET invited_at = now() WHERE phone IN (%s, %s)", (key, phone))
    t = await tenants.create(pool, name=name, owner_phone=phone, upi_vpa=upi, upi_name=name if upi else None)
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE tenants SET joined_with_code = %s, telegram_chat_id = %s, owner_last_channel = 'telegram' WHERE id = %s",
            (code, chat_id, t.id))
    await _say(app, chat_id, welcome_done(name))
    log.info("new shop %s joined on Telegram via %s", t.id, code or "open sign-up")
    await ops_alert(app, f"new-shop-{t.id}", f"Smara: a new shop joined on Telegram: {name}.")
