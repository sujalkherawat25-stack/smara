import json
from pathlib import Path
import pytest

from smara.code_graph import CodePropertyGraph
from smara.tool_registry import GraphInspectSymbolTool, GraphBlastRadiusTool, GraphFindReferencesTool, ToolContext


@pytest.fixture
def sample_workspace(tmp_path: Path) -> Path:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    # Create module A: auth.py
    (src_dir / "auth.py").write_text(
        '''"""Authentication module."""
def hash_password(password: str) -> str:
    """Hash a password."""
    return f"hashed_{password}"

def verify_token(token: str) -> bool:
    """Verify security token."""
    return token.startswith("valid_")

class AuthManager:
    """Manages user authentication sessions."""
    def login(self, username: str, token: str) -> bool:
        return verify_token(token)
''',
        encoding="utf-8",
    )

    # Create module B: api.py (depends on auth.py)
    (src_dir / "api.py").write_text(
        '''from .auth import AuthManager, verify_token

def handle_request(path: str, token: str) -> str:
    """Handle incoming API request."""
    if not verify_token(token):
        return "unauthorized"
    manager = AuthManager()
    return "ok"
''',
        encoding="utf-8",
    )

    # Create test module: test_auth.py
    (tests_dir / "test_auth.py").write_text(
        '''from src.auth import verify_token, hash_password

def test_verify_token():
    assert verify_token("valid_123") is True
    assert verify_token("bad") is False
''',
        encoding="utf-8",
    )

    return tmp_path


def test_code_graph_indexes_symbols(sample_workspace: Path):
    graph = CodePropertyGraph(sample_workspace)
    count = graph.index()
    assert count >= 4

    # Test inspect symbol
    token_info = graph.inspect_symbol("verify_token")
    assert token_info is not None
    assert token_info["name"] == "verify_token"
    assert token_info["kind"] == "function"
    assert "token" in token_info["parameters"]
    assert "Verify security token" in token_info["docstring"]
    assert "handle_request" in token_info["called_by"] or "test_verify_token" in token_info["called_by"] or "AuthManager.login" in token_info["called_by"]


def test_code_graph_inspect_class_method(sample_workspace: Path):
    graph = CodePropertyGraph(sample_workspace)
    graph.index()

    method_info = graph.inspect_symbol("AuthManager.login")
    assert method_info is not None
    assert method_info["kind"] == "method"
    assert "username" in method_info["parameters"]


def test_code_graph_blast_radius(sample_workspace: Path):
    graph = CodePropertyGraph(sample_workspace)
    graph.index()

    # Blast radius of verify_token symbol
    radius = graph.blast_radius("verify_token")
    assert radius["target"] == "verify_token"
    assert any("auth.py" in f for f in radius["impacted_files"])
    assert radius["associated_tests"] or radius["impacted_callers"]


def test_code_graph_find_references(sample_workspace: Path):
    graph = CodePropertyGraph(sample_workspace)
    graph.index()

    refs = graph.find_references("verify_token")
    assert len(refs) >= 1
    assert any(r["kind"] == "definition" for r in refs)


@pytest.mark.anyio
async def test_graph_tools_invocation(sample_workspace: Path):
    ctx = ToolContext(account_id="acct_1", workspace_id="ws_1")

    # 1. GraphInspectSymbolTool
    inspect_tool = GraphInspectSymbolTool(sample_workspace)
    res1 = await inspect_tool.run({"symbol": "verify_token"}, ctx)
    assert res1.ok is True
    data1 = json.loads(res1.content)
    assert data1["name"] == "verify_token"

    # 2. GraphBlastRadiusTool
    blast_tool = GraphBlastRadiusTool(sample_workspace)
    res2 = await blast_tool.run({"target": "verify_token"}, ctx)
    assert res2.ok is True
    data2 = json.loads(res2.content)
    assert "impacted_files" in data2

    # 3. GraphFindReferencesTool
    ref_tool = GraphFindReferencesTool(sample_workspace)
    res3 = await ref_tool.run({"symbol": "verify_token"}, ctx)
    assert res3.ok is True
    data3 = json.loads(res3.content)
    assert isinstance(data3, list)
