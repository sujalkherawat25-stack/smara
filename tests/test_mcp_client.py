"""Unit tests for Model Context Protocol (MCP) stdio client and tool dispatcher."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from smara.mcp_client import MCPManager, MCPServerProcess


def test_mcp_server_process_lifecycle(tmp_path: Path):
    server = MCPServerProcess(
        name="test_server",
        command="python",
        args=["-c", "import sys"],
        cwd=tmp_path,
    )
    assert server.name == "test_server"
    assert server._running is False

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()

    # Setup simulated JSON-RPC responses
    init_response = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mock-mcp", "version": "1.0"},
        },
    }) + "\n"

    list_tools_response = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "read_db",
                    "description": "Read SQL database",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                }
            ]
        },
    }) + "\n"

    mock_proc.stdout.readline.side_effect = [init_response, list_tools_response]

    with patch("subprocess.Popen", return_value=mock_proc):
        started = server.start()
        assert started is True
        assert len(server.tools) == 1
        assert server.tools[0]["name"] == "read_db"

    # Test call_tool
    call_response = json.dumps({
        "jsonrpc": "2.0",
        "id": 3,
        "result": {
            "content": [{"type": "text", "text": "Row count: 42"}],
            "isError": False,
        },
    }) + "\n"
    mock_proc.stdout.readline.side_effect = [call_response]

    res = server.call_tool("read_db", {"query": "SELECT * FROM users"})
    assert res["status"] == "ok"
    assert res["output"] == "Row count: 42"
    assert res["is_error"] is False

    server.stop()
    assert server._running is False


def test_mcp_manager_discovery_and_schema(tmp_path: Path):
    mcp_config = {
        "mcpServers": {
            "sqlite": {
                "command": "python",
                "args": ["-m", "sqlite_server"],
            }
        }
    }
    (tmp_path / "mcp.json").write_text(json.dumps(mcp_config), encoding="utf-8")

    manager = MCPManager(tmp_path)
    with patch.object(MCPServerProcess, "start", return_value=True):
        servers = manager.discover_and_load()
        assert "sqlite" in servers
        servers["sqlite"].tools = [
            {
                "name": "query",
                "description": "Execute query",
                "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}},
            }
        ]

        schemas = manager.get_tool_schemas()
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "mcp_sqlite_query"
        assert "[MCP: sqlite]" in schemas[0]["function"]["description"]


def test_mcp_manager_execution_routing(tmp_path: Path):
    manager = MCPManager(tmp_path)
    mock_server = MagicMock()
    mock_server.call_tool.return_value = {"status": "ok", "output": "Query results: 5 rows"}
    manager.servers["db"] = mock_server

    # Successful call
    out = manager.execute_mcp_tool("mcp_db_query", {"sql": "SELECT 1"})
    assert out == "Query results: 5 rows"
    mock_server.call_tool.assert_called_with("query", {"sql": "SELECT 1"})

    # Unknown server
    err_out = manager.execute_mcp_tool("mcp_unknown_query", {})
    assert "not connected" in err_out

    # Non-mcp prefix
    assert manager.execute_mcp_tool("local_exec", {}) is None

    manager.shutdown()
    assert len(manager.servers) == 0


def test_mcp_request_deadline_on_silent_server(tmp_path: Path):
    """A server that never emits a response must not block the caller."""
    import time

    server = MCPServerProcess(
        name="silent",
        command="python",
        args=["-c", "import time; time.sleep(2)"],
        cwd=tmp_path,
    )
    started = time.monotonic()
    assert server.start(timeout=0.05) is False
    assert time.monotonic() - started < 1.0
    server.stop()


def test_mcp_server_names_with_underscores_route_without_ambiguity(tmp_path: Path):
    manager = MCPManager(tmp_path)
    mock_server = MagicMock()
    mock_server.tools = [{"name": "query"}]
    mock_server.call_tool.return_value = {"status": "ok", "output": "ok"}
    manager.servers["alpha_beta"] = mock_server

    assert manager.get_tool_schemas()[0]["function"]["name"] == "mcp__alpha_beta__query"
    assert manager.execute_mcp_tool("mcp__alpha_beta__query", {}) == "ok"
    mock_server.call_tool.assert_called_once_with("query", {})


def test_mcp_configuration_is_not_started_in_untrusted_workspace(tmp_path: Path):
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"unsafe": {"command": "python"}}}),
        encoding="utf-8",
    )
    manager = MCPManager(tmp_path, trusted=False)
    with patch.object(MCPServerProcess, "start") as start:
        assert manager.discover_and_load() == {}
        start.assert_not_called()
