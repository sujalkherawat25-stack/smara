"""Desktop client for hosted approvals: pairing stores only a DPAPI-protected token; answers map server replies."""
import json
import os

import httpx
import pytest

from smara import hosted_approvals as ha

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the token is kept with Windows DPAPI")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "state" / "desktop.json"))
    monkeypatch.setenv("SMARA_BRIDGE_HOME", str(tmp_path / "bridge"))
    (tmp_path / "bridge").mkdir()
    (tmp_path / "bridge" / "config.json").write_text(json.dumps({"server": "https://ai.example.test/smara", "token": "device"}))
    seen = []
    state = {"approvals": [{"id": "11111111-2222-3333-4444-555555555555", "tool": "browser_step", "card": "Smara wants to click Next",
                            "step": {"action": "click"}, "site_choice": True, "asked_at": "2026-11-18T10:00:00+00:00", "extra": "x"}],
             "answers": {}}

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, request.headers.get("authorization", "")))
        if request.url.path.endswith("/bridge/approvals/pair"):
            body = json.loads(request.content)
            return httpx.Response(200, json={"owner_token": "sma_secret", "shop": "Sharma Traders"}) if body["code"] == "ABCD-EFGH" \
                else httpx.Response(400, json={"detail": "bad"})
        if request.headers.get("authorization") != "Bearer sma_secret":
            return httpx.Response(401)
        if request.method == "GET":
            return httpx.Response(200, json={"approvals": state["approvals"]})
        sid = request.url.path.rsplit("/", 1)[1]
        if sid in state["answers"]:
            return httpx.Response(409)
        state["answers"][sid] = json.loads(request.content)["choice"]
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(ha, "_client", lambda base: httpx.Client(base_url=base, transport=httpx.MockTransport(handler)))
    return seen, state


def test_pairing_keeps_only_a_protected_token(server):
    seen, _ = server
    assert ha.status()["connected"] is False and ha.status()["server"] == "https://ai.example.test/smara"
    with pytest.raises(ValueError, match="wrong, used, or expired"):
        ha.manage({"operation": "hosted_approvals", "action": "pair", "code": "ZZZZ-ZZZZ"})
    out = ha.manage({"operation": "hosted_approvals", "action": "pair", "code": "abcd-efgh"})
    assert out == {"connected": True, "shop": "Sharma Traders", "server": "https://ai.example.test/smara"}
    raw = ha.store_path().read_text(encoding="utf-8")
    assert "sma_secret" not in raw                                   # DPAPI-protected on disk
    assert seen[-1][2] == ""                                          # pairing sends no token


def test_list_and_answers_first_wins(server):
    _, state = server
    ha.pair("ABCD-EFGH")
    listed = ha.manage({"operation": "hosted_approvals", "action": "list"})
    assert listed["approvals"][0]["card"] == "Smara wants to click Next" and "extra" not in listed["approvals"][0]
    sid = listed["approvals"][0]["id"]
    assert ha.decide(sid, "site") == {"outcome": "done"} and state["answers"][sid] == "site"
    assert ha.decide(sid, "approve") == {"outcome": "already"}
    for bad in (("not-a-session!", "approve"), (sid, "pay")):
        with pytest.raises(ValueError):
            ha.decide(*bad)


def test_a_revoked_token_disconnects_this_pc(server, monkeypatch):
    ha.pair("ABCD-EFGH")
    from smara.native_profiles import protect, write_object
    write_object(ha.store_path(), {"server": "https://ai.example.test/smara", "shop": "x", "token": protect("sma_revoked")})
    with pytest.raises(ValueError, match="switched approvals off"):
        ha.pending()
    assert ha.status()["connected"] is False


def test_no_paired_browser_means_no_server_to_ask(server, tmp_path):
    (tmp_path / "bridge" / "config.json").unlink()
    with pytest.raises(ValueError, match="Pair this PC's browser"):
        ha.pair("ABCD-EFGH")
    with pytest.raises(ValueError, match="not connected"):
        ha.pending()
