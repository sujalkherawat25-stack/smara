import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest

from smara.harness import Budget, SessionEngine
from smara.subagent_orchestrator import DelegationResult, SubagentRole, SubagentWorker, SubagentOrchestrator
from smara.subagent_worktree import create_subagent_worktree, inspect_subagent_worktree, cleanup_subagent_worktree


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def repository(root):
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Isolated test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text("from calculator import add\ndef test_add():\n    assert add(2, 3) == 5\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")


def test_complete_patch_includes_new_files_and_parent_is_unchanged(tmp_path):
    repository(tmp_path)
    baseline = git(tmp_path, "status", "--porcelain")
    snapshot = create_subagent_worktree(tmp_path, "unit_snapshot")
    assert snapshot and (Path(snapshot["path"]) / ".git").is_dir()
    root = Path(snapshot["path"])
    try:
        (root / "added.py").write_text("value = 3\n")
        inspection = inspect_subagent_worktree(snapshot)
        assert inspection["has_changes"] and "+++ b/added.py" in inspection["diff"]
        assert git(tmp_path, "status", "--porcelain") in {baseline, "?? .worktrees/"}
        assert not (tmp_path / "added.py").exists()
        assert not cleanup_subagent_worktree(snapshot)
    finally:
        assert cleanup_subagent_worktree(snapshot, force=True)


def test_tester_runs_real_tests_on_exact_patch_and_cannot_edit(tmp_path, monkeypatch):
    from smara.sandbox import docker_engine_status
    if not docker_engine_status().get("engine_available"):
        pytest.skip("Docker is required for the actual verification command")
    repository(tmp_path)
    coder = create_subagent_worktree(tmp_path, "unit_coder")
    root = Path(coder["path"])
    (root / "calculator.py").write_text("def add(a, b):\n    return a + b\n")
    patch = inspect_subagent_worktree(coder)["diff"]
    from smara.autonomous_agent import SmaraAutonomousAgent
    observations = {}
    def verify(self, task):
        self.session_engine.begin_incremental(task)
        observations["root"] = self.workspace_root
        observations["code"] = (self.workspace_root / "calculator.py").read_text()
        observations["denied"] = self.execute_tool("python_execute", {"code": "from pathlib import Path; Path('calculator.py').write_text('bad')"})
        observations["test"] = self.execute_tool("terminal", {"command": "python3 -m pytest tests -q --basetemp=/tmp/checks"})
        return {"session": self.session_engine.finish_incremental("completed", "Focused test passed"), "answer": "Focused test passed"}
    monkeypatch.setattr(SmaraAutonomousAgent, "run", verify)
    worker = SubagentWorker("unit_tester", SubagentRole.TESTER, api_key="unit-fixture", workspace_root=tmp_path,
                            implementation_patch=patch, base_revision=coder["base_commit"])
    result = worker.run("Verify add")
    try:
        assert result.status == "SUCCESS", result.error
        assert result.reviewed_patch_sha256 == hashlib.sha256(patch.encode()).hexdigest()
        assert "return a + b" in observations["code"]
        assert "Read-only file system" in observations["denied"]
        assert "1 passed" in observations["test"]
        assert "return a - b" in (tmp_path / "calculator.py").read_text()
        assert result.worktree_path != str(root) and result.worktree_path != str(tmp_path)
    finally:
        cleanup_subagent_worktree(coder, force=True)
        cleanup_subagent_worktree({"path": result.worktree_path or str(tmp_path / ".worktrees/subagent-unit_tester"),
            "repo_root": str(tmp_path), "base_commit": coder["base_commit"], "kind": "standalone_snapshot"}, force=True)


def timed_receipt(worker, goal, context, output):
    started = time.monotonic()
    time.sleep(.7)
    output.put(DelegationResult(worker.task_id, goal, "SUCCESS",
        json.dumps({"start": started, "end": time.monotonic(), "padding": "x" * 200_000}),
        0, 700, [], usage={"billed_tokens": 1}).to_dict())


def test_batch_is_parallel_and_large_queue_results_do_not_deadlock(tmp_path, monkeypatch):
    import smara.subagent_orchestrator as module
    monkeypatch.setattr(module, "DELEGATION_ENABLED", True)
    monkeypatch.setattr(module, "_worker_process_entry", timed_receipt)
    root = SessionEngine(tmp_path, "batch", budget=Budget(30,20,6,100_000,2))
    root.begin_incremental("batch")
    try:
        results = SubagentOrchestrator(workspace_root=tmp_path, root_session=root).delegate_batch(
            [{"goal": "one", "max_iterations": 1}, {"goal": "two", "max_iterations": 1}], max_workers=2, timeout=12)
        assert [result.status for result in results] == ["SUCCESS", "SUCCESS"]
        assert len({result.task_id for result in results}) == 2
        first, second = [json.loads(result.summary) for result in results]
        assert max(first["start"], second["start"]) < min(first["end"], second["end"])
        assert root.get("usage")["billed_tokens"] == 2
        assert all(item["status"] == "reconciled" for item in root.get("child_reservations").values())
    finally:
        root.close()


def test_resume_preserves_budget_and_blocks_uncertain_mutation(tmp_path):
    session = SessionEngine(tmp_path, "continue", budget=Budget(60,20,4,1000,1))
    try:
        session.begin_incremental("Patch and test")
        session.checkpoint([{"role": "user", "content": "Patch and test"}], {"phase": "model"})
        session.set("usage", {"tool_calls": 3, "model_calls": 1, "billed_tokens": 700, "dollars": .2})
        original = session.get("budget")
        session.prepare_resume()
        assert session.get("budget") == original
        assert session.get("usage")["billed_tokens"] == 700
        session.db.execute("INSERT INTO calls VALUES('uncertain',0,'patch','{}',?,1,'admitted',NULL,NULL,NULL)", (str(tmp_path),))
        with pytest.raises(ValueError, match="uncertain"):
            session.prepare_resume()
    finally:
        session.close()


def test_worker_transport_failure_reaps_child_and_charges_reserved_budget(tmp_path, monkeypatch):
    import multiprocessing
    import smara.subagent_orchestrator as module
    monkeypatch.setattr(module, "DELEGATION_ENABLED", True)
    monkeypatch.setattr(module, "_worker_process_entry", timed_receipt)
    root = SessionEngine(tmp_path, "transport", budget=Budget(30,20,6,100_000,2))
    root.begin_incremental("transport")
    original_get = root.get
    before = {child.pid for child in multiprocessing.active_children()}
    cancel_reads = 0
    def fail_cancel_read(key, *args):
        nonlocal cancel_reads
        if key == "cancelled":
            cancel_reads += 1
            if cancel_reads > 1:
                raise RuntimeError("Injected transport failure")
        return original_get(key, *args)
    monkeypatch.setattr(root, "get", fail_cancel_read)
    try:
        result = SubagentOrchestrator(workspace_root=tmp_path, root_session=root).delegate("one", max_iterations=1, timeout=12)
        assert result.status == "FAILED" and result.error == "RuntimeError"
        assert {child.pid for child in multiprocessing.active_children()} <= before
        assert original_get("usage")["billed_tokens"] == 20_000
        assert all(item["status"] == "reconciled" for item in original_get("child_reservations").values())
    finally:
        root.close()
