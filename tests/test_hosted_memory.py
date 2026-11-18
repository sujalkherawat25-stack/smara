"""Optional memory sharing from Desktop: off by default, per connected shop, scrubbed, never blocks a chat turn."""
import json
import os

import httpx
import pytest

from smara import hosted_approvals as ha
from smara import hosted_memory as hm

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the token is kept with Windows DPAPI")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "state" / "desktop.json"))
    monkeypatch.setenv("SMARA_BRIDGE_HOME", str(tmp_path / "bridge"))
    (tmp_path / "bridge").mkdir()
    (tmp_path / "bridge" / "config.json").write_text(json.dumps({"server": "https://ai.example.test/smara", "token": "device"}))
    state = {"enabled": False, "notes": [], "forgot": 0, "shop": "Sharma Traders"}

    def handler(request: httpx.Request):
        path = request.url.path
        if path.endswith("/bridge/approvals/pair"):
            return httpx.Response(200, json={"owner_token": "sma_secret", "shop": state["shop"]})
        if request.headers.get("authorization") != "Bearer sma_secret":
            return httpx.Response(401)
        body = json.loads(request.content) if request.content else {}
        if path.endswith("/memory/enable"):
            state["enabled"] = body["enabled"]
            return httpx.Response(200, json={"enabled": body["enabled"]})
        if path.endswith("/memory/forget"):
            state["forgot"] += 1
            return httpx.Response(200, json={"ok": True})
        if not state["enabled"]:
            return httpx.Response(403)
        if path.endswith("/memory/notes"):
            state["notes"].append(body["text"])
            return httpx.Response(200, json={"stored": True})
        if path.endswith("/memory/search"):
            return httpx.Response(200, json={"state_card": "Shop closes Sundays. key=sk_live_abcdefghijkl"})
        return httpx.Response(404)

    monkeypatch.setattr(ha, "_client", lambda base: httpx.Client(base_url=base, transport=httpx.MockTransport(handler)))
    return state


def test_off_by_default_and_nothing_is_sent_until_turned_on(server):
    assert hm.status() == {"connected": False, "shop": "", "enabled": False}
    with pytest.raises(ValueError, match="Connect this PC"):
        hm.set_enabled(True)
    ha.pair("ABCD-EFGH")
    assert hm.status()["enabled"] is False
    assert hm.share_turn("hello", "hi") is False and server["notes"] == []


def test_turned_on_sends_only_scrubbed_text_and_turning_off_stops_it(server):
    ha.pair("ABCD-EFGH")
    assert hm.manage({"operation": "hosted_memory", "action": "set", "enabled": True})["enabled"] is True
    sent = hm.share_turn("My card is 4111 1111 1111 1111, OTP is 551234", "Noted. password: hunter2hunter2")
    assert sent is True
    note = server["notes"][0]
    assert "4111" not in note and "551234" not in note and "hunter2" not in note
    hm.set_enabled(False)
    assert hm.share_turn("more", "more") is False and len(server["notes"]) == 1 and server["enabled"] is False


def test_it_is_tied_to_the_shop_and_resets_when_the_connection_changes(server):
    ha.pair("ABCD-EFGH")
    hm.set_enabled(True)
    assert hm.enabled() is True
    server["shop"] = "Another Shop"
    ha.disconnect()
    ha.pair("ABCD-EFGH")                                 # a different shop on the same PC: sharing is off again
    assert hm.enabled() is False and hm.share_turn("a", "b") is False


def test_search_and_forget_and_server_side_off(server):
    ha.pair("ABCD-EFGH")
    with pytest.raises(ValueError, match="off"):
        hm.search("opening hours")
    hm.set_enabled(True)
    card = hm.search("opening hours")["state_card"]
    assert "Sundays" in card and "sk_live" not in card
    assert hm.forget()["forgotten"] is True and server["forgot"] == 1
    server["enabled"] = False                            # the owner sent 'stop sharing desktop memory' in chat
    assert hm.share_turn("x", "y") is False and hm.enabled() is False


def test_a_failing_server_never_breaks_a_chat_turn(server, monkeypatch):
    ha.pair("ABCD-EFGH")
    hm.set_enabled(True)
    monkeypatch.setattr(ha, "_client", lambda base: (_ for _ in ()).throw(RuntimeError("offline")))
    assert hm.share_turn("a", "b") is False
