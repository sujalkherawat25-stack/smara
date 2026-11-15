"""The public Smara number: invite codes, sign-up chat, waitlist, daily limits, legal pages."""
import httpx
import pytest
from fastapi import FastAPI
from test_agent_db import OWNER, FakeWA, ScriptLLM, env, one, owner, say  # noqa: F401

from src import tenants
from src.config import Settings
from src.ingest import legal, onboarding
from src.ingest.dispatch import dispatch
from src.ingest.payload import InboundMessage

NEW = "919811100777"


def text(wamid, body, sender=NEW):
    return InboundMessage(wamid=wamid, sender=sender, kind="text", text=body)


def last(env, to=NEW):
    return [m for m in env.app.state.wa.sent if m["to"] == to][-1]["text"]["body"]


async def make_code(env, **kw):
    code = onboarding.new_code("BSW")
    async with env.pool.connection() as c:
        await c.execute("insert into invite_codes (code, label, max_uses) values (%s, %s, %s)", (code, kw.get("label", "t"), kw.get("uses", 1)))
    return code


async def test_a_stranger_without_a_code_is_waitlisted_once_a_day_and_never_gets_a_shop(env):
    await dispatch(env.app, text("s1", "Hi, mujhe Smara use karna hai"))
    assert "invite" in last(env).lower()
    await dispatch(env.app, text("s2", "Kya hua?"))
    assert len([m for m in env.app.state.wa.sent if m["to"] == NEW]) == 1             # no second reply the same day
    row = await one(env.pool, "select messages, first_text from waitlist where phone = %s", NEW)
    assert row["messages"] == 2 and "Smara" in row["first_text"]
    assert await tenants.by_owner_phone(env.pool, NEW) is None


async def test_an_invite_code_creates_the_account_at_once_without_any_questions(env):
    code = await make_code(env, label="Ramesh Hardware")
    msg = InboundMessage(wamid="j1", sender=NEW, kind="text", text=f"join {code.lower()}", profile_name="Ramesh")
    await dispatch(env.app, msg)
    shop = await tenants.by_owner_phone(env.pool, NEW)
    assert shop and shop.name == "Ramesh" and shop.upi_vpa is None                  # the name WhatsApp already shows
    assert "I'm Smara" in last(env) and "tell me a little about yourself" in last(env)
    assert (await one(env.pool, "select uses from invite_codes where code = %s", code))["uses"] == 1
    assert (await one(env.pool, "select joined_with_code c from tenants where owner_phone = %s", NEW))["c"] == code
    # now they are an owner: their next message goes to the assistant
    env.app.state.llm = ScriptLLM(say("Abhi koi dues nahi hain."))
    await dispatch(env.app, text("j4", "kitna baaki hai?"))
    assert "dues" in last(env)


async def test_a_single_use_code_cannot_create_two_accounts_and_a_missing_profile_name_is_friend(env):
    code = await make_code(env)
    await dispatch(env.app, text("k1", f"JOIN {code}"))
    assert (await tenants.by_owner_phone(env.pool, NEW)).name == "Friend"
    other = "919822200888"
    await dispatch(env.app, text("o1", f"JOIN {code}", sender=other))                       # a second person tries the same code
    assert await tenants.by_owner_phone(env.pool, other) is None                              # gets the waitlist reply


async def test_open_sign_up_lets_anyone_in_without_questions(env):
    env.app.state.settings = Settings(signup_mode="open")
    await dispatch(env.app, text("p1", "hello"))
    assert "I'm Smara" in last(env)
    assert (await tenants.by_owner_phone(env.pool, NEW)).name == "Friend"


async def test_a_customer_of_a_shop_stays_a_customer_but_can_still_join_with_a_code(env):
    code = await make_code(env)
    async with env.pool.connection() as c:
        await c.execute("update parties set whatsapp_phone = %s where id = %s", (NEW, env.pid))
    await dispatch(env.app, text("c1", "ok ji, thank you"))
    assert not [m for m in env.app.state.wa.sent if m["to"] == NEW]                          # customer flow: no sign-up chat
    await dispatch(env.app, text("c2", f"JOIN {code}"))
    assert "I'm Smara" in last(env)


