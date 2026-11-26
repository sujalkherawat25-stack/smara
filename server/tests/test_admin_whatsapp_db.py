"""Admin commands from an admin WhatsApp number; strangers land on the waitlist and the admins are told."""
from test_agent_db import env, one  # noqa: F401

from src import tenants
from src.config import Settings, admin_wa_number
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage, parse_messages

ADMIN, STRANGER = "917877787675", "919811100888"
_n = iter(range(1, 10_000))


def say(sender, body):
    return InboundMessage(wamid=f"adm{next(_n)}", sender=sender, kind="text", text=body)


def last(env, to):
    return [m for m in env.app.state.wa.sent if m["to"] == to][-1]["text"]["body"]


def texts(env, to):
    return [m["text"]["body"] for m in env.app.state.wa.sent if m["to"] == to and m["type"] == "text"]


def buttons(env, to):
    return [[b["reply"]["id"] for b in m["interactive"]["action"]["buttons"]] for m in env.app.state.wa.sent
            if m["to"] == to and m.get("interactive", {}).get("type") == "button"]


def tap(sender, button_id):
    return InboundMessage(wamid=f"adm{next(_n)}", sender=sender, kind="interactive", button_id=button_id)


async def accounts(env, phone):
    return (await one(env.pool, "select count(*) n from tenants where owner_phone = %s", phone))["n"]


def as_admin(env):
    env.app.state.settings = Settings(**{**env.app.state.settings.__dict__, "admin_whatsapp_numbers": (ADMIN,),
                                         "smara_wa_number": "916375168132"})


def test_a_bare_ten_digit_number_gets_the_country_code():
    assert admin_wa_number("7877787675") == "917877787675"
    assert admin_wa_number("+91 78777 87675") == "917877787675"
    assert admin_wa_number("") == ""


async def test_an_admin_number_makes_lists_and_revokes_invites(env):
    as_admin(env)
    await dispatch(env.app, say(ADMIN, "/invite Ramesh"))
    reply = last(env, ADMIN)
    code = reply.split("Code: ")[1].split("\n")[0]
    assert code.startswith("SMA-") and f"https://wa.me/916375168132?text=JOIN%20{code}" in reply
    assert (await one(env.pool, "select label, max_uses from invite_codes where code = %s", code))["max_uses"] == 1
    await dispatch(env.app, say(ADMIN, "/invites"))
    assert code in last(env, ADMIN)
    await dispatch(env.app, say(ADMIN, f"/revoke {code}"))
    assert "band kar diya" in last(env, ADMIN)


async def test_any_other_message_from_an_admin_number_shows_the_commands_and_never_the_waitlist(env):
    as_admin(env)
    await dispatch(env.app, say(ADMIN, "Hi"))
    assert "Smara admin" in last(env, ADMIN) and "/invite" in last(env, ADMIN)
    await dispatch(env.app, say(ADMIN, "Hii"))                                      # every time, not once a day
    assert len([m for m in env.app.state.wa.sent if m["to"] == ADMIN]) == 2
    assert (await one(env.pool, "select count(*) n from waitlist where phone = %s", ADMIN))["n"] == 0


async def test_an_admin_who_also_has_an_account_chats_with_smara_as_usual(env):
    as_admin(env)
    await tenants.create(env.pool, name="Admin Shop", owner_phone=ADMIN)
    await dispatch(env.app, say(ADMIN, "Hi"))
    sent = [m["text"]["body"] for m in env.app.state.wa.sent if m["to"] == ADMIN]
    assert not any("Smara admin" in t for t in sent)


async def test_the_same_command_from_anyone_else_is_just_a_message_and_a_waitlist_entry(env):
    as_admin(env)
    await dispatch(env.app, say(STRANGER, "/invite"))
    assert "waitlist" in last(env, STRANGER)
    assert (await one(env.pool, "select count(*) n from invite_codes"))["n"] == 0


async def test_a_new_waitlist_request_is_reported_to_the_admin_and_an_invite_goes_to_the_stranger(env):
    as_admin(env)
    await dispatch(env.app, say(STRANGER, "hello"))
    assert buttons(env, ADMIN)[-1] == [f"adm:approve:{STRANGER}"]
    await dispatch(env.app, say(STRANGER, "hello again"))
    assert len([m for m in env.app.state.wa.sent if m["to"] == ADMIN]) == 1          # told once
    await dispatch(env.app, say(ADMIN, "/waitlist"))
    assert STRANGER in texts(env, ADMIN)[-1]
    await dispatch(env.app, say(ADMIN, f"/invite {STRANGER} Meera"))
    sent = last(env, STRANGER)
    assert "You now have access" in sent and "JOIN SMA-" in sent
    assert (await one(env.pool, "select invited_at is not null i from waitlist where phone = %s", STRANGER))["i"] is True
    code = sent.split("JOIN ")[1].strip()
    await dispatch(env.app, say(STRANGER, f"JOIN {code}"))
    assert "I'm Smara" in last(env, STRANGER)


async def wrote_without_an_invite(env, phone=STRANGER, name="Meera K"):
    await dispatch(env.app, InboundMessage(wamid=f"adm{next(_n)}", sender=phone, kind="text", text="hello", profile_name=name))


