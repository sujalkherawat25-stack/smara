"""The browser Smara uses on its own server cannot be seen or used by the owner. Smara must never ask them to sign in, type a
code or pay there, and must not show a card saying 'your turn in the browser'. On the owner's own PC the hand-off still works."""
import json

from test_agent_db import FakeBrowsers, ScriptLLM, call, env, one, owner, say  # noqa: F401
from test_bridge_db import DeviceFakeBrowsers

from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES
from src.ingest.dispatch import dispatch


def test_policy_refuses_hand_off_on_the_server_browser_and_explains():
    server = SessionCtx(id="s", tenant_id="t", trust="TRUSTED", on_device=False)
    d = pre_tool_use(server, "hand_off_to_owner", {"reason": "sign in"}, TOOL_NAMES)
    assert d.verdict == "deny"
    low = d.reason.lower()
    assert "cannot see" in low and "not supported yet" in low and "cart" in low
    pc = SessionCtx(id="s", tenant_id="t", trust="TRUSTED", on_device=True)
    assert pre_tool_use(pc, "hand_off_to_owner", {"reason": "sign in"}, TOOL_NAMES).verdict == "approve"


async def test_server_browser_login_wall_ends_with_an_honest_message_and_no_fake_card(env):
    env.app.state.browsers = FakeBrowsers()                      # a server browser: where() does not say "device"
    llm = env.app.state.llm = ScriptLLM(
        call("browser_step", action="navigate", url="https://shop.example.com/cart"),
        call("hand_off_to_owner", reason="Please sign in on the open page."),
        say("Your item is in the cart (Sony earphones, Rs 999) but checkout needs a sign-in, which I can't do from here yet. "
            "Please finish it on your phone or PC."))
    await dispatch(env.app, owner("so1", "add sony earphones on shop.example.com and check out"))
    sent = env.app.state.wa.sent
    cards = [m for m in sent if m.get("type") == "interactive"]
    assert not any("Your turn in the browser" in json.dumps(c) for c in cards)      # no card asking them to act on a page they cannot see
    waiting = await one(env.pool, "select count(*) n from agent_sessions where status = 'WAITING_APPROVAL'")
    assert waiting["n"] == 0
    tool_msgs = [m for m in llm.seen[-1] if m["role"] == "tool"]
    assert any("cannot see it" in str(m["content"]) for m in tool_msgs)             # the model was told why
    assert "can't do from here" in sent[-1]["text"]["body"]


async def test_on_the_owners_own_pc_the_hand_off_still_works(env):
    env.app.state.browsers = DeviceFakeBrowsers()
    env.app.state.llm = ScriptLLM(
        call("browser_step", action="navigate", url="https://example.com/account"),
        call("hand_off_to_owner", reason="Please sign in to your account in the open browser window."),
        say("ok"))
    await dispatch(env.app, owner("so2", "check my orders on example.com"))
    card = env.app.state.wa.sent[-1]["interactive"]
    assert "Your turn in the browser" in card["body"]["text"]