# ---------------------------------------------------------------- limits

async def test_a_new_shop_hits_its_daily_limit_politely_but_approvals_always_work(env):
    async with env.pool.connection() as c:
        await c.execute("update tenants set limits_exempt = false")
    env.app.state.settings = Settings(owner_daily_msgs=3)
    env.app.state.llm = ScriptLLM(*[say(f"jawab {i}") for i in range(5)])
    for i in range(3):
        await dispatch(env.app, owner(f"l{i}", f"sawaal {i}"))
    await dispatch(env.app, owner("l3", "chautha sawaal"))
    assert "limit poori" in env.app.state.wa.to(OWNER)[-1]["text"]["body"]
    assert len(env.app.state.llm.steps) == 2                                                    # the agent did not run for it
    await dispatch(env.app, owner("l4", button="ag_ok:00000000-0000-0000-0000-000000000000"))   # a button tap is never blocked
    assert "limit poori" not in env.app.state.wa.to(OWNER)[-1]["text"].get("body", "") if "text" in env.app.state.wa.to(OWNER)[-1] else True


async def test_shops_that_existed_before_limits_are_exempt(env):
    env.app.state.settings = Settings(owner_daily_msgs=1)
    env.app.state.llm = ScriptLLM(say("a"), say("b"), say("c"))
    async with env.pool.connection() as c:
        await c.execute("update tenants set limits_exempt = true")
    for i in range(3):
        await dispatch(env.app, owner(f"x{i}", f"sawaal {i}"))
    assert env.app.state.wa.to(OWNER)[-1]["text"]["body"] == "c"


# ---------------------------------------------------------------- data requests and the legal pages

async def test_delete_my_data_is_recorded_once_and_can_be_cancelled(env):
    env.app.state.llm.enabled = False
    await dispatch(env.app, owner("d1", "delete my data"))
    assert "7 din" in env.app.state.wa.to(OWNER)[-1]["text"]["body"]
    await dispatch(env.app, owner("d2", "delete my data"))
    assert (await one(env.pool, "select count(*) n from data_requests"))["n"] == 1
    await dispatch(env.app, owner("d3", "cancel delete"))
    assert (await one(env.pool, "select count(*) n from data_requests"))["n"] == 0


async def test_privacy_terms_and_deletion_pages_are_public():
    app = FastAPI()
    app.include_router(legal.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://smara.test") as http:
        for path, needle in (("/privacy", "never store bank passwords"), ("/terms", "never sends a message"),
                             ("/data-deletion", "delete my data")):
            r = await http.get(path)
            assert r.status_code == 200 and needle in r.text and "<title>" in r.text


async def test_home_page_describes_smara_and_links_the_policies():
    app = FastAPI()
    app.include_router(legal.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://smara.test") as http:
        r = await http.get("/")
    assert r.status_code == 200
    for needle in ("Smara", "WhatsApp", "Tally", "approve", "never moves money", "hello@syntarus.com", 'href="privacy"', 'href="terms"',
                   "not a debt collection agency", "agreed to hear from you"):
        assert needle in r.text, needle
    assert "Continuum" not in r.text and "munim" not in r.text.lower()
    assert "collections" not in r.text.lower() and "chase" not in r.text.lower()


async def test_page_links_follow_public_base_url_when_set():
    from types import SimpleNamespace
    app = FastAPI()
    app.include_router(legal.router)
    app.state.settings = SimpleNamespace(public_base_url="https://smara.example.com/app/")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://smara.test") as http:
        r = await http.get("/privacy")
    assert 'href="https://smara.example.com/app/terms"' in r.text and 'href="https://smara.example.com/app/"' in r.text
