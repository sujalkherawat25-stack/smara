import pytest
from pathlib import Path
import sys

sys.path.insert(0, "src")
from smara.subagent_orchestrator import (
    DELEGATE_BLOCKED_TOOLS,
    SubagentRole,
    DelegationResult,
    SubagentWorker,
    SubagentOrchestrator
)


def test_subagent_blocked_tools():
    assert "delegate_task" in DELEGATE_BLOCKED_TOOLS
    assert "memory" in DELEGATE_BLOCKED_TOOLS
    assert "clarify" in DELEGATE_BLOCKED_TOOLS


def test_subagent_roles_and_results():
    assert SubagentRole.CODER.value == "coder"
    assert SubagentRole.RESEARCHER.value == "researcher"

    res = DelegationResult(
        task_id="sub_test_123",
        goal="Extract data from logs",
        status="SUCCESS",
        summary="Found 14 warning entries.",
        trace_steps=2,
        duration_ms=450,
        tools_used=["file_read"]
    )
    d = res.to_dict()
    assert d["status"] == "SUCCESS"
    assert d["duration_ms"] == 450
    assert d["tools_used"] == ["file_read"]


def test_orchestrator_instantiation():
    orchestrator = SubagentOrchestrator(api_key="mock_key")
    assert orchestrator.default_model == "glm5.2"


if __name__ == "__main__":
    test_subagent_blocked_tools()
    test_subagent_roles_and_results()
    test_orchestrator_instantiation()
    print("All subagent_orchestrator tests passed successfully!")
