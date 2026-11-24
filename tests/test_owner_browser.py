"""Smara in the owner's own browser: relay protocol, native messaging host, setup and runtime wiring.
The end-to-end path (Playwright -> relay -> extension -> a real Brave tab) is checked by hand with a throwaway profile."""
import asyncio
import base64
import hashlib
import io
import json
import struct
from pathlib import Path

import pytest

from smara import owner_browser as ob

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "desktop.json"))
    return tmp_path


def test_extension_id_is_fixed_by_the_manifest_key():
    manifest = json.loads((ROOT / "apps/desktop/src-tauri/resources/browser-extension/manifest.json").read_text(encoding="utf-8"))
    der = base64.b64decode(manifest["key"])
    assert "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32]) == ob.EXTENSION_ID
    assert {"debugger", "nativeMessaging", "tabs"} <= set(manifest["permissions"])
    assert "host_permissions" not in manifest and "content_scripts" not in manifest   # it reads no page by itself


def test_the_extension_refuses_tabs_it_did_not_open():
    script = (ROOT / "apps/desktop/src-tauri/resources/browser-extension/background.js").read_text(encoding="utf-8")
    assert 'owner.get(message.tabId) !== id) throw new Error("Smara may only use the tabs it opened")' in script
    assert 'owner.get(message.tabId) !== id) throw new Error("Smara may only close the tabs it opened")' in script


def run(coro):
    return asyncio.run(coro)


def test_relay_opens_tabs_through_the_extension_and_forwards_page_commands(state):
    async def scenario():
        from websockets.asyncio.client import connect
        relay = ob.Relay("Scout")
        endpoint = await relay.start()
        announced = json.loads(relay.path.read_text(encoding="utf-8"))
        assert announced["port"] == relay.port and announced["label"] == "Scout" and announced["token"] == relay.token
        seen = []

        async def extension():
            async with connect(f"ws://127.0.0.1:{relay.port}/extension/{relay.token}") as socket:
                async for raw in socket:
                    message = json.loads(raw)
                    seen.append(message)
                    if message["type"] == "open":
                        await socket.send(json.dumps({"type": "reply", "id": message["id"], "result": {
                            "tabId": 7, "targetInfo": {"targetId": "T7", "type": "page", "title": "", "url": "about:blank"}}}))
                    elif message["type"] == "cdp":
                        await socket.send(json.dumps({"type": "reply", "id": message["id"], "result": {"echo": message["method"]}}))
                        await socket.send(json.dumps({"type": "event", "tabId": 7, "method": "Page.loadEventFired", "params": {}}))
                    elif message["type"] == "release":
                        return

        task = asyncio.ensure_future(extension())
        assert await relay.wait_for_extension(5)
        async with connect(endpoint) as pw:
            await pw.send(json.dumps({"id": 1, "method": "Browser.getVersion"}))
            assert json.loads(await pw.recv())["result"]["protocolVersion"] == "1.3"
            await pw.send(json.dumps({"id": 2, "method": "Target.createTarget", "params": {"url": "about:blank"}}))
            attached = json.loads(await pw.recv())
            assert attached["method"] == "Target.attachedToTarget" and attached["params"]["targetInfo"]["targetId"] == "T7"
            session = attached["params"]["sessionId"]
            assert json.loads(await pw.recv()) == {"id": 2, "result": {"targetId": "T7"}}
            await pw.send(json.dumps({"id": 3, "sessionId": session, "method": "Page.navigate", "params": {"url": "https://example.com"}}))
            replies = [json.loads(await pw.recv()) for _ in range(2)]
            assert {"id": 3, "sessionId": session, "result": {"echo": "Page.navigate"}} in replies
            assert {"method": "Page.loadEventFired", "params": {}, "sessionId": session} in replies
            await pw.send(json.dumps({"id": 4, "sessionId": "someone-elses-tab", "method": "Runtime.evaluate", "params": {}}))
            assert "closed" in json.loads(await pw.recv())["error"]["message"]
        forwarded = next(m for m in seen if m["type"] == "cdp")
        assert forwarded["tabId"] == 7 and forwarded["method"] == "Page.navigate" and forwarded["sessionId"] is None
        await relay.close()
        await asyncio.wait_for(task, 5)
        assert not relay.path.exists()

    run(scenario())


