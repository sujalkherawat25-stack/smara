"""Smara's browser keeps the owner's sign-ins in its own profile, and a busy profile never stops a task."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright")


class Site(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        if self.path.startswith("/signin"):
            self.send_header("Set-Cookie", "session=owner-42; Max-Age=86400; Path=/")
        self.end_headers()
        who = "signed in" if "session=owner-42" in (self.headers.get("Cookie") or "") else "signed out"
        self.wfile.write(f"<title>Portal</title><p>You are {who}</p>".encode())


@pytest.fixture
def site(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr("smara.native_browser._is_public_http_url", lambda url: url.startswith(base))
    yield base
    server.shutdown()


def browser(tmp_path):
    from smara.native_browser import NativeBrowser
    state = {"paused": False, "stopped": False}
    control = SimpleNamespace(id="t", state=lambda: state, update=lambda **v: state.update(v))
    return NativeBrowser(tmp_path, ["*"], {}, control)


def text(b):
    page = b.call("browser_observe", {})
    return page.get("listing", "") + (b.latest or {}).get("text", "")


def test_a_sign_in_survives_to_the_next_run(tmp_path, site, monkeypatch):
    monkeypatch.setenv("SMARA_BROWSER_PROFILE", str(tmp_path / "profile"))
    first = browser(tmp_path)
    try:
        first.call("browser_open", {"url": site + "/signin"})            # the site sets its sign-in cookie
    finally:
        first.call("browser_close", {})
        first.engine.shutdown()
    second = browser(tmp_path)                                   # a later task, a new browser process
    try:
        second.call("browser_open", {"url": site + "/"})
        assert "You are signed in" in text(second)
    finally:
        second.call("browser_close", {})
        second.engine.shutdown()


def test_without_a_profile_every_run_starts_fresh(tmp_path, site, monkeypatch):
    monkeypatch.delenv("SMARA_BROWSER_PROFILE", raising=False)
    first = browser(tmp_path)
    try:
        first.call("browser_open", {"url": site + "/signin"})
    finally:
        first.call("browser_close", {})
        first.engine.shutdown()
    second = browser(tmp_path)
    try:
        second.call("browser_open", {"url": site + "/"})
        assert "You are signed out" in text(second)
    finally:
        second.call("browser_close", {})
        second.engine.shutdown()


def test_a_profile_in_use_by_another_task_falls_back_to_a_fresh_browser(tmp_path, site, monkeypatch):
    monkeypatch.setenv("SMARA_BROWSER_PROFILE", str(tmp_path / "profile"))
    holder, other = browser(tmp_path), browser(tmp_path)
    try:
        holder.call("browser_open", {"url": site + "/signin"})
        other.call("browser_open", {"url": site + "/"})          # same profile already open: still works
        assert "You are" in text(other)
    finally:
        for b in (holder, other):
            b.call("browser_close", {})
            b.engine.shutdown()
