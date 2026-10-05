"""Web-only runs must not fingerprint unrelated local files to make progress."""
import json

import pytest

from smara import harness
from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import SessionEngine, ToolCall, ToolResult


def forbid_workspace_scan(_root):
    pytest.fail("Web research unexpectedly scanned the entire local workspace")


def test_web_checkpoint_and_finish_do_not_scan_workspace(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "web")
    try:
        session.set("tool_profile", "research_web")
        session.set("research_required", True)
        session.begin_incremental("Explain a historical development")
        monkeypatch.setattr(harness, "workspace_revision", forbid_workspace_scan)
        session.checkpoint([], {"phase": "model"})
        checkpoint = json.loads(session.resolve_artifact(session.get("continuation_artifact_id")))
        assert checkpoint["workspace_revision"] == "web-only:no-workspace-files"
        # Removing an irrelevant file scan must not relax research evidence gates.
        result = session.finish_incremental("completed", "An unsupported answer")
        assert result["status"] == "needs_input"
    finally:
        session.close()


def test_web_tools_do_not_scan_workspace(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "tools")
    try:
        session.set("tool_profile", "research_web")
        session.begin_incremental("Find documentary evidence")
        monkeypatch.setattr(harness, "workspace_revision", forbid_workspace_scan)
        result = session.execute_incremental(
            ToolCall("search", "research_search", {"query": "database history"}, str(tmp_path)),
            lambda _: ToolResult("search", "ok", "A deterministic test observation"),
        )
        assert result.ok
        assert session.inspect()["calls"][0]["after_revision"] == "web-only:no-workspace-files"
    finally:
        session.close()


def test_coding_checkpoint_retains_full_revision_checks(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "coding")
    scans = []
    try:
        session.set("tool_profile", "coding")
        session.begin_incremental("Fix a real file")
        monkeypatch.setattr(harness, "workspace_revision", lambda root: scans.append(root) or "real-revision")
        session.checkpoint([], {"phase": "model"})
        assert scans == [tmp_path]
        checkpoint = json.loads(session.resolve_artifact(session.get("continuation_artifact_id")))
        assert checkpoint["workspace_revision"] == "real-revision"
    finally:
        session.close()


def test_non_web_action_forces_full_checks_even_in_web_session(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "mixed")
    scans = []
    try:
        session.set("tool_profile", "research_web")
        session.begin_incremental("A session with an unexpected local action")
        original = harness.workspace_revision
        monkeypatch.setattr(harness, "workspace_revision", lambda root: scans.append(root) or original(root))

        def write(_):
            (tmp_path / "changed.py").write_text("changed = True\n", encoding="utf-8")
            return ToolResult("write", "ok", "Wrote a real local file")

        assert session.execute_incremental(ToolCall("write", "write_file", {}, str(tmp_path)), write).ok
        assert scans
        assert session.finish_incremental("completed", "Changed the file")["status"] == "needs_input"
        assert session.current_workspace_revision() != "web-only:no-workspace-files"
    finally:
        session.close()


def test_agent_web_tool_receipts_use_the_session_revision_scope(tmp_path, monkeypatch):
    session = SessionEngine(tmp_path, "agent")
    try:
        agent = SmaraAutonomousAgent(api_key="unit-test", workspace_root=tmp_path,
                                     profile="research_web", session_engine=session)
        session.begin_incremental("Investigate a historical topic")
        monkeypatch.setattr(harness, "workspace_revision", forbid_workspace_scan)
        result = json.loads(agent.execute_tool("research_plan", {
            "question": "How did databases develop?",
            "nodes": [{"id": "history", "question": "How did databases develop?"}],
        }, "plan"))
        assert result["status"] == "ok"
        assert session.inspect()["calls"][0]["before_revision"] == "web-only:no-workspace-files"
    finally:
        session.close()
