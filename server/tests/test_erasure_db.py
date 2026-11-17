"""'DELETE MY ACCOUNT' erases everything, on the exact phrase only; 'DELETE ALL FILES' empties the file space."""
from datetime import datetime, timezone

from test_phase_b_db import make_app, one, pool, seed  # noqa: F401

from src import erasure, tenants
from src.ingest import owner_files
from src.ingest import telegram as tgmod

CHAT = 7777
_n = iter(range(1, 10_000))


class Continuum:
    def __init__(self, down=False):
        self.deleted, self.down = [], down

    async def delete_user(self, user_id):
        if self.down:
            raise ConnectionError("continuum unreachable")
        self.deleted.append(user_id)


def msg(text):
    return {"message": {"message_id": next(_n), "chat": {"id": CHAT, "type": "private"}, "from": {"id": 1, "first_name": "R"}, "text": text}}


async def shop(pool, tmp_path, down=False):
    app = make_app(pool, owner_files_dir=str(tmp_path))
    app.state.continuum = Continuum(down)
    t, _ = await seed(pool)
    async with pool.connection() as c:
        await c.execute("UPDATE tenants SET telegram_chat_id = %s, owner_last_channel = 'telegram', limits_exempt = true WHERE id = %s",
                        (CHAT, t.id))
        await c.execute("INSERT INTO chat_messages (tenant_id, channel, role, content) VALUES (%s, 'telegram', 'owner', 'hello')", (t.id,))
        await c.execute("INSERT INTO shop_facts (tenant_id, slot, topic, text) VALUES (%s, 'shop:rule', 'rule', 'always Hindi')", (t.id,))
        await c.execute("INSERT INTO continuum_outbox (user_id, source_id, text, trusted) VALUES (%s, 's1', 'x', true)",
                        (f"{t.id}_shop",))
    owner_files.save(str(tmp_path), t.id, "bills.xlsx", b"x" * 1000)
    return app, t


async def tenant_rows(pool, tid):
    out = {}
    async with pool.connection() as c:
        cols = await (await c.execute("SELECT DISTINCT table_name FROM information_schema.columns WHERE column_name = 'tenant_id'")).fetchall()
        for r in cols:
            n = (await (await c.execute(f"SELECT count(*) AS n FROM {r['table_name']} WHERE tenant_id = %s", (tid,))).fetchone())["n"]
            if n:
                out[r["table_name"]] = n
    return out


async def test_every_table_with_a_tenant_id_is_covered_by_the_erasure(pool):
    async with pool.connection() as c:
        rows = await (await c.execute("SELECT DISTINCT table_name FROM information_schema.columns WHERE column_name = 'tenant_id' "
                                      "AND table_schema = 'public'")).fetchall()
    missing = {r["table_name"] for r in rows} - set(erasure.TENANT_TABLES)
    assert not missing, f"add these tables to erasure.TENANT_TABLES: {missing}"


async def test_the_exact_phrase_erases_the_shop_its_memory_and_its_files(pool, tmp_path):
    app, t = await shop(pool, tmp_path)
    assert await tenant_rows(pool, t.id)
    await tgmod.handle_update(app, msg("DELETE MY ACCOUNT"))
    assert await tenants.by_id(pool, t.id) is None
    assert not await tenant_rows(pool, t.id)
    assert (await one(pool, "select count(*) n from continuum_outbox"))["n"] == 0
    assert (await one(pool, "select count(*) n from obligations"))["n"] == 0
    assert any(u.endswith("_shop") for u in app.state.continuum.deleted) and len(app.state.continuum.deleted) >= 2   # shop + Ravi
    assert not (tmp_path / str(t.id).lower()).exists()
    log = await one(pool, "select * from erasure_log")
    assert log["tenant_hash"] != t.id and log["files_removed"] == 1 and log["continuum_users"] >= 2
    assert "hata diya" in [m for m in app.state.tg.sent if m["chat_id"] == CHAT][-1]["text"]


async def test_anything_softer_than_the_exact_phrase_deletes_nothing(pool, tmp_path):
    app, t = await shop(pool, tmp_path)
    for text in ("delete my account", "Delete My Account", "DELETE MY ACCOUNT please", "delete my data", "mera data delete karo",
                 "DELETE  MY  ACCOUNT", "DELETE MY ACCOUNT."):
        await tgmod.handle_update(app, msg(text))
    assert await tenants.by_id(pool, t.id) is not None
    assert app.state.continuum.deleted == []
    sent = [m["text"] for m in app.state.tg.sent if m["chat_id"] == CHAT]
    assert sum("DELETE MY ACCOUNT" in s for s in sent) >= 4          # the plain-words explanation, with the exact phrase


async def test_nothing_is_deleted_when_the_memory_service_is_down(pool, tmp_path):
    app, t = await shop(pool, tmp_path, down=True)
    await tgmod.handle_update(app, msg("DELETE MY ACCOUNT"))
    assert await tenants.by_id(pool, t.id) is not None and await tenant_rows(pool, t.id)
    assert (tmp_path / str(t.id).lower()).exists()
    assert "kuch bhi delete nahi hua" in [m for m in app.state.tg.sent if m["chat_id"] == CHAT][-1]["text"]


async def test_another_shop_is_untouched(pool, tmp_path):
    app, t = await shop(pool, tmp_path)
    other = await tenants.create(pool, name="Other", owner_phone="919800000001", upi_vpa="o@upi")
    async with pool.connection() as c:
        await c.execute("INSERT INTO shop_facts (tenant_id, slot, topic, text) VALUES (%s, 'shop:k', 'k', 'keep me')", (other.id,))
    await tgmod.handle_update(app, msg("DELETE MY ACCOUNT"))
    assert await tenants.by_id(pool, other.id) is not None and (await tenant_rows(pool, other.id)).get("shop_facts") == 1


async def test_delete_all_files_needs_its_exact_phrase_and_can_be_restored(pool, tmp_path):
    app, t = await shop(pool, tmp_path)
    await tgmod.handle_update(app, msg("delete all files"))
    assert "DELETE ALL FILES" in app.state.tg.sent[-1]["text"]
    assert owner_files.used_bytes(str(tmp_path), t.id) == 1000                       # only asked so far
    await tgmod.handle_update(app, msg("DELETE ALL FILES"))
    assert owner_files.used_bytes(str(tmp_path), t.id) == 0 and "hata di gayi" in app.state.tg.sent[-1]["text"]
    assert owner_files.restore(str(tmp_path), t.id, 1, datetime.now(timezone.utc))["name"] == "bills.xlsx"
