"""Tests for Smara Task Planner and Compaction Retention."""
import json
import pytest
from smara.task_planner import SmaraTaskPlanner, TODO_INJECTION_HEADER
from smara.agent_tools import todo_tool
from smara.autonomous_agent import _compact_conversation_history


def test_planner_basic_lifecycle():
    planner = SmaraTaskPlanner()
    assert not planner.has_items()

    todos = [
        {"id": "1", "content": "Analyze architecture", "status": "completed"},
        {"id": "2", "content": "Implement planner", "status": "in_progress"},
        {"id": "3", "content": "Run tests", "status": "pending"},
    ]
    items = planner.write(todos)
    assert len(items) == 3
    assert planner.has_items()

    summary = planner.summary()
    assert summary["total"] == 3
    assert summary["completed"] == 1
    assert summary["in_progress"] == 1
    assert summary["pending"] == 1


def test_planner_merge_mode():
    planner = SmaraTaskPlanner()
    planner.write([
        {"id": "1", "content": "Initial step", "status": "in_progress"},
        {"id": "2", "content": "Second step", "status": "pending"},
    ])

    # Merge update: complete 1, add 3
    planner.write([
        {"id": "1", "status": "completed"},
        {"id": "3", "content": "Third step", "status": "pending"},
    ], merge=True)

    items = planner.read()
    assert len(items) == 3
    assert items[0]["status"] == "completed"
    assert items[0]["content"] == "Initial step"
    assert items[2]["id"] == "3"


def test_format_for_injection():
    planner = SmaraTaskPlanner()
    planner.write([
        {"id": "1", "content": "Completed phase", "status": "completed"},
        {"id": "2", "content": "Parent phase", "status": "in_progress"},
        {"id": "2a", "content": "Child active subtask", "status": "pending", "parent": "2"},
    ])

    injection = planner.format_for_injection()
    assert injection is not None
    assert TODO_INJECTION_HEADER in injection
    # Completed item 1 must NOT be injected
    assert "Completed phase" not in injection
    # Active parent and child must be injected
    assert "Parent phase" in injection
    assert "Child active subtask" in injection
    assert "[>]" in injection or "[ ]" in injection


def test_todo_tool_integration():
    planner = SmaraTaskPlanner()
    raw = todo_tool(
        todos=[{"id": "t1", "content": "Inspect code", "status": "in_progress"}],
        planner=planner,
    )
    data = json.loads(raw)
    assert "todos" in data
    assert data["summary"]["in_progress"] == 1

    # Read back without todos
    raw_read = todo_tool(planner=planner)
    data_read = json.loads(raw_read)
    assert len(data_read["todos"]) == 1


def test_compaction_todo_retention():
    planner = SmaraTaskPlanner()
    planner.write([
        {"id": "1", "content": "Pending verification", "status": "pending"},
    ])

    # Build conversation with 8 turns and > 35,000 chars to force middle compaction
    messages = [
        {"role": "system", "content": "System instructions."},
        {"role": "user", "content": "Solve complex problem."},
        {"role": "assistant", "content": "First thought..."},
        {"role": "tool", "content": "First observation..."},
        {"role": "assistant", "content": "Thinking step... " * 1000},
        {"role": "tool", "content": "Huge observation text... " * 1000},
        {"role": "assistant", "content": "Synthesizing..."},
        {"role": "user", "content": "Next turn."},
    ]

    compacted = _compact_conversation_history(messages, max_chars=1000, planner=planner)
    # Check that active todo injection header is present in compacted messages
    found = any(TODO_INJECTION_HEADER in str(m.get("content", "")) for m in compacted)
    assert found, "Compacted messages must preserve active task checklist."
