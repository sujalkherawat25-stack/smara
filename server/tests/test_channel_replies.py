"""Replies that look right on Telegram and WhatsApp: no stray marks, long answers split into short messages, Telegram never
loses a message to bad HTML, the owner's reply setting, the cloud browser for shops without a PC, and the hourly error report."""
import asyncio
import dataclasses
import html
import logging
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from test_agent_db import OWNER, ScriptLLM, call, env, one, owner, say  # noqa: F401

from src import health_watch, prefs
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES
from src.config import Settings, pc_allowed
from src.ingest.dispatch import dispatch
from src.loop import owner_channel
from src.loop.owner_channel import parts_of, wa_to_telegram
from src.loop.wa_format import (CARD_CHARS, MAX_PARTS, PART_CHARS, TOO_LONG, html_to_plain, split_text, to_telegram_html,
                                to_whatsapp, wa_to_html)
from src.loop.whatsapp import text_message


# ------------------------------------------------------------------------------------------------ formatting

def test_markdown_the_model_slips_in_becomes_clean_chat_text():
    assert to_whatsapp("***Total*** \\*approx\\*") == "*Total* *approx*"
    assert to_whatsapp("<b>Ravi</b> owes<br>₹500") == "*Ravi* owes\n₹500"
    assert to_whatsapp("Name | Amount | Due\n--- | --- | ---\nRavi | ₹500 | 12 Nov") == \
        "• Name · Amount · Due\n• Ravi · ₹500 · 12 Nov"
    assert to_whatsapp("### **Summary**") == "*Summary*"
    assert to_whatsapp("*Ravi Traders* owes ₹500*") == "*Ravi Traders* owes ₹500"      # a lone mark would show raw
    assert to_whatsapp("5 * 3 = 15") == "5 * 3 = 15"                                     # arithmetic is left alone


def test_formatting_twice_changes_nothing():
    src = "## Dues\n**Ravi**: ₹12,500 `INV-1`\n- Sharma\n| a | b |\n|---|---|\n| 1 | 2 |"
    once = to_whatsapp(src)
    assert to_whatsapp(once) == once


def test_telegram_html_keeps_links_whole_and_is_always_balanced():
    out = to_telegram_html("Pay here: https://pay.example.com/a_b_c_d?x=_y_ and *now*")
    assert "https://pay.example.com/a_b_c_d?x=_y_" in out and "<i>" not in out and "<b>now</b>" in out
    for text in ("**a** _b_ ~c~ <x> & `code` ```\npre < >\n```", "*unclosed bold and _half", "a_b_c *x* y_z"):
        h = to_telegram_html(text)
        for tag in ("b", "i", "s", "pre"):
            assert h.count(f"<{tag}>") == h.count(f"</{tag}>"), (text, h)
    assert html_to_plain("<b>Ravi</b> &amp; <i>Co</i>") == "Ravi & Co"


# ------------------------------------------------------------------------------------------------ splitting

