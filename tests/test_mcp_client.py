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
