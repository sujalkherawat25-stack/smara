"""Optional memory sharing: off by default, per shop, revocable, scrubbed, and never trusted as ledger fact."""
from test_agent_db import env, one, owner  # noqa: F401
from test_desktop_approvals_db import api, owner_token

from src.bridge import approvals, memory_share
from src.ingest.dispatch import dispatch


class Recorder:
    def __init__(self):
        self.deleted, self.asked = [], []

    async def resolve(self, **kw):
        self.asked.append(kw["user_id"])
        return {"resolution": {"state_card": "Shop closes on Sundays. key=sk_mem_abcdefghijklmnop"}}

    async def delete_user(self, user_id):
        self.deleted.append(user_id)


def test_scrub_removes_keys_codes_and_card_numbers_but_keeps_plain_facts():
    text = ("Ravi pays on Friday.\napi_key = sk_live_abcdefghijkl\nCard 4111 1111 1111 1111. UPI PIN 482913. "
            "OTP is 551234. Invoice 1234567890123 stays. Bearer abcdefghijklmnop12345")
    clean = memory_share.scrub(text)
    for leaked in ("sk_live_abcdefghijkl", "4111", "482913", "551234", "abcdefghijklmnop12345"):
        assert leaked not in clean
    assert "Ravi pays on Friday" in clean and "1234567890123" in clean       # a non-card number is kept


async def test_off_by_default_then_notes_are_stored_untrusted_in_their_own_scope(env):
    env.app.state.continuum = Recorder()
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        h = {"Authorization": f"Bearer {token}"}
        assert (await http.get("/bridge/memory", headers=h)).json() == {"enabled": False}
        assert (await http.post("/bridge/memory/notes", json={"text": "hello"}, headers=h)).status_code == 403
        assert (await http.post("/bridge/memory/search", json={"query": "opening hours"}, headers=h)).status_code == 403
        assert (await http.post("/bridge/memory/enable", json={"enabled": True}, headers=h)).json() == {"enabled": True}
        r = await http.post("/bridge/memory/notes", json={"text": "Owner prefers Hindi. password: hunter2hunter2"}, headers=h)
        assert r.status_code == 200 and r.json()["stored"] is True
        row = await one(env.pool, "select user_id, text, trusted from continuum_outbox where source_id like 'desk_%%'")
        assert row["user_id"] == f"{env.t.id}_desktop" and row["trusted"] is False
        assert "hunter2" not in row["text"] and "Owner prefers Hindi" in row["text"]
        found = (await http.post("/bridge/memory/search", json={"query": "opening hours"}, headers=h)).json()
        assert "Sundays" in found["state_card"] and "sk_mem_abcdefghijklmnop" not in found["state_card"]
        assert env.app.state.continuum.asked[-1] == f"{env.t.id}_shop"


async def test_turning_off_stops_both_directions_and_forget_erases_only_the_desktop_scope(env):
    env.app.state.continuum = Recorder()
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        h = {"Authorization": f"Bearer {token}"}
        await dispatch(env.app, owner("ms1", "share desktop memory"))
        assert "ON" in env.app.state.wa.sent[-1]["text"]["body"]
        assert (await http.post("/bridge/memory/notes", json={"text": "a note"}, headers=h)).status_code == 200
        await dispatch(env.app, owner("ms2", "stop sharing desktop memory"))
        assert "OFF" in env.app.state.wa.sent[-1]["text"]["body"]
        assert (await http.post("/bridge/memory/notes", json={"text": "b note"}, headers=h)).status_code == 403
        assert (await http.post("/bridge/memory/search", json={"query": "anything here"}, headers=h)).status_code == 403
        await dispatch(env.app, owner("ms3", "forget desktop memory"))
        assert env.app.state.continuum.deleted == [f"{env.t.id}_desktop"]
        assert (await one(env.pool, "select count(*) n from continuum_outbox where user_id like '%%_desktop'"))["n"] == 0


async def test_sharing_is_per_shop_and_needs_the_owner_token(env):
    env.app.state.continuum = Recorder()
    async with api(env) as http:
        token, _ = await owner_token(env, http)
        await http.post("/bridge/memory/enable", json={"enabled": True}, headers={"Authorization": f"Bearer {token}"})
        code = await approvals.start_pairing(env.pool, str(env.other.id))
        other = (await http.post("/bridge/approvals/pair", json={"code": code, "name": "x"})).json()["owner_token"]
        h = {"Authorization": f"Bearer {other}"}
        assert (await http.get("/bridge/memory", headers=h)).json() == {"enabled": False}      # the other shop did not opt in
        assert (await http.post("/bridge/memory/notes", json={"text": "x note"}, headers=h)).status_code == 403
        assert (await http.post("/bridge/memory/notes", json={"text": "x note"})).status_code == 401


async def test_the_hosted_agent_reads_desktop_notes_only_while_sharing_is_on(env):
    from src.agent.tools import resolve_continuum_memory
    env.app.state.continuum = Recorder()
    from types import SimpleNamespace
    ctx = SimpleNamespace(tenant_id=str(env.t.id), id="s", call_id="c", trust="TRUSTED")
    off = await resolve_continuum_memory(env.app, ctx, {"party_id": "shop", "query": "hours"})
    assert "from_desktop_unverified" not in off
    await memory_share.set_enabled(env.pool, str(env.t.id), True)
    on = await resolve_continuum_memory(env.app, ctx, {"party_id": "shop", "query": "hours"})
    assert "from_desktop_unverified" in on and f"{env.t.id}_desktop" in env.app.state.continuum.asked
