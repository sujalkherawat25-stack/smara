import json

from smara.cli import _mcp_add_server, build_parser
from smara.mcp_client import MCPManager


def test_mcp_add_registers_stdio_server_and_forwards_flags(tmp_path):
    result = _mcp_add_server(
        tmp_path,
        "demo",
        "python",
        ["-m", "demo_server", "--stdio"],
        ["DEMO_MODE=1"],
        False,
    )

    config_path = tmp_path / ".mcp" / "servers.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert result["transport"] == "stdio"
    assert payload["mcpServers"]["demo"] == {
        "command": "python",
        "args": ["-m", "demo_server", "--stdio"],
        "env": {"DEMO_MODE": "1"},
    }


def test_mcp_manager_merges_supported_config_locations_with_root_precedence(tmp_path):
    hidden = tmp_path / ".mcp"
    hidden.mkdir()
    (hidden / "servers.json").write_text(
        json.dumps({"mcpServers": {"hidden": {"command": "hidden"}, "shared": {"command": "hidden"}}}),
        encoding="utf-8",
    )
    (tmp_path / "mcp.json").write_text(
        json.dumps({"mcpServers": {"root": {"url": "https://example.test/mcp"}, "shared": {"command": "root"}}}),
        encoding="utf-8",
    )

    configured = MCPManager(tmp_path).configured_servers()
    assert set(configured) == {"hidden", "root", "shared"}
    assert configured["shared"]["command"] == "root"


def test_cli_parser_exposes_mcp_and_test_fix_commands():
    parser = build_parser()

    mcp = parser.parse_args(["mcp", "add", "demo", "python", "-m", "demo_server"])
    fixer = parser.parse_args(["test-fix", "tests/test_mcp_client.py", "--max-iterations", "2"])

    assert (mcp.command, mcp.mcp_action, mcp.server_command, mcp.args) == ("mcp", "add", "python", ["-m", "demo_server"])
    assert (fixer.command, fixer.test_file, fixer.max_iterations) == ("test-fix", ["tests/test_mcp_client.py"], 2)
