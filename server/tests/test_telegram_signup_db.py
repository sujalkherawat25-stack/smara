"""A new person joins from Telegram with an invite link: the account is created at once and nothing is asked."""
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
    return {"message": {"message_id": next(_n), "chat": {"id": chat, "type": "private"}, "from": {"id": USER, "first_name": "Ramesh"}, "text": text, **extra}}


def contact(phone=PHONE, user_id=USER, chat=CHAT):
    return msg(chat=chat, contact={"phone_number": f"+{phone}", "first_name": "Ramesh", "user_id": user_id})


def last(app, chat=CHAT):
    return [m for m in app.state.tg.sent if m["chat_id"] == chat][-1]


async def make_code(pool, uses=1):
    code = onboarding.new_code("BSW")
    async with pool.connection() as c:
        await c.execute("insert into invite_codes (code, label, max_uses) values (%s, 't', %s)", (code, uses))
    return code


async def test_an_invite_link_creates_the_account_at_once_and_asks_for_nothing(pool):
    app = make_app(pool, ops_telegram_chat_id=99)
    code = await make_code(pool)
    await tgmod.handle_update(app, msg(f"/start {code}"))                 # the invite link: t.me/<bot>?start=<code>
    welcome = last(app)
    assert "I'm Smara" in welcome["text"] and "reply_markup" not in welcome          # no phone button, no question chat
    assert "UPI ID" not in welcome["text"] and "phone number" not in welcome["text"].lower()
    assert "tell me a little about yourself" in welcome["text"]
    shop = await tenants.by_owner_phone(pool, tgo.owner_key(CHAT))
    assert shop and shop.name == "Ramesh" and shop.upi_vpa is None                  # the name Telegram already shows
    assert shop.telegram_chat_id == CHAT and shop.owner_last_channel == "telegram" and not shop.limits_exempt
    assert (await one(pool, "select uses from invite_codes where code = %s", code))["uses"] == 1
    assert "Ramesh" in last(app, chat=99)["text"]                                    # the Smara team is told
    # the next message goes straight to the assistant, not to any sign-up step
    await tgmod.handle_update(app, msg("help"))
    assert "Just talk to me" in last(app)["text"]


async def test_join_in_plain_text_works_and_a_spent_or_unknown_code_does_not(pool):
    app = make_app(pool)
    code = await make_code(pool)
    await tgmod.handle_update(app, msg(f"JOIN {code}"))
    assert await tenants.by_owner_phone(pool, tgo.owner_key(CHAT))
    other = 5151
    await tgmod.handle_update(app, msg(f"JOIN {code}", chat=other))                  # the single use is gone: told plainly, waitlisted, no account
    assert "expired or was already used" in last(app, chat=other)["text"].lower()
    assert await one(pool, "select 1 as x from waitlist where phone = %s", tgo.session_key(other))
    assert await tenants.by_owner_phone(pool, tgo.owner_key(other)) is None
    assert (await one(pool, "select uses from invite_codes where code = %s", code))["uses"] == 1


async def test_someone_who_was_in_the_old_question_chat_is_let_straight_in(pool):
    app = make_app(pool)
    code = await make_code(pool)
    async with pool.connection() as c:
        await c.execute("insert into onboarding_sessions (phone, state, invite_code) values (%s, 'ASK_SHOP', %s)",
                        (tgo.session_key(CHAT), code))
    await tgmod.handle_update(app, msg("Ramesh Hardware"))
    shop = await tenants.by_owner_phone(pool, tgo.owner_key(CHAT))
    assert shop and "I'm Smara" in last(app)["text"]
    assert (await one(pool, "select uses from invite_codes where code = %s", code))["uses"] == 1


async def test_without_an_invite_a_telegram_stranger_is_waitlisted_and_gets_no_shop(pool):
    app = make_app(pool)
    await tgmod.handle_update(app, msg("Hi, mujhe Smara chahiye"))
    await tgmod.handle_update(app, msg("/start"))
    await tgmod.handle_update(app, msg("JOIN BSW-ZZZZ"))                  # not a real code
    assert all("invite" in m["text"].lower() for m in app.state.tg.sent)
    row = await one(pool, "select messages, first_text from waitlist where phone = %s", tgo.session_key(CHAT))
    assert row["messages"] == 3 and "Smara" in row["first_text"]
    assert (await one(pool, "select count(*) as n from tenants"))["n"] == 0


async def test_open_sign_up_needs_no_code_and_a_revoked_invite_is_refused(pool):
    app = make_app(pool, signup_mode="open")
    await tgmod.handle_update(app, msg("hello"))
    assert (await tenants.by_owner_phone(pool, tgo.owner_key(CHAT))).name == "Ramesh"
    assert "I'm Smara" in last(app)["text"]

    app2 = make_app(pool, signup_mode="invite")
    code = await make_code(pool)
    other_chat = 5252
    async with pool.connection() as c:
        await c.execute("update invite_codes set revoked_at = now() where code = %s", (code,))
    await tgmod.handle_update(app2, msg(f"JOIN {code}", chat=other_chat))
    assert await tenants.by_owner_phone(pool, tgo.owner_key(other_chat)) is None


async def test_someone_who_left_with_telegram_off_and_returns_gets_the_same_account(pool):
    app = make_app(pool)
    code = await make_code(pool, uses=2)
    await tgmod.handle_update(app, msg(f"JOIN {code}"))
    first = await tenants.by_owner_phone(pool, tgo.owner_key(CHAT))
    async with pool.connection() as c:
        await c.execute("update tenants set telegram_chat_id = NULL where id = %s", (first.id,))
    await tgmod.handle_update(app, msg(f"JOIN {code}"))
    assert "Welcome back" in last(app)["text"]
    assert (await one(pool, "select count(*) as n from tenants"))["n"] == 1
    assert (await one(pool, "select uses from invite_codes where code = %s", code))["uses"] == 1


async def test_telegram_messages_count_towards_the_daily_limit(pool):
    app = make_app(pool, owner_daily_msgs=2)
    t = await tenants.create(pool, name="Busy Shop", owner_phone=PHONE)
    async with pool.connection() as c:
        await c.execute("update tenants set telegram_chat_id = %s where id = %s", (CHAT, t.id))
    for _ in range(3):
        await tgmod.handle_update(app, msg("what do I owe"))
    assert (await one(pool, "select count(*) as n from owner_inbound_log where tenant_id = %s", t.id))["n"] == 2    # the one turned back is not counted
    assert "limit" in last(app)["text"].lower()
