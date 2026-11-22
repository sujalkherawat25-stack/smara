"""Admin commands from an admin WhatsApp number; strangers land on the waitlist and the admins are told."""
from test_agent_db import env, one  # noqa: F401

from src.config import Settings, admin_wa_number
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage

ADMIN, STRANGER = "917877787675", "919811100888"
_n = iter(range(1, 10_000))


def say(sender, body):
    return InboundMessage(wamid=f"adm{next(_n)}", sender=sender, kind="text", text=body)


def last(env, to):
    return [m for m in env.app.state.wa.sent if m["to"] == to][-1]["text"]["body"]


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


async def test_the_same_command_from_anyone_else_is_just_a_message_and_a_waitlist_entry(env):
    as_admin(env)
    await dispatch(env.app, say(STRANGER, "/invite"))
    assert "waitlist" in last(env, STRANGER)
    assert (await one(env.pool, "select count(*) n from invite_codes"))["n"] == 0


async def test_a_new_waitlist_request_is_reported_to_the_admin_and_an_invite_goes_to_the_stranger(env):
    as_admin(env)
    await dispatch(env.app, say(STRANGER, "hello"))
    assert f"/invite {STRANGER}" in last(env, ADMIN)
    await dispatch(env.app, say(STRANGER, "hello again"))
    assert len([m for m in env.app.state.wa.sent if m["to"] == ADMIN]) == 1          # told once
    await dispatch(env.app, say(ADMIN, "/waitlist"))
    assert STRANGER in last(env, ADMIN)
    await dispatch(env.app, say(ADMIN, f"/invite {STRANGER} Meera"))
    sent = last(env, STRANGER)
    assert "You now have access" in sent and "JOIN SMA-" in sent
    assert (await one(env.pool, "select invited_at is not null i from waitlist where phone = %s", STRANGER))["i"] is True
    code = sent.split("JOIN ")[1].strip()
    await dispatch(env.app, say(STRANGER, f"JOIN {code}"))
    assert "I'm Smara" in last(env, STRANGER)
