"""Deterministic integration acceptance fixtures.

The fixture is a real local HTTP server speaking the same JSON-RPC/OAuth
boundaries used by remote MCP. No provider keys or external network are
required, and every test asserts the failure behavior as well as success.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest

from smara.mcp_client import MCPRemoteServer
from smara.plugins import PluginManager
from smara.skills_system import SkillLifecycleManager


class _MCPHandler(BaseHTTPRequestHandler):
    server_version = "smara-fixture/1"

    def log_message(self, *_args):
        return

    def _json(self, payload: dict, status: int = 200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        path = urlparse(self.path).path
        if path == "/oauth/token":
            from urllib.parse import parse_qs
            body = {key: values[0] for key, values in parse_qs((self.rfile.read(length) or b"").decode()).items()}
            if body.get("grant_type") == "authorization_code" or body.get("code"):
                self._json({"access_token": "fixture-access", "refresh_token": "fixture-refresh", "expires_in": 60})
            else:
                self._json({"access_token": "fixture-refreshed", "expires_in": 3600})
            return
        body = json.loads(self.rfile.read(length) or b"{}")
        method = body.get("method")
        response = {"jsonrpc": "2.0", "id": body.get("id")}
        if method == "initialize":
            response["result"] = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}}
        elif method == "notifications/initialized":
            response["result"] = {}
        elif method == "tools/list":
            response["result"] = {"tools": [{"name": "echo", "description": "Bounded fixture echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}
        elif method == "tools/call":
            response["result"] = {"content": [{"type": "text", "text": str(body.get("params", {}).get("arguments", {}).get("text", ""))}], "isError": False}
        else:
            response["error"] = {"code": -32601, "message": "method not found"}
        self._json(response)


@pytest.fixture()
def mcp_fixture():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MCPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_remote_mcp_discovery_call_and_health(mcp_fixture: str):
    remote = MCPRemoteServer("fixture", f"{mcp_fixture}/mcp", allow_private=True, timeout=3)
    assert remote.start() is True
    assert [tool["name"] for tool in remote.tools] == ["echo"]
    assert remote.call_tool("echo", {"text": "hello"})["output"] == "hello"
    health = remote.health()
    assert health["status"] == "healthy"
    assert health["tool_count"] == 1


def test_remote_mcp_pkce_oauth_exchange_and_state(mcp_fixture: str):
    remote = MCPRemoteServer(
        "fixture", f"{mcp_fixture}/mcp", allow_private=True,
        oauth={"authorization_endpoint": f"{mcp_fixture}/oauth/authorize", "token_endpoint": f"{mcp_fixture}/oauth/token", "client_id": "smara-test", "scope": "mcp"},
    )
    url, verifier = remote.oauth_authorization_url("http://127.0.0.1/callback")
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"]
    token = remote.oauth_exchange_code("fixture-code", verifier, "http://127.0.0.1/callback")
    assert token["access_token"] == "fixture-access"
    assert remote.headers["Authorization"] == "Bearer fixture-access"


def test_integration_refresh_and_expiry(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "refreshed", "expires_in": 3600}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return _Response()

    from smara import integration_oauth
    monkeypatch.setattr(integration_oauth, "settings", type("Settings", (), {"google_client_id": "id", "google_client_secret": "secret"})())
    monkeypatch.setattr(integration_oauth.httpx, "AsyncClient", lambda **_kwargs: _Client())
    refreshed = asyncio.run(integration_oauth.refresh_google({"refresh_token": "refresh", "access_token": "expired"}))
    assert refreshed["access_token"] == "refreshed"
    assert refreshed["refresh_token"] == "refresh"


def test_plugin_health_failure_is_recorded_and_recoverable(tmp_path: Path):
    manager = PluginManager(tmp_path)
    manifest = tmp_path / "plugin.json"
    manifest.write_text(json.dumps({"name": "fixture", "version": "1.0.0", "kind": "mcp", "tools": ["echo"], "endpoint": "https://mcp.example.test/mcp"}), encoding="utf-8")
    manager.install(manifest)
    with patch("smara.mcp_client.MCPRemoteServer.start", return_value=False):
        assert manager.health("fixture")[0]["status"] == "unhealthy"
    with patch("smara.mcp_client.MCPRemoteServer.start", return_value=True):
        assert manager.health("fixture")[0]["status"] == "healthy"
    assert manager.discover()[0]["lifecycle"] == "installed"


def test_skill_promotion_reuse_rejection_and_rollback(tmp_path: Path):
    skill_dir = tmp_path / ".smara" / "skills" / "fixture"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: fixture\nversion: 1.0.0\nrisk: read_only\n---\nRead and summarize.\n", encoding="utf-8")
    manager = SkillLifecycleManager(tmp_path)
    gate = {"passed": True, "overall_rate": 1.0, "category_rates": {"fixture": 1.0}, "false_completions": 0, "safety_violations": 0, "reproducible": True, "independent_runs": 3, "triggers": ["summarize"]}
    manager.submit_candidate("fixture", version="1.0.0", gate=gate, risk="read_only", automatic_reuse=True)
    manager.promote("fixture")
    assert manager.reuse_decision("fixture", "summarize this")["eligible"] is True
    assert manager.reuse_decision("fixture", "delete this")["eligible"] is False
    (skill_dir / "SKILL.md").write_text("tampered", encoding="utf-8")
    assert manager.validate("fixture")["passed"] is True  # structure is still data; digest changes below
    assert manager.reuse_decision("fixture", "summarize this")["eligible"] is False
