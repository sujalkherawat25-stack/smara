"""Sign-up edge cases that a real first owner can hit: odd ways of typing the code, two people on one code at the same moment,
and our own error halfway through (the invite must not be lost)."""
import asyncio

import pytest
from test_phase_b_db import make_app, one, pool  # noqa: F401
from test_telegram_signup_db import CHAT, last, make_code, msg

from src import tenants
from src.ingest import onboarding
from src.ingest import telegram as tgmod
from src.ingest import telegram_onboarding as tgo


async def accounts(pool):
    return (await one(pool, "select count(*) n from tenants"))["n"]


@pytest.mark.parametrize("typed", ["/start {c}", "JOIN {c}", "join {c}", "  join   {c}  ", "Code: {c}", "{c}", "invite {c}"])
async def test_the_new_six_character_code_works_however_it_is_typed(pool, typed):
    app = make_app(pool)
    code = await make_code(pool)
    assert len(code.split("-")[1]) == 6
    await tgmod.handle_update(app, msg(typed.format(c=code)))
    assert await tenants.by_owner_phone(pool, tgo.owner_key(CHAT)), typed
    assert "I'm Smara" in last(app)["text"]


async def test_the_code_in_lower_case_or_without_the_dash_also_works(pool):
    app = make_app(pool)
    code = await make_code(pool)
    await tgmod.handle_update(app, msg("join " + code.lower().replace("-", " "), chat=777))
    assert await tenants.by_owner_phone(pool, tgo.owner_key(777))


async def test_two_people_on_one_single_use_code_at_the_same_moment_make_one_account(pool):
    app = make_app(pool)
    code = await make_code(pool)
    before = await accounts(pool)
    await asyncio.gather(*(tgmod.handle_update(app, msg(f"/start {code}", chat=c)) for c in (8001, 8002, 8003, 8004)))
    assert await accounts(pool) == before + 1
    assert (await one(pool, "select uses from invite_codes where code = %s", code))["uses"] == 1


async def test_our_own_error_while_creating_the_account_gives_the_invite_back(pool, monkeypatch):
    app = make_app(pool)
    code = await make_code(pool)
    real = tenants.create

    async def boom(*a, **k):
        raise RuntimeError("database hiccup")
    monkeypatch.setattr(tenants, "create", boom)
    await tgmod.handle_update(app, msg(f"/start {code}"))
    assert "something went wrong" in last(app)["text"].lower() and "nothing was used up" in last(app)["text"].lower()
    assert (await one(pool, "select uses from invite_codes where code = %s", code))["uses"] == 0
    assert await tenants.by_owner_phone(pool, tgo.owner_key(CHAT)) is None
    monkeypatch.setattr(tenants, "create", real)                           # a minute later, the same link works
    await tgmod.handle_update(app, msg(f"/start {code}"))
    assert await tenants.by_owner_phone(pool, tgo.owner_key(CHAT))
    assert (await one(pool, "select uses from invite_codes where code = %s", code))["uses"] == 1


async def test_a_wrong_code_does_not_burn_a_real_one_and_the_right_code_still_works(pool):
    app = make_app(pool)
    code = await make_code(pool)
    await tgmod.handle_update(app, msg("/start SMA-ZZZZZZ", chat=9100))
    assert await tenants.by_owner_phone(pool, tgo.owner_key(9100)) is None
    await tgmod.handle_update(app, msg(f"/start {code}", chat=9100))
    assert await tenants.by_owner_phone(pool, tgo.owner_key(9100))


async def test_someone_already_in_a_shop_who_opens_another_invite_is_told_so_and_the_invite_is_kept(pool):
    app = make_app(pool)
    a, b = await make_code(pool), await make_code(pool)
    await tgmod.handle_update(app, msg(f"/start {a}", chat=9200))
    await tgmod.handle_update(app, msg(f"/start {b}", chat=9200))
    assert "linked to" in last(app, chat=9200)["text"].lower()
    assert (await one(pool, "select uses from invite_codes where code = %s", b))["uses"] == 0
