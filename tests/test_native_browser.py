"""Real Chromium on owned fixture HTTP pages; not live-provider quality."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import pytest
from smara.native_browser import NativeBrowser, configured_origins, TOOLS
from smara.managed_browser import StaleObservation


@pytest.mark.parametrize("url", ["file:///private", "http://127.0.0.1/", "http://localhost/", "https://user:password@example.com/", "https://example.com/path", "https://example.com/?key=private"])
def test_rejects_nonpublic_or_nonorigin_configuration(url):
    with pytest.raises(ValueError):
        configured_origins([url])


def test_action_tools_are_not_marked_readonly():
    for entry in TOOLS:
        assert entry["annotations"]["readOnlyHint"] == (entry["name"] in {"browser_observe", "browser_text_page"})


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
        name = next(ref for ref, entry in first["elements"].items() if entry["tag"] == "input")
        second = browser.call("browser_act", {"observation_id": first["observation_id"], "ref": name, "action": "fill", "value": "actual synthetic edit"})
        with pytest.raises(StaleObservation):
            browser.call("browser_act", {"observation_id": first["observation_id"], "ref": name, "action": "fill", "value": "stale"})
        button = next(ref for ref, entry in second["elements"].items() if entry["text"] == "Save locally")
        third = browser.call("browser_act", {"observation_id": second["observation_id"], "ref": button, "action": "click"})
        assert "actual synthetic edit" in third["text"]
        native_page = browser.engine.page(browser.engine.sessions[browser.session])
        for input_type in ("password", "PASSWORD", "file", "FILE"):
            ref = next(ref for ref, entry in third["elements"].items() if native_page.locator(entry["selector"]).get_attribute("type") == input_type)
            with pytest.raises(ValueError, match="authentication"):
                browser.call("browser_act", {"observation_id": third["observation_id"], "ref": ref, "action": "fill", "value": "never sent"})
        with pytest.raises(ValueError, match="origins"):
            browser.call("browser_open", {"url": "http://127.0.0.1:1/"})
        assert not posts and not browser.allow_writes
        assert browser.engine.sessions[browser.session].context.cookies() == []
    finally:
        browser.engine.shutdown(); server.shutdown(); server.server_close()
