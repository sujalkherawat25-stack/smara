import json
import os
import subprocess
import sys
import uuid
from types import SimpleNamespace

import pytest

from src.box.gstin import check_gstin_status, checksum_char
from src.box.guard import check_url, host_blocked, is_mutating, validate_plan
from src.box.tools import parse_cards

SAMPLE = "27AAPFU0939F1ZV"  # widely published example GSTIN


def test_gstin():
    r = check_gstin_status(SAMPLE.lower() + " ")
    assert r["valid"] and r["state_code"] == "27" and r["pan"] == "AAPFU0939F"
    typo = SAMPLE[:-1] + ("A" if SAMPLE[-1] != "A" else "B")
    assert "checksum" in check_gstin_status(typo)["reason"]
    assert not check_gstin_status("123")["valid"]
    assert not check_gstin_status("99AAPFU0939F1ZV"[:2] + "XXXXX")["valid"]
    assert checksum_char(SAMPLE[:14]) == SAMPLE[-1]


def test_host_blocking():
    for h in ("localhost", "127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "db.internal"):
        assert host_blocked(h), h
    assert not host_blocked("example.com") and not host_blocked("8.8.8.8")


async def test_check_url_rejects_non_http_and_private():
    for u in ("file:///etc/passwd", "http://127.0.0.1:8080/x", "ftp://example.com"):
        with pytest.raises(ValueError):
            await check_url(u)


def test_plan_validation():
    ok = [{"action": "goto", "url": "https://example.com"}, {"action": "extract_text", "selector": "h1"}]
    assert validate_plan(ok) == ok and not is_mutating(ok)
    assert is_mutating(ok + [{"action": "click", "selector": "a"}])
    for bad in ([], [{"action": "extract_text", "selector": "h1"}], [{"action": "eval", "value": "x"}],
                [{"action": "goto", "url": "javascript:alert(1)"}], [{"action": "goto", "url": "https://a.com"}] * 16):
        with pytest.raises(ValueError):
            validate_plan(bad)


def test_parse_cards():
    cards = parse_cards([
        "Steel Pipe 2 inch\n₹ 450 / Meter\nPune\nContact Supplier",
        "Steel Pipe 2 inch\n₹ 460 / Meter\nContact Supplier",   # duplicate title
        "Verified GI Pipe dealer\nNoida\nContact Supplier",
    ], limit=5)
    assert [c["title"] for c in cards] == ["Steel Pipe 2 inch", "Verified GI Pipe dealer"]
    assert cards[0]["price"] == "₹ 450 / Meter" and cards[0]["location"] == "Pune"
    assert cards[1]["price"] is None


def test_worker_runs_real_chromium_on_local_html():
    """End-to-end through the subprocess + extraction JS, using a data: page (no network)."""
    from src.box.client import _run
    html = ("<div><div><b>LED Bulb 9W</b><p>₹ 80 / Piece</p><p>Delhi</p><button>Contact Supplier</button></div>"
            "<div><b>LED Panel</b><p>Mumbai</p><button>Contact Supplier</button></div></div>")
    from src.box import tools
    code = (
        "import asyncio,json,sys;from src.box import tools\n"
        "async def m():\n"
        "  async with tools.browser_page() as p:\n"
        f"    await p.set_content({html!r})\n"
        "    return tools.parse_cards(await p.evaluate(tools.CARD_JS), 5)\n"
        "print(json.dumps(asyncio.run(m())))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         cwd=os.path.dirname(os.path.dirname(__file__)))
    cards = json.loads(out.stdout.strip().splitlines()[-1])
    assert [c["title"] for c in cards] == ["LED Bulb 9W", "LED Panel"] and cards[0]["price"] == "₹ 80 / Piece"
    assert _run("nope", {}, 20)["error"].startswith("unknown tool")


# ---- approval gate (needs Postgres) ----
URL = os.environ.get("TEST_DATABASE_URL")


class FakeWA:
    def __init__(self):
        self.sent = []

    async def send(self, p, **kw):
        self.sent.append(p)


class FakeLLM:
    enabled = True

    async def json(self, system, user, **kw):
        return {"plan": [{"action": "goto", "url": "https://example.com"}, {"action": "click", "selector": "a"}]}


@pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
async def test_browse_waits_for_approval(monkeypatch):
    from src.db import migrate, open_pool
    from src.ingest import box_commands as bc
    pool = await open_pool(URL)
    async with pool.connection() as c:
        await c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await migrate(pool)
    ran = []

    async def fake_run(tool, args, timeout=90):
        ran.append(tool)
        return {"url": "https://example.com", "title": "Example", "extracted": ["hi"]}

    monkeypatch.setattr(bc, "run_box", fake_run)
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, wa=FakeWA(), llm=FakeLLM(), bg=set()))
    tenant = str(uuid.uuid4())

    assert await bc.handle_text(app, tenant, "91", "browse check example.com") is None
    card = app.state.wa.sent[0]
    assert "clicking" in card["interactive"]["body"]["text"] and ran == []   # proposed, not executed
    tid = card["interactive"]["action"]["buttons"][0]["reply"]["id"].split(":")[1]

    assert "cancelled" not in await bc.decide_browser_task(app, str(uuid.uuid4()), "91", tid, True)  # wrong tenant
    assert ran == []
    assert "Running" in await bc.decide_browser_task(app, tenant, "91", tid, True)
    await asyncio.gather(*app.state.bg)
    assert ran == ["run_personal_browser_task"] and "Example" in app.state.wa.sent[-1]["text"]["body"]
    assert "already handled" in await bc.decide_browser_task(app, tenant, "91", tid, True)  # no re-run
    await pool.close()


import asyncio  # noqa: E402
