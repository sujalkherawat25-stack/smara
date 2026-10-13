from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from types import SimpleNamespace
import pytest
from smara.native_browser import NativeBrowser
from smara.native_browser_transfer import upload_file, save_download


def test_upload_boundary_and_download_type(tmp_path):
    (tmp_path / "note.txt").write_text("Synthetic upload")
    assert upload_file(tmp_path, "note.txt").name == "note.txt"
    (tmp_path / ".env.txt").write_text("Not sent")
    for name in (".env.txt", "../outside.txt"):
        with pytest.raises((ValueError, FileNotFoundError)): upload_file(tmp_path, name)
    deleted = []
    with pytest.raises(ValueError, match="type"):
        save_download(tmp_path, SimpleNamespace(suggested_filename="../evil.exe", delete=lambda: deleted.append(True)))
    assert deleted == [True]


def test_owned_chromium_screenshot_upload_download_and_handoff(tmp_path, monkeypatch):
    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_GET(self):
            if self.path == "/file":
                self.send_response(200); self.send_header("Content-Disposition", 'attachment; filename="fixture.txt"'); self.end_headers(); self.wfile.write(b"Owned download evidence"); return
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b'<title>Owned transfer fixture</title><input type="file"><a href="/file">Download synthetic</a><p>Only synthetic</p>')
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr("smara.native_browser._is_public_http_url", lambda url: url.startswith(base + "/") or url == base)
    state = {"paused": False, "stopped": False}
    control = SimpleNamespace(id="synthetic", state=lambda: state, update=lambda **values: state.update(values))
    browser = NativeBrowser(tmp_path, [base], {"transfers": True}, control)
    try:
        first = browser.call("browser_open", {"url": base + "/"})
        image = browser.call("browser_snapshot", {})
        assert Path(image["path"]).read_bytes().startswith(b"\x89PNG") and not image["uploaded"]
        current = browser.call("browser_observe", {})
        ref = next(ref for ref, entry in current["elements"].items() if entry["tag"] == "input")
        (tmp_path / "note.txt").write_text("Owned synthetic upload")
        current = browser.call("browser_upload", {"observation_id": current["observation_id"], "ref": ref, "path": "note.txt"})
        page = browser.engine.page(browser.engine.sessions[browser.session])
        assert page.locator("input").evaluate("el=>el.files[0].name") == "note.txt"
        ref = next(ref for ref, entry in current["elements"].items() if entry["tag"] == "a")
        result = browser.call("browser_download", {"observation_id": current["observation_id"], "ref": ref})
        assert Path(result["path"]).read_text() == "Owned download evidence" and not result["executed"]
        assert len(browser.call("browser_tabs", {})["tabs"]) == 1
        browser.options["visible"] = True  # Policy test; no private browser/login.
        assert browser.call("browser_handoff", {})["paused"]
        with pytest.raises(ValueError, match="human"):
            browser.call("browser_observe", {})
        state["paused"] = False  # Synthetic user-only control.
        assert browser.call("browser_observe", {})["title"] == first["title"]
        state["stopped"] = True
        with pytest.raises(ValueError, match="stopped"): browser.call("browser_observe", {})
    finally:
        browser.engine.shutdown(); server.shutdown(); server.server_close()
