"""The people who run Smara invite others from Telegram; strangers land on the waitlist and the admins are told."""
import httpx
from test_phase_b_db import make_app, one, pool  # noqa: F401

from src.ingest import admin_tg
from src.ingest import telegram as tgmod

ADMIN, STRANGER, OTHER = 5447631816, 6765688446, 777000111
_n = iter(range(1, 10_000))


def msg(text, chat, first_name="Asha", username=None):
    frm = {"id": chat, "first_name": first_name, **({"username": username} if username else {})}
    return {"message": {"message_id": next(_n), "chat": {"id": chat, "type": "private"}, "from": frm, "text": text}}


def texts(app, chat):
    return [m["text"] for m in app.state.tg.sent if m["chat_id"] == chat]


async def test_the_admin_makes_an_invite_link_and_lists_and_revokes_codes(pool):
    app = make_app(pool, ops_telegram_chat_id=ADMIN)                     # the ops chat is the admin when no list is set
    await tgmod.handle_update(app, msg("/invite Ramesh Hardware", ADMIN))
    reply = texts(app, ADMIN)[-1]
    assert "https://t.me/smara_test_bot?start=SMA-" in reply
    code = reply.split("start=")[1].split()[0].split("\n")[0]
    row = await one(pool, "select label, max_uses from invite_codes where code = %s", code)
    assert row["label"] == "Ramesh Hardware" and row["max_uses"] == 1
    await tgmod.handle_update(app, msg("/invites", ADMIN))
    assert code in texts(app, ADMIN)[-1]
    await tgmod.handle_update(app, msg(f"/revoke {code}", ADMIN))
    assert "band kar diya" in texts(app, ADMIN)[-1]
    assert (await one(pool, "select revoked_at is not null as r from invite_codes where code = %s", code))["r"] is True


async def test_an_admin_invite_with_a_telegram_id_sends_the_link_to_that_person(pool):
    app = make_app(pool, admin_telegram_ids=(ADMIN,))
    await tgmod.handle_update(app, msg("hello", STRANGER))               # a stranger writes first: waitlist
    assert "waitlist" in texts(app, STRANGER)[-1]
    assert f"/invite {STRANGER}" in texts(app, ADMIN)[-1] and "Telegram id" in texts(app, ADMIN)[-1]   # the admin is told
    await tgmod.handle_update(app, msg(f"/invite {STRANGER}", ADMIN))
    sent = texts(app, STRANGER)[-1]
    assert "https://t.me/smara_test_bot?start=SMA-" in sent and "access mil gaya" in sent
    assert (await one(pool, "select invited_at is not null as i from waitlist where phone = %s", f"tg:{STRANGER}"))["i"] is True
    assert "bhej diya" in texts(app, ADMIN)[-1]


async def test_a_stranger_hears_they_are_on_the_waitlist_once_in_full_and_then_briefly(pool):
    app = make_app(pool, admin_telegram_ids=(ADMIN,))
    await tgmod.handle_update(app, msg("hi", OTHER, first_name="Meera", username="meera_k"))
    first = texts(app, OTHER)[-1]
    assert "waitlist mein hain" in first and "AI assistant" in first
    note = texts(app, ADMIN)[-1]
    assert "Meera" in note and "@meera_k" in note and str(OTHER) in note
    await tgmod.handle_update(app, msg("hello again", OTHER))
    assert texts(app, OTHER)[-1].startswith("Aap abhi waitlist mein hain") and len(texts(app, ADMIN)) == 1   # no second alert
    await tgmod.handle_update(app, msg("/waitlist", ADMIN))
    assert str(OTHER) in texts(app, ADMIN)[-1] and "2 msg" in texts(app, ADMIN)[-1]


async def test_admin_commands_do_nothing_for_anyone_else(pool):
    app = make_app(pool, admin_telegram_ids=(ADMIN,))
    await tgmod.handle_update(app, msg("/invite", STRANGER))             # not an admin: it is just a stranger's message
    assert "https://t.me" not in " ".join(texts(app, STRANGER))
    assert (await one(pool, "select count(*) as n from invite_codes"))["n"] == 0
    assert (await one(pool, "select count(*) as n from waitlist"))["n"] == 1


async def test_when_telegram_refuses_the_message_the_admin_gets_the_link_to_forward(pool):
    app = make_app(pool, admin_telegram_ids=(ADMIN,))
    real_send = app.state.tg.send

    async def refusing(payload):
        if payload["chat_id"] == OTHER:
            raise httpx.HTTPStatusError("403", request=httpx.Request("POST", "http://x"), response=httpx.Response(403))
        await real_send(payload)
    app.state.tg.send = refusing
    await tgmod.handle_update(app, msg(f"/invite {OTHER}", ADMIN))
    reply = texts(app, ADMIN)[-1]
    assert "message nahi jaane diya" in reply and "https://t.me/smara_test_bot?start=SMA-" in reply


def test_admin_ids_come_from_the_list_or_else_the_ops_chat():
    from src.config import Settings
    assert admin_tg.admin_ids(Settings(admin_telegram_ids=(1, 2), ops_telegram_chat_id=3)) == {1, 2}
    assert admin_tg.admin_ids(Settings(ops_telegram_chat_id=3)) == {3}
    assert admin_tg.admin_ids(Settings()) == set()
