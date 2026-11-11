"""A new owner signs up from Telegram: invite, own phone number from Telegram's contact button, shop name, UPI ID."""
from test_phase_b_db import make_app, one, pool  # noqa: F401

from src import tenants
from src.config import Settings
from src.ingest import onboarding
from src.ingest import telegram as tgmod
from src.ingest import telegram_onboarding as tgo
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage

CHAT, USER, PHONE = 4242, 9001, "919811100777"
_n = iter(range(1, 10_000))


def msg(text="", chat=CHAT, **extra):
    return {"message": {"message_id": next(_n), "chat": {"id": chat, "type": "private"}, "from": {"id": USER}, "text": text, **extra}}


def contact(phone=PHONE, user_id=USER, chat=CHAT):
    return msg(chat=chat, contact={"phone_number": f"+{phone}", "first_name": "Ramesh", "user_id": user_id})


def last(app, chat=CHAT):
    return [m for m in app.state.tg.sent if m["chat_id"] == chat][-1]


async def make_code(pool, uses=1):
    code = onboarding.new_code("BSW")
    async with pool.connection() as c:
        await c.execute("insert into invite_codes (code, label, max_uses) values (%s, 't', %s)", (code, uses))
    return code


async def test_an_invite_link_signs_a_new_owner_up_on_telegram_and_whatsapp_then_finds_the_same_shop(pool):
    app = make_app(pool, ops_telegram_chat_id=99)
    code = await make_code(pool)
    await tgmod.handle_update(app, msg(f"/start {code}"))                 # the invite link: t.me/<bot>?start=<code>
    ask = last(app)
    assert ask["reply_markup"]["keyboard"][0][0]["request_contact"] is True
    await tgmod.handle_update(app, contact())
    assert "naam kya hai" in last(app)["text"] and last(app)["reply_markup"] == {"remove_keyboard": True}
    await tgmod.handle_update(app, msg("Ramesh Hardware"))
    assert "UPI" in last(app)["text"]
    await tgmod.handle_update(app, msg("ramesh@upi"))
    shop = await tenants.by_owner_phone(pool, PHONE)
    assert shop and shop.name == "Ramesh Hardware" and shop.upi_vpa == "ramesh@upi"
    assert shop.telegram_chat_id == CHAT and shop.owner_last_channel == "telegram" and not shop.limits_exempt
    assert "Swagat hai" in last(app)["text"]
    assert (await one(pool, "select uses from invite_codes where code = %s", code))["uses"] == 1
    assert "Ramesh Hardware" in last(app, chat=99)["text"]                # the Smara team is told
    # the same number on WhatsApp is this shop's owner, not a stranger and not a second shop
    await dispatch(app, InboundMessage(wamid="w-same", sender=PHONE, kind="text", text="telegram off"))
    assert (await one(pool, "select count(*) as n from tenants where owner_phone = %s", PHONE))["n"] == 1
    assert (await tenants.by_owner_phone(pool, PHONE)).telegram_chat_id is None


async def test_join_in_plain_text_works_and_a_typed_or_borrowed_number_is_refused(pool):
    app = make_app(pool)
    code = await make_code(pool)
    await tgmod.handle_update(app, msg(f"JOIN {code}"))
    await tgmod.handle_update(app, msg("9811100777"))                     # typed: not proof of anything
    assert "button" in last(app)["text"] and "request_contact" in str(last(app)["reply_markup"])
    await tgmod.handle_update(app, contact(user_id=USER + 1))             # someone else's contact card
    assert "button" in last(app)["text"]
    assert (await one(pool, "select state, owner_phone from onboarding_sessions where phone = %s", tgo.session_key(CHAT))) == \
        {"state": "ASK_PHONE", "owner_phone": None}
    await tgmod.handle_update(app, msg("cancel"))
    assert (await one(pool, "select state from onboarding_sessions where phone = %s", tgo.session_key(CHAT)))["state"] == "DONE"
    assert (await one(pool, "select uses from invite_codes where code = %s", code))["uses"] == 0


async def test_without_an_invite_a_telegram_stranger_is_waitlisted_and_gets_no_shop(pool):
    app = make_app(pool)
    await tgmod.handle_update(app, msg("Hi, mujhe Smara chahiye"))
    await tgmod.handle_update(app, msg("/start"))
    await tgmod.handle_update(app, msg("JOIN BSW-ZZZZ"))                  # not a real code
    assert all("invite" in m["text"].lower() for m in app.state.tg.sent)
    row = await one(pool, "select messages, first_text from waitlist where phone = %s", tgo.session_key(CHAT))
    assert row["messages"] == 3 and "Smara" in row["first_text"]
    assert (await one(pool, "select count(*) as n from tenants"))["n"] == 0


async def test_a_number_that_already_owns_a_shop_cannot_be_claimed_from_telegram(pool):
    app = make_app(pool, ops_telegram_chat_id=99)
    await tenants.create(pool, name="Existing Shop", owner_phone=PHONE)
    code = await make_code(pool)
    await tgmod.handle_update(app, msg(f"JOIN {code}"))
    await tgmod.handle_update(app, contact())
    assert "pehle se" in last(app)["text"]
    shop = await tenants.by_owner_phone(pool, PHONE)
    assert shop.name == "Existing Shop" and shop.telegram_chat_id is None
    assert (await one(pool, "select uses from invite_codes where code = %s", code))["uses"] == 0
    assert "already has a shop" in last(app, chat=99)["text"]


async def test_open_sign_up_needs_no_code_and_a_spent_invite_stops_at_the_end(pool):
    app = make_app(pool, signup_mode="open")
    await tgmod.handle_update(app, msg("hello"))
    assert "request_contact" in str(last(app)["reply_markup"])
    await tgmod.handle_update(app, contact())
    await tgmod.handle_update(app, msg("Open Shop"))
    await tgmod.handle_update(app, msg("skip"))
    assert (await tenants.by_owner_phone(pool, PHONE)).name == "Open Shop"

    app2 = make_app(pool)
    code = await make_code(pool)
    other_chat, other_phone = 5151, "919811100888"
    await tgmod.handle_update(app2, msg(f"JOIN {code}", chat=other_chat))
    await tgmod.handle_update(app2, contact(phone=other_phone, chat=other_chat))
    await tgmod.handle_update(app2, msg("Late Shop", chat=other_chat))
    async with pool.connection() as c:
        await c.execute("update invite_codes set revoked_at = now() where code = %s", (code,))
    await tgmod.handle_update(app2, msg("skip", chat=other_chat))
    assert "kaam nahi" in last(app2, chat=other_chat)["text"]
    assert await tenants.by_owner_phone(pool, other_phone) is None


async def test_telegram_messages_count_towards_the_daily_limit(pool):
    app = make_app(pool, owner_daily_msgs=2)
    t = await tenants.create(pool, name="Busy Shop", owner_phone=PHONE)
    async with pool.connection() as c:
        await c.execute("update tenants set telegram_chat_id = %s where id = %s", (CHAT, t.id))
    for _ in range(3):
        await tgmod.handle_update(app, msg("help"))
    assert (await one(pool, "select count(*) as n from owner_inbound_log where tenant_id = %s", t.id))["n"] == 3
    assert "limit" in last(app)["text"].lower()
