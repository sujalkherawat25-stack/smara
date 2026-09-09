"""Direct ReAct Loop Integration Tests for SmaraAutonomousAgent."""
import json
import tempfile
from pathlib import Path
import pytest
from smara.autonomous_agent import SmaraAutonomousAgent, get_tool_schemas


def test_toolset_profiles():
    full_tools = get_tool_schemas("full")
    coding_tools = get_tool_schemas("coding")
    research_tools = get_tool_schemas("research")

    full_names = {t["function"]["name"] for t in full_tools}
    coding_names = {t["function"]["name"] for t in coding_tools}
    research_names = {t["function"]["name"] for t in research_tools}

    # Verify coding profile includes core SWE tools
    assert {"terminal", "file_write", "patch", "python_execute", "file_read", "todo"}.issubset(coding_names)
    assert "audio_transcribe" not in coding_names

    # Verify research profile includes web & document tools
    assert {"browser_action", "research_plan", "research_search", "research_fetch", "research_resolve", "research_validate", "pdf_search", "todo"}.issubset(research_names)
    assert "web_search" not in research_names and "web_extract" not in research_names
    assert "patch" not in research_names


def test_agent_execute_tool_dispatch():
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = SmaraAutonomousAgent(api_key="mock_key", model="mock_model", workspace_root=tmpdir)

        # 1. Test todo tool dispatch through agent
        todo_res = agent.execute_tool("todo", {
            "todos": [{"id": "step1", "content": "Set up environment", "status": "in_progress"}]
        })
        todo_data = json.loads(todo_res)
        assert todo_data["summary"]["in_progress"] == 1
        assert agent.task_planner.has_items()

        # 2. Test file_write and patch tool dispatch
        fpath = Path(tmpdir) / "script.py"
        write_res = agent.execute_tool("file_write", {
            "path": str(fpath),
            "content": "MSG = 'initial_value'\n"
        })
        assert "successfully written" in write_res
        assert fpath.exists()

        patch_res = agent.execute_tool("patch", {
            "path": str(fpath),
            "old_string": "MSG = 'initial_value'",
            "new_string": "MSG = 'updated_value'"
        })
        assert "Patch applied successfully" in patch_res
        assert "MSG = 'updated_value'" in fpath.read_text(encoding="utf-8")

        # 3. Test terminal tool dispatch
        term_res = agent.execute_tool("terminal", {"command": "echo SmaraAgentTerminal"})
        assert "SmaraAgentTerminal" in term_res
        assert "[Exit Code: 0]" in term_res


def test_tool_admission_and_workspace_boundary_are_enforced(tmp_path: Path):
    agent = SmaraAutonomousAgent(api_key="mock_key", model="mock_model", profile="research", workspace_root=tmp_path)
    assert "Denied" in agent.execute_tool("patch", {"path": "nope.py", "old_string": "a", "new_string": "b"})
    assert "Denied" in agent.execute_tool("delegate_task", {"goal": "unsafe"})
    full_agent = SmaraAutonomousAgent(api_key="mock_key", model="mock_model", workspace_root=tmp_path)
    assert "outside the configured workspace" in full_agent.execute_tool(
        "file_write", {"path": str(tmp_path.parent / "canary.txt"), "content": "no"}
    )
    assert not (tmp_path.parent / "canary.txt").exists()


def test_unknown_tool_profile_is_rejected():
    assert get_tool_schemas("not-a-profile") == []
    with pytest.raises(ValueError, match="Unknown tool profile"):
        SmaraAutonomousAgent(profile="not-a-profile")
