import asyncio
import json

import pytest

from smara.tool_registry import ToolContext, ToolError, default_tool_registry
from smara.workflow import validate_workflow, workflow_summary


def _stages():
    return [
        {"stage": "inspect", "capability": "local_file_read", "payload": {"operation": "list_tree", "path": "workspace"}},
        {"stage": "plan", "capability": "local_file_read", "payload": {"operation": "read_file", "path": "workspace/plan.md"}},
        {"stage": "edit", "capability": "local_file_write", "payload": {"operation": "patch", "path": "workspace/plan.md", "find": "draft", "replace": "ready"}},
        {"stage": "run", "capability": "local_terminal", "payload": {"recipe": "python.compile", "cwd": "workspace"}},
        {"stage": "verify", "capability": "local_terminal", "payload": {"recipe": "git.diff-check", "cwd": "workspace"}},
        {"stage": "report", "capability": "local_file_read", "payload": {"operation": "git_summary", "path": "workspace"}},
    ]


def test_workflow_normalizes_sequential_dependencies_without_secrets():
    stages = validate_workflow(_stages())
    assert [item["depends_on"] for item in stages] == [[], [0], [1], [2], [3], [4]]
    assert workflow_summary(stages) == {
        "stage_count": 6,
        "stages": [
            {"stage": "inspect", "capability": "local_file_read"},
            {"stage": "plan", "capability": "local_file_read"},
            {"stage": "edit", "capability": "local_file_write"},
            {"stage": "run", "capability": "local_terminal"},
            {"stage": "verify", "capability": "local_terminal"},
            {"stage": "report", "capability": "local_file_read"},
        ],
    }


@pytest.mark.parametrize("bad", [
    [{"stage": "run", "capability": "local_terminal", "payload": {}}, {"stage": "inspect", "capability": "local_file_read", "payload": {}}],
    [{"stage": "inspect", "capability": "unknown", "payload": {}}, {"stage": "report", "capability": "local_file_read", "payload": {}}],
    [{"stage": "inspect", "capability": "local_file_read", "payload": {"workflow": []}}, {"stage": "report", "capability": "local_file_read", "payload": {}}],
    [{"stage": "inspect", "capability": "local_integration", "payload": {"provider": "tavily", "api_key": "should-not-travel"}}, {"stage": "report", "capability": "local_file_read", "payload": {}}],
])
def test_workflow_rejects_bad_order_capability_and_nesting(bad):
    with pytest.raises(ValueError):
        validate_workflow(bad)


def test_workflow_tool_creates_one_approval_intent_with_stage_summary():
    captured = []

    def requester(preview, stages):
        captured.append((preview, stages))
        return {"task_id": "task_workflow", "status": "waiting_approval"}

    async def execute():
        result = await default_tool_registry(desktop_workflow_requester=requester).invoke(
            "desktop.request_workflow",
            {"preview": "Inspect, edit, and verify the approved workspace", "stages": _stages()},
            ToolContext("acct_test", "workspace", desktop_workflow_requester=requester),
        )
        data = json.loads(result.content)
        assert data["approval_required"] is True
        assert data["task_id"] == "task_workflow"
        assert data["workflow"]["stage_count"] == 6
        assert captured[0][1][2]["depends_on"] == [1]

    asyncio.run(execute())


def test_workflow_tool_reports_missing_host_callback():
    async def execute():
        registry = default_tool_registry(desktop_workflow_requester=lambda *_: {})
        with pytest.raises(ToolError, match="approved hosted task"):
            await registry.invoke(
                "desktop.request_workflow",
                {"preview": "Run workflow", "stages": _stages()},
                ToolContext("acct_test", "workspace"),
            )

    asyncio.run(execute())
