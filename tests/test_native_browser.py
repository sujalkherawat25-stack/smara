"""Real Chromium on owned fixture HTTP pages; not live-provider quality."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import pytest
from smara.native_browser import NativeBrowser, configured_origins, TOOLS
from smara.managed_browser import StaleObservation


@pytest.mark.parametrize("url", ["file:///private", "http://127.0.0.1/", "http://localhost/", "https://user:password@example.com/", "https://example.com/path", "https://example.com/?key=private"])
def test_rejects_nonpublic_or_nonorigin_configuration(url):
    with pytest.raises(ValueError):
        configured_origins([url])


def test_action_tools_are_not_marked_readonly():
    for entry in TOOLS:
        assert entry["annotations"]["readOnlyHint"] == (entry["name"] in {"browser_observe", "browser_text_page", "browser_snapshot", "browser_tabs"})


def test_real_dom_actions_pagination_stale_and_auth_denial(tmp_path, monkeypatch):
    posts = []
    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_GET(self):
            body = ('<!doctype html><title>Owned fixture</title><input id="name"><button onclick="document.querySelector(\'#state\').textContent=document.querySelector(\'#name\').value">Save locally</button><span id="state">initial</span><input type="password"><input type="PASSWORD"><input type="file"><input type="FILE"><p>' + 'fixture evidence ' * 2000 + '</p>').encode()
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            posts.append(self.path); self.send_response(200); self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    # Dependency injection confines the real browser to the owned HTTP fixture.
    # Production exposes no private-network override or fixture CLI flag.
    monkeypatch.setattr("smara.native_browser._is_public_http_url", lambda url: url.startswith(base + "/") or url == base)
    browser = NativeBrowser(tmp_path, [base])
    try:
        first = browser.call("browser_open", {"url": base + "/"})
        assert first["title"] == "Owned fixture" and first["modality"] == "DOM text; no vision"
        parts, offset = [first["text"]], first["next_offset"]
        while offset is not None:
            result = browser.call("browser_text_page", {"observation_id": first["observation_id"], "offset": offset})
            parts.append(result["text"]); offset = result["next_offset"]
        assert len("".join(parts)) == first["total_chars"] > 16000
        name = next(ref for ref, entry in first["elements"].items() if entry["role"] == "textbox"
                    and browser.engine.attribute(browser.session, first["observation_id"], ref, "type") in (None, "text"))
        second = browser.call("browser_act", {"observation_id": first["observation_id"], "ref": name, "action": "fill", "value": "actual synthetic edit"})
        with pytest.raises(StaleObservation):
            browser.call("browser_act", {"observation_id": first["observation_id"], "ref": name, "action": "fill", "value": "stale"})
        button = next(ref for ref, entry in second["elements"].items() if entry["text"] == "Save locally")
        third = browser.call("browser_act", {"observation_id": second["observation_id"], "ref": button, "action": "click"})
        assert "actual synthetic edit" in third["text"]
        kinds = {ref: (browser.engine.attribute(browser.session, third["observation_id"], ref, "type") or "").lower()
                 for ref, entry in third["elements"].items() if entry["role"] in {"textbox", "file"}}
        for input_type in ("password", "file"):
            ref = next(ref for ref, kind in kinds.items() if kind == input_type)
            with pytest.raises(ValueError, match="authentication"):
                browser.call("browser_act", {"observation_id": third["observation_id"], "ref": ref, "action": "fill", "value": "never sent"})
        with pytest.raises(ValueError, match="origins"):
            browser.call("browser_open", {"url": "http://127.0.0.1:1/"})
        assert not posts and not browser.allow_writes
        assert browser.engine.cookies(browser.session) == []
    finally:
        browser.engine.shutdown(); server.shutdown(); server.server_close()


def test_any_public_site_still_refuses_private_addresses():
    from smara.native_browser import ANY_PUBLIC_SITE
    assert configured_origins([ANY_PUBLIC_SITE]) == {ANY_PUBLIC_SITE}
    browser = NativeBrowser.__new__(NativeBrowser)
    browser.origins = configured_origins([ANY_PUBLIC_SITE])
    assert browser.allowed("https://example.com/a?b=1")
    assert not browser.allowed("http://127.0.0.1:8000/")
    assert not browser.allowed("file:///etc/passwd")


class _FakeEngine:
    """Stands in for the managed browser so close/reopen rules can be tested without launching Chromium."""
    def __init__(self): self.created = 0; self.shut = 0; self.sessions = {}
    def create(self, _options, _configure): self.created += 1; return "s%d" % self.created
    def navigate(self, _session, _url): return _fake_observation()
    def shutdown(self): self.shut += 1


def _fake_observation():
    return {"observation_id": "o", "session_id": "s1", "tab_id": "t", "url": "https://example.com/", "title": "",
            "timestamp": time.time(), "generation": 1, "elements": {}, "listing": "", "total_controls": 0,
            "body_text": "", "screenshot": "", "screenshot_sha256": ""}


def _fake_browser(tmp_path):
    browser = NativeBrowser(tmp_path, ["https://example.com"])
    browser.engine = _FakeEngine()
    browser.observe = lambda: {"observation_id": "o", "url": "https://example.com/"}
    return browser


def test_agent_can_reopen_after_its_own_close_within_the_same_budget(tmp_path):
    browser = _fake_browser(tmp_path)
    browser.call("browser_open", {"url": "https://example.com/"})
    deadline = browser.deadline
    assert browser.call("browser_close", {})["can_reopen"] is True
    browser.call("browser_open", {"url": "https://example.com/"})
    assert browser.engine.created == 2 and browser.session == "s2"
    assert browser.deadline == deadline, "a reopen must not buy a fresh time budget"
    assert browser.actions == 2, "a reopen still counts against the action limit"


def test_budget_still_ends_the_session_after_an_agent_close(tmp_path):
    browser = _fake_browser(tmp_path)
    browser.call("browser_open", {"url": "https://example.com/"})
    browser.call("browser_close", {})
    browser.deadline = 0.0  # time budget already spent
    with pytest.raises(ValueError, match="bound reached"):
        browser.call("browser_open", {"url": "https://example.com/"})


def test_closure_not_requested_by_the_agent_still_needs_an_explicit_reconnect(tmp_path):
    browser = _fake_browser(tmp_path)
    browser.call("browser_open", {"url": "https://example.com/"})
    browser.session = None  # e.g. the owner stopped it or the engine dropped it
    with pytest.raises(ValueError, match="reconnect explicitly"):
        browser.call("browser_open", {"url": "https://example.com/"})


def test_failures_report_our_own_message_and_one_line_for_engine_errors():
    from smara.native_browser import failure_text
    assert failure_text(ValueError("Browser was closed; reconnect explicitly")) == "Browser was closed; reconnect explicitly"
    assert failure_text(StaleObservation("Observation expired; observe again")) == "Observation expired; observe again"
    engine_error = type("Error", (Exception,), {})("Page.goto: net::ERR_BLOCKED\nCall log:\n  - navigating to https://x/ secret-token-123")
    text = failure_text(engine_error)
    assert text.startswith("Error: Page.goto: net::ERR_BLOCKED.") and "Call log" not in text and "secret-token" not in text and "computer_" in text