async def went_quiet(env, phone=STRANGER):
    async with env.pool.connection() as conn:
        await conn.execute("update waitlist set last_seen_at = now() - interval '3 days' where phone = %s", (phone,))


async def test_one_tap_makes_the_account_and_tells_the_person_the_wait_is_over(env):
    as_admin(env)
    await wrote_without_an_invite(env)
    await dispatch(env.app, tap(ADMIN, buttons(env, ADMIN)[-1][0]))
    assert await accounts(env, STRANGER) == 1
    assert (await one(env.pool, "select name from tenants where owner_phone = %s", STRANGER))["name"] == "Meera K"
    told = texts(env, STRANGER)[-1]
    assert "Hi Meera! Your wait is over" in told and "No code needed" in told and "JOIN" not in told
    assert "Meera K" in texts(env, ADMIN)[-1] and "bhej diya" in texts(env, ADMIN)[-1] and "wa.me" not in texts(env, ADMIN)[-1]
    before = len(texts(env, STRANGER))
    await dispatch(env.app, tap(ADMIN, f"adm:approve:{STRANGER}"))                       # the old button, tapped again
    assert "account pehle se hai" in texts(env, ADMIN)[-1] and len(texts(env, STRANGER)) == before


async def test_after_a_day_of_silence_the_message_waits_for_their_next_one(env):
    as_admin(env)
    await wrote_without_an_invite(env)
    await went_quiet(env)
    before = len(texts(env, STRANGER))
    await dispatch(env.app, say(ADMIN, "/approve +91 98111 00888"))
    assert await accounts(env, STRANGER) == 1 and len(texts(env, STRANGER)) == before     # WhatsApp would drop it
    assert "https://wa.me/916375168132?text=Hi" in texts(env, ADMIN)[-1]
    await dispatch(env.app, say(STRANGER, "Hi"))
    assert sum("Your wait is over" in t for t in texts(env, STRANGER)) == 1
    await dispatch(env.app, say(STRANGER, "hello again"))
    assert sum("Your wait is over" in t for t in texts(env, STRANGER)) == 1              # once
    assert not any("waitlist" in t.lower() for t in texts(env, STRANGER)[before:])


async def test_an_approved_template_is_used_when_one_is_set_up(env):
    as_admin(env)
    env.app.state.settings = Settings(**{**env.app.state.settings.__dict__, "wa_approved_template": "smara_wait_over"})
    await wrote_without_an_invite(env)
    await went_quiet(env)
    await dispatch(env.app, say(ADMIN, f"/approve {STRANGER}"))
    sent = [m for m in env.app.state.wa.sent if m["to"] == STRANGER][-1]
    assert sent["type"] == "template" and sent["template"]["name"] == "smara_wait_over"
    assert sent["template"]["components"][0]["parameters"] == [{"type": "text", "text": "Meera K"}]


async def test_only_someone_on_the_waitlist_can_be_approved(env):
    as_admin(env)
    await dispatch(env.app, say(ADMIN, f"/approve {STRANGER}"))
    assert "waitlist me nahi hai" in texts(env, ADMIN)[-1] and await accounts(env, STRANGER) == 0
    assert not [m for m in env.app.state.wa.sent if m["to"] == STRANGER]
    for bad in ("/approve", "/approve Meera", "/approve 12345", "/approve 9811100888 and everyone"):
        await dispatch(env.app, say(ADMIN, bad))
        assert "Aise likhiye" in texts(env, ADMIN)[-1], bad


async def test_nobody_but_an_admin_can_approve_and_without_approval_a_code_is_still_needed(env):
    as_admin(env)
    await wrote_without_an_invite(env)
    for attempt in (say(STRANGER, f"/approve {STRANGER}"), tap(STRANGER, f"adm:approve:{STRANGER}")):
        await dispatch(env.app, attempt)
    await wrote_without_an_invite(env)
    assert await accounts(env, STRANGER) == 0


async def test_the_waitlist_comes_with_a_list_to_pick_from(env):
    as_admin(env)
    for i in range(12):
        await wrote_without_an_invite(env, f"9198111009{i:02d}", f"Person number {i} with a very long profile name")
    await dispatch(env.app, say(ADMIN, "/waitlist"))
    picker = env.app.state.wa.sent[-1]["interactive"]
    rows = picker["action"]["sections"][0]["rows"]
    assert picker["type"] == "list" and len(rows) == 10
    assert all(len(r["title"]) <= 24 and len(r["description"]) <= 72 and r["id"].startswith("adm:approve:91") for r in rows)
    assert "12" in texts(env, ADMIN)[-1]
    await dispatch(env.app, tap(ADMIN, rows[0]["id"]))
    await dispatch(env.app, say(ADMIN, "/waitlist"))
    assert rows[0]["id"] not in str(env.app.state.wa.sent[-1])


def test_a_pick_from_a_whatsapp_list_reaches_the_app():
    body = {"entry": [{"changes": [{"value": {"messages": [{"id": "w1", "from": ADMIN, "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": f"adm:approve:{STRANGER}", "title": "Meera"}}}]}}]}]}
    assert parse_messages(body)[0].button_id == f"adm:approve:{STRANGER}"