def _para(i: int, n: int = 300) -> str:
    return f"Part {i}: " + ("word " * (n // 5)).strip()


def test_short_text_is_one_part_and_long_text_splits_at_paragraphs():
    assert split_text("Ravi owes ₹500.") == ["Ravi owes ₹500."]
    assert split_text("") == []
    text = "\n\n".join(_para(i) for i in range(12))
    parts = split_text(text)
    assert 2 <= len(parts) <= MAX_PARTS and all(len(p) <= PART_CHARS for p in parts)
    assert parts[0].startswith("Part 0:") and all(p.split(":")[0].startswith("Part") for p in parts)   # cut between paragraphs


def test_too_much_text_ends_with_a_note_instead_of_a_flood_of_messages():
    parts = split_text("\n\n".join(_para(i, 900) for i in range(20)))
    assert len(parts) == MAX_PARTS and parts[-1].endswith(TOO_LONG) and all(len(p) <= PART_CHARS for p in parts)


def test_one_huge_line_is_cut_at_spaces_and_code_blocks_stay_closed():
    parts = split_text("x " * 2000)
    assert len(parts) >= 2 and all(len(p) <= PART_CHARS for p in parts) and all(re.fullmatch(r"[x ]+", p) for p in parts)
    code = "Result:\n```\n" + "\n".join(f"row {i} = {i * 7}" for i in range(400)) + "\n```\nDone"
    for p in split_text(code):
        assert p.count("```") % 2 == 0


def test_a_long_card_keeps_its_buttons_on_a_short_last_part():
    card = {"messaging_product": "whatsapp", "to": OWNER, "type": "interactive", "interactive": {
        "type": "button", "body": {"text": "\n\n".join(_para(i) for i in range(8))},
        "action": {"buttons": [{"type": "reply", "reply": {"id": "ag_ok:1", "title": "Approve"}}]}}}
    parts = parts_of(card)
    assert len(parts) >= 2 and parts[-1]["type"] == "interactive"
    assert len(parts[-1]["interactive"]["body"]["text"]) <= CARD_CHARS
    assert all(p["type"] == "text" and len(p["text"]["body"]) <= PART_CHARS for p in parts[:-1])
    assert parts_of(text_message(OWNER, "short")) == [text_message(OWNER, "short")]


class FakeTG:
    def __init__(self):
        self.sent, self.actions = [], []

    async def send(self, payload):
        self.sent.append(payload)

    async def typing(self, chat_id):
        self.actions.append(chat_id)


async def test_a_long_answer_reaches_telegram_as_several_valid_messages(monkeypatch):
    monkeypatch.setattr(owner_channel, "PART_GAP_S", 0)
    tg = FakeTG()
    app = SimpleNamespace(state=SimpleNamespace(pool=None, settings=Settings(), tg=tg, wa=None))
    tenant = SimpleNamespace(id="t", owner_phone=OWNER, telegram_chat_id=42, owner_last_channel="telegram",
                             owner_last_inbound_at=None)
    body = "\n\n".join(f"*Customer {i}* owes ₹{i},000 & more <details>" for i in range(200))
    how = await owner_channel._deliver(app, tenant, text_message(OWNER, body), kind="owner_text", reply_channel="telegram")
    assert how == "telegram" and 2 <= len(tg.sent) <= MAX_PARTS
    for m in tg.sent:
        visible = html.unescape(re.sub(r"<[^>]+>", "", m["text"]))
        assert len(visible) <= 4096 and m["text"].count("<b>") == m["text"].count("</b>") and m["parse_mode"] == "HTML"


async def test_telegram_resends_plain_text_when_it_refuses_the_html():
    from src.ingest.telegram import TelegramClient
    seen = []

    def handler(req: httpx.Request):
        import json
        body = json.loads(req.content)
        seen.append(body)
        if body.get("parse_mode"):
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"})
        return httpx.Response(200, json={"ok": True, "result": {}})
    tg = TelegramClient("t", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await tg.send({"chat_id": 1, "text": "<b>Ravi</b> &amp; Co", "parse_mode": "HTML"})
    assert len(seen) == 2 and seen[1] == {"chat_id": 1, "text": "Ravi & Co"}


# ------------------------------------------------------------------------------------------------ reply setting

def test_style_commands_are_understood_and_sentences_are_left_to_the_agent():
    assert prefs.parse("style") == ("show", None) and prefs.parse("/style") == ("show", None)
    assert prefs.parse("style Hindi") == ("set", "hindi") and prefs.parse("style chhota") == ("set", "chhota")
    assert prefs.parse("style note amount lakh mein batao") == ("note", "amount lakh mein batao")
    assert prefs.parse("style reset") == ("reset", None)
    assert prefs.parse("settings mein kya kya hai?") is None and prefs.parse("Ravi ka style") is None


async def test_the_owner_sets_their_reply_style_with_the_command_and_the_buttons(env):
    await dispatch(env.app, owner("st1", "style"))
    cards = [m for m in env.app.state.wa.to(OWNER) if m.get("type") == "interactive"]
    assert len(cards) == 2 and [b["reply"]["id"] for b in cards[1]["interactive"]["action"]["buttons"]] == \
        ["pref:len:short", "pref:len:normal", "pref:len:detailed"]
    await dispatch(env.app, owner("st2", button="pref:lang:hindi"))
    await dispatch(env.app, owner("st3", "style short"))
    await dispatch(env.app, owner("st4", "style note amount lakh mein batao"))
    p = await prefs.get(env.pool, env.t.id)
    assert p == {"language": "hindi", "length": "short", "note": "amount lakh mein batao"}
    assert "Bhasha: Hindi" in env.app.state.wa.sent[-2]["text"]["body"] or "lakh" in env.app.state.wa.sent[-1]["text"]["body"]
    assert (await prefs.get(env.pool, env.other.id))["language"] == "auto"               # another shop is untouched
    await dispatch(env.app, owner("st5", "style reset"))
    assert await prefs.get(env.pool, env.t.id) == prefs.DEFAULT


async def test_the_setting_reaches_the_agent_and_the_agent_can_change_it(env):
    from src.agent.prompt import build_system
    await prefs.put(env.pool, env.t.id, language="hinglish", length="short", note="call me Sujal bhai")
    text = await build_system(env.app, env.t, "TRUSTED")
    assert "Hindi words written in English letters" in text and "1 to 3 lines" in text and "Sujal bhai" in text
    assert text.rstrip().endswith("Sujal bhai")                                       # the last thing the model reads
    assert "Sujal bhai" not in await build_system(env.app, env.t, "UNTRUSTED")
    llm = env.app.state.llm = ScriptLLM(call("set_reply_style", language="english", length="detailed"), say("Done."))
    await dispatch(env.app, owner("st6", "from now on reply in English, in detail"))
    assert llm.last_tool_result()["saved"] is True
    assert (await prefs.get(env.pool, env.t.id))["language"] == "english"
    for trust in ("UNTRUSTED", "SYSTEM"):
        assert pre_tool_use(SessionCtx("s", "t", trust, party_id="p"), "set_reply_style", {"length": "short"}, TOOL_NAMES).verdict == "deny"
    r = await prefs.set_reply_style(env.app, SessionCtx("s", env.t.id, "TRUSTED"), {"language": "klingon"})
    assert "error" in r


# ------------------------------------------------------------------------------------------------ browser per shop

def test_pc_browser_only_for_listed_shops():
    assert pc_allowed(Settings(), "abc") is True                                   # tests and local runs
    assert pc_allowed(Settings(pc_browser_tenants=""), "abc") is False             # production default: cloud browser for all
    assert pc_allowed(Settings(pc_browser_tenants="ABC, def"), "abc") is True
    assert pc_allowed(Settings(pc_browser_tenants="def"), "abc") is False


async def test_a_shop_without_pc_access_uses_the_cloud_browser_and_is_never_asked_to_pair(env):
    from src.agent.prompt import build_system
    from src.bridge.device import BrowserRouter
    env.app.state.settings = dataclasses.replace(env.app.state.settings, pc_browser_tenants="")
    await dispatch(env.app, owner("pb1", "pair browser"))
    assert "Smara Desktop" in env.app.state.wa.sent[-1]["text"]["body"] and "code" not in env.app.state.wa.sent[-1]["text"]["body"].lower()
    text = await build_system(env.app, env.t, "TRUSTED")
    assert "cloud browser" in text and "suggest Smara Desktop (Settings > Browser)" not in text
    router = BrowserRouter(env.pool, server_browser=None, pc_allowed=lambda t: False)
    assert await router._paired(env.t.id) is None
    from src.agent.pc import pc_files
    r = await pc_files(env.app, SessionCtx("s", env.t.id, "TRUSTED"), {"action": "list"})
    assert "not available" in r["error"]


# ------------------------------------------------------------------------------------------------ hourly error report

@pytest.fixture
def fresh_watch(monkeypatch):
    health_watch.reset()
    monkeypatch.setattr(health_watch, "_last_report", -1e9)
    health_watch.install()
    yield
    health_watch.reset()


async def test_errors_are_counted_and_sent_once_outside_quiet_hours(fresh_watch):
    tg = FakeTG()
    settings = Settings(ops_telegram_chat_id=-100, quiet_start_hour=21, quiet_end_hour=8)
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, tg=tg))
    try:
        raise RuntimeError("db went away")
    except RuntimeError:
        logging.getLogger("src.agent_loop").exception("agent session %s failed", "abc")
    health_watch.tool_failed("run_python", ValueError("bad"))
    night = datetime(2026, 11, 14, 17, 30, tzinfo=timezone.utc)          # 23:00 IST: held
    assert await health_watch.report_due(app, night) is False and tg.sent == []
    day = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)              # 09:30 IST: the night's errors go out
    assert await health_watch.report_due(app, day) is True
    text = tg.sent[0]["text"]
    assert tg.sent[0]["chat_id"] == -100 and "1 error(s), 1 tool failure(s)" in text and "RuntimeError: db went away" in text
    assert "run_python" in text
    assert await health_watch.report_due(app, day) is False                     # at most once an hour


