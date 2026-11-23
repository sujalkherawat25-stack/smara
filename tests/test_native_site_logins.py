"""Saved site logins: typed on this PC by the browser, never shown to the model, only on their own site."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from smara import native_site_logins as logins


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "desktop.json"))
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(tmp_path / "credentials.json"))
    from smara import native_management
    monkeypatch.setattr(native_management, "settings_path", lambda: tmp_path / "native-settings.json")
    monkeypatch.setattr(native_management, "vault_path", lambda: tmp_path / "credentials.json")
    return tmp_path


def test_a_saved_login_is_used_only_on_its_own_site_and_the_password_is_not_in_plain_files(vault):
    from smara.native_profiles import write_object
    saved = logins.save({}, "https://www.gst.gov.in/login", "ramesh@shop.in", "S3cret-pass!")
    write_object(vault / "native-settings.json", saved)
    assert logins.listing(saved) == [{"host": "gst.gov.in", "username": "ramesh@shop.in", "updated_at": saved["site_logins"][0]["updated_at"]}]
    assert logins.for_url("https://services.gst.gov.in/services/login")[1:] == ("ramesh@shop.in", "S3cret-pass!")
    for other in ("https://gst.gov.in.evil.com/login", "https://gstgov.in/login", "https://example.com/"):
        assert logins.for_url(other) is None                                   # look-alike sites get nothing
    for f in vault.iterdir():
        assert "S3cret-pass!" not in f.read_text(errors="ignore")
    write_object(vault / "native-settings.json", logins.delete(saved, "gst.gov.in"))
    assert logins.for_url("https://gst.gov.in/") is None


@pytest.mark.parametrize("bad", ["", "localhost", "not a site", "http://"])
def test_only_real_site_addresses_are_accepted(bad):
    with pytest.raises(ValueError):
        logins.clean_host(bad)


def test_the_browser_logs_in_without_the_password_reaching_the_model_or_the_server_path(tmp_path, monkeypatch):
    from smara.native_browser import NativeBrowser
    from smara import smara_bridge

    class Site(BaseHTTPRequestHandler):
        def log_message(self, *_a): pass
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b'<title>Portal</title><form method="post" action="/in"><label>Email <input name="email" type="email"></label>'
                             b'<label>Password <input name="pw" type="password"></label><label>OTP <input name="otp" autocomplete="one-time-code"></label>'
                             b'<button type="submit">Sign in</button></form>')
        def do_POST(self):
            form = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
            ok = form.get("pw") == ["S3cret-pass!"] and form.get("otp") == ["482913"]
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(f"<title>Home</title><p>{'Welcome ' + form['email'][0] if ok else 'Wrong login'}</p>".encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr("smara.native_browser._is_public_http_url", lambda url: url.startswith(base + "/") or url == base)
    monkeypatch.setattr(logins, "for_url", lambda url: ("portal.test", "ramesh@shop.in", "S3cret-pass!") if url.startswith(base) else None)
    state = {"paused": False, "stopped": False}
    control = SimpleNamespace(id="t", state=lambda: state, update=lambda **v: state.update(v))
    browser = NativeBrowser(tmp_path, [base], {}, control)
    try:
        browser.call("browser_open", {"url": base + "/"})
        page = browser.call("browser_observe", {})
        refs = {e["name"].split()[0].lower(): r for r, e in page["elements"].items() if e.get("name")}
        with pytest.raises(ValueError):          # the ordinary action still refuses a password field
            browser.call("browser_act", {"observation_id": page["observation_id"], "ref": refs["password"], "action": "fill", "value": "x"})
        page = browser.call("browser_observe", {})
        with pytest.raises(ValueError, match="code"):
            browser.call("browser_fill_code", {"observation_id": page["observation_id"], "ref": refs["otp"], "code": "12"})
        page = browser.call("browser_observe", {})
        got = browser.call("browser_fill_code", {"observation_id": page["observation_id"], "ref": refs["otp"], "code": "482913"})
        page = got["observation"]
        done = browser.call("browser_login", {"observation_id": page["observation_id"], "username_ref": refs["email"],
                                              "password_ref": refs["password"], "submit_ref": refs["sign"]})
        assert done["username"] == "ramesh@shop.in" and "S3cret-pass!" not in json.dumps(done)
        assert "Welcome ramesh@shop.in" in browser.call("browser_observe", {})["listing"] + browser.latest["text"]
        assert "S3cret-pass!" not in json.dumps([a for s in browser.engine.sessions.values() for a in s.actions])
    finally:
        browser.call("browser_close", {})
        server.shutdown()
    assert "fill_secret" not in json.dumps(smara_bridge.__dict__.get("ACTIONS", []))       # never a server command


def test_settings_can_save_list_and_remove_a_site_login(vault, monkeypatch):
    from smara import native_management
    out = native_management.manage({"operation": "save_site_login", "host": "portal.example.com", "username": "ramesh", "password": "pw-123456"})
    assert [r["host"] for r in out["preferences"]["site_logins"]] == ["portal.example.com"]
    assert "pw-123456" not in json.dumps(out)
    assert logins.for_url("https://portal.example.com/login")[2] == "pw-123456"
    out = native_management.manage({"operation": "delete_site_login", "host": "portal.example.com"})
    assert out["preferences"]["site_logins"] == [] and logins.for_url("https://portal.example.com/") is None
