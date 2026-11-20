"""Desktop side of the one-tap sign-in: a code and links to show, both tokens stored on approval, nothing on denial."""
import json

import httpx
import pytest

from smara import hosted_approvals, hosted_login


class Fake:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, server):
        outer = self

        class Client:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def post(self, path, json=None, headers=None):
                outer.calls.append((path, json, headers))
                status, body = outer.replies.pop(0)
                return httpx.Response(status, json=body, request=httpx.Request("POST", server + path))
        return Client()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_BRIDGE_HOME", str(tmp_path / "bridge"))
    monkeypatch.setattr(hosted_login, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(hosted_login, "approvals_path", lambda: tmp_path / "hosted-approvals.json")
    monkeypatch.setattr(hosted_approvals, "store_path", lambda: tmp_path / "hosted-approvals.json")
    monkeypatch.setattr(hosted_login, "protect", lambda v: v[::-1])
    monkeypatch.setattr(hosted_login, "unprotect", lambda v: v[::-1])
    monkeypatch.setattr(hosted_approvals, "unprotect", lambda v: v[::-1])
    return tmp_path


START = {"device_secret": "sdl_abc", "code": "ABCD-EFGH", "expires_in": 600, "links": {
    "telegram": "https://t.me/SmaraBot?start=login_ABCD-EFGH", "whatsapp": "https://wa.me/9198?text=login%20ABCD-EFGH",
    "evil": "https://evil.example/x"}}


def test_start_shows_the_code_and_only_telegram_and_whatsapp_links(home, monkeypatch):
    fake = Fake([(200, START)])
    monkeypatch.setattr(hosted_login, "_client", fake)
    out = hosted_login.start()
    assert out["waiting"] and out["code"] == "ABCD-EFGH" and out["message"] == "login ABCD-EFGH" and not out["signed_in"]
    assert set(out["links"]) == {"telegram", "whatsapp"}
    assert "sdl_abc" not in (home / "hosted-login.json").read_text()      # the secret is stored protected


def test_a_server_that_is_not_https_is_refused(home):
    with pytest.raises(ValueError, match="https"):
        hosted_login.start("http://evil.example")


def test_pending_then_approved_stores_both_tokens_and_clears_the_pending_login(home, monkeypatch):
    monkeypatch.setattr(hosted_login, "_client", Fake([(200, START), (200, {"status": "pending"}), (200, {"status": "asked"}),
        (200, {"status": "approved", "device_token": "dev-t", "owner_token": "sma_own", "shop": "Demo Shop"})]))
    hosted_login.start()
    assert hosted_login.poll()["state"] == "pending"
    assert hosted_login.poll()["state"] == "asked"
    done = hosted_login.poll()
    assert done["state"] == "approved" and done["signed_in"] and done["shop"] == "Demo Shop" and not done["waiting"]
    cfg = json.loads((home / "bridge" / "config.json").read_text())
    assert cfg["token"] == "dev-t" and cfg["shop"] == "Demo Shop" and cfg["server"].startswith("https://")
    assert "sma_own" not in (home / "hosted-approvals.json").read_text()          # protected, not plain
    assert hosted_login.poll()["state"] == "none"


@pytest.mark.parametrize("word", ["denied", "expired"])
def test_denied_or_expired_leaves_nothing_behind(home, monkeypatch, word):
    monkeypatch.setattr(hosted_login, "_client", Fake([(200, START), (200, {"status": word})]))
    hosted_login.start()
    assert hosted_login.poll()["state"] == word
    assert not hosted_login.status()["waiting"] and not hosted_login.status()["signed_in"]
    assert not (home / "bridge" / "config.json").exists()


def test_sign_out_forgets_everything_and_asks_the_server_to_revoke(home, monkeypatch):
    fake = Fake([(200, START), (200, {"status": "approved", "device_token": "d", "owner_token": "sma_o", "shop": "S"}), (200, {"ok": True})])
    monkeypatch.setattr(hosted_login, "_client", fake)
    hosted_login.start(); hosted_login.poll()
    out = hosted_login.sign_out()
    assert not out["signed_in"] and not (home / "bridge" / "config.json").exists() and not (home / "hosted-approvals.json").exists()
    path, _, headers = fake.calls[-1]
    assert path == "/bridge/logout" and headers == {"Authorization": "Bearer sma_o"}


def test_unknown_requests_are_refused(home):
    with pytest.raises(ValueError):
        hosted_login.manage({"operation": "hosted_login", "action": "start", "token": "x"})
    with pytest.raises(ValueError):
        hosted_login.manage({"operation": "hosted_login", "action": "nope"})