def test_a_second_browser_with_the_extension_cannot_take_over_a_task(state):
    async def scenario():
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed
        relay = ob.Relay()
        await relay.start()
        first = await connect(f"ws://127.0.0.1:{relay.port}/extension/{relay.token}")
        assert await relay.wait_for_extension(5)
        second = await connect(f"ws://127.0.0.1:{relay.port}/extension/{relay.token}")
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(second.recv(), 5)
        assert second.close_code == 1013
        wrong = await connect(f"ws://127.0.0.1:{relay.port}/extension/not-the-token")
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(wrong.recv(), 5)
        await first.close()
        await relay.close()

    run(scenario())


def frame(value):
    data = json.dumps(value).encode()
    return struct.pack("<I", len(data)) + data


def test_native_host_lists_live_relays_and_writes_a_heartbeat(state):
    import os
    folder = ob.relays_dir()
    folder.mkdir(parents=True)
    (folder / "a.json").write_text(json.dumps({"id": "a", "port": 1234, "token": "t", "pid": os.getpid(), "label": "Kiran"}), encoding="utf-8")
    (folder / "b.json").write_text(json.dumps({"id": "b", "port": 1, "token": "x", "pid": 2 ** 31 - 2, "label": "gone"}), encoding="utf-8")
    out = io.BytesIO()
    assert ob.host(io.BytesIO(frame({"type": "hello", "browser": "Brave", "version": "1.0.0"})), out) == 0
    raw = out.getvalue()
    (length,) = struct.unpack("<I", raw[:4])
    message = json.loads(raw[4:4 + length])
    assert message == {"type": "relays", "relays": [{"id": "a", "port": 1234, "token": "t", "label": "Kiran"}]}
    assert json.loads(ob.heartbeat_path().read_text(encoding="utf-8"))["seen"] > 0


def test_register_lets_only_the_smara_extension_start_the_host(state):
    result = ob.register()
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["name"] == ob.HOST_NAME and manifest["type"] == "stdio"
    assert manifest["allowed_origins"] == [f"chrome-extension://{ob.EXTENSION_ID}/"]
    assert Path(manifest["path"]).exists()


def test_the_browser_starts_the_host_through_the_frozen_entry(monkeypatch):
    from smara import native_management
    monkeypatch.setattr(ob, "host", lambda: 42)
    assert native_management.main([f"chrome-extension://{ob.EXTENSION_ID}/", "--parent-window=0"]) == 42
    assert native_management.main(["--native-browser-host"]) == 42


def test_settings_save_needs_an_installed_browser_and_turns_owner_mode_on(state, monkeypatch):
    monkeypatch.setattr(ob, "installed_browsers", lambda: {"brave": r"C:\Brave\brave.exe"})
    monkeypatch.setattr(ob, "register", lambda: {})
    assert ob.manage({"action": "status"})["use"] is False
    with pytest.raises(ValueError, match="installed"):
        ob.manage({"action": "save", "browser": "chrome", "use": True})
    with pytest.raises(ValueError, match="whether"):
        ob.manage({"action": "save", "browser": "brave", "use": "yes"})
    saved = ob.manage({"action": "save", "browser": "brave", "use": True})
    assert saved["use"] is True and saved["path"] == r"C:\Brave\brave.exe" and saved["connected"] is False
    with pytest.raises(ValueError, match="Unsupported"):
        ob.manage({"action": "delete_everything"})


def test_runtime_hands_the_browser_choice_to_the_browser_helper(state, tmp_path, monkeypatch):
    from smara.native_provider import ChatEndpoint, ResponsesAdapter
    from smara.native_runtime import launch_options
    monkeypatch.setattr(ob, "choice", lambda: {"use": True, "browser": "brave", "path": r"C:\Brave\brave.exe"})
    workspace = tmp_path / "work"
    workspace.mkdir()
    adapter = ResponsesAdapter(ChatEndpoint("http://127.0.0.1", "custom", ""))
    try:
        args, _ = launch_options(adapter, home=tmp_path / "home", workspace=workspace, browser_origins=["*"], agent_label="Scout")
    finally:
        adapter.server.server_close()
        adapter.client.close()
    joined = "\n".join(args)
    assert 'mcp_servers.smara_browser.env.SMARA_BROWSER_MODE="owner"' in joined
    assert "SMARA_OWNER_BROWSER" in joined and 'SMARA_BROWSER_LABEL="Scout"' in joined
    assert "mcp_servers.smara_browser.tool_timeout_sec=90" in joined


def test_smara_browser_window_is_shown_by_default():
    from smara.native_browser_control import DEFAULTS
    assert DEFAULTS["visible"] is True