async def test_a_quiet_hour_with_no_errors_sends_nothing(fresh_watch):
    tg = FakeTG()
    app = SimpleNamespace(state=SimpleNamespace(settings=Settings(ops_telegram_chat_id=-100), tg=tg))
    logging.getLogger("src.x").error("no exception attached")                   # not an exception: not counted
    assert await health_watch.report_due(app, datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)) is False
    assert tg.sent == []


async def test_a_tool_that_raises_is_counted_by_the_agent_loop(env, fresh_watch, monkeypatch):
    from src import agent_loop

    async def boom(app, ctx, args):
        raise KeyError("nope")
    monkeypatch.setitem(agent_loop.EXECUTORS, "list_followups", boom)
    env.app.state.llm = ScriptLLM(call("list_followups"), say("ok"))
    await dispatch(env.app, owner("hw1", "followups?"))
    assert "list_followups" in health_watch.summary()


async def test_the_agent_is_given_the_overdue_total_in_rupees(env):
    from src.agent.prompt import _inr, ledger_line
    assert (_inr(45322500), _inr(100), _inr(150), _inr(12345678901)) == ("4,53,225", "1", "1.50", "12,34,56,789.01")
    line = await ledger_line(env.pool, env.t.id)
    assert "1 open invoices, Rs 10,000 outstanding" in line and "1 are overdue, Rs 10,000" in line
