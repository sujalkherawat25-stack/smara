import os
import sys
import threading
import time
from pathlib import Path

import pytest

from smara.harness import Budget, SessionBusy, SessionEngine, ToolBroker, ToolCall, ToolResult
from smara.autonomous_agent import SmaraAutonomousAgent


def call(root: Path, ident: str, name: str, arguments: dict) -> ToolCall:
    return ToolCall(ident, name, arguments, str(root.resolve()))


def test_schema_and_capability_fail_closed_without_side_effect(tmp_path: Path):
    broker = ToolBroker(tmp_path, {"write_file"})
    invalid = broker.dispatch(call(tmp_path, "a", "write_file", {"path": "x.txt"}))
    omitted = broker.dispatch(call(tmp_path, "b", "read_file", {"path": "x.txt"}))
    unknown = broker.dispatch(call(tmp_path, "c", "not_in_schema", {}))
    assert (invalid.status, omitted.status, unknown.status) == ("schema_error", "denied", "denied")
    assert not (tmp_path / "x.txt").exists()


def test_absolute_relative_and_symlink_escape_are_denied(tmp_path: Path):
    workspace = tmp_path / "workspace"; outside = tmp_path / "outside"
    workspace.mkdir(); outside.mkdir(); (outside / "secret.txt").write_text("secret")
    broker = ToolBroker(workspace, {"read_file", "write_file"})
    for raw in (str(outside / "secret.txt"), "../outside/secret.txt"):
        assert broker.dispatch(call(workspace, raw, "read_file", {"path": raw})).status == "denied"
    link = workspace / "link"
    try: link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt": pytest.skip("symlink creation is unavailable")
        import subprocess
        made = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True).returncode
        if made != 0: pytest.skip("junction creation is unavailable")
    assert broker.dispatch(call(workspace, "link", "read_file", {"path": "link/secret.txt"})).status == "denied"


def test_atomic_hash_guard_preserves_preexisting_user_edit(tmp_path: Path):
    target = tmp_path / "x.txt"; target.write_text("user edit")
    broker = ToolBroker(tmp_path, {"write_file"})
    result = broker.dispatch(call(tmp_path, "a", "write_file", {"path": "x.txt", "content": "lost", "expected_sha256": "0" * 64}))
    assert result.status == "denied"
    assert target.read_text() == "user edit"
    assert not list(tmp_path.glob(".x.txt.*"))


def test_failed_test_and_stale_test_evidence_never_complete(tmp_path: Path):
    (tmp_path / "x.py").write_text("x=1\n")
    failed = SessionEngine(tmp_path, "failed", budget=Budget(tool_calls=2), constrained=False).run("repair", [
        {"name": "patch_file", "arguments": {"path": "x.py", "old": "1", "new": "2"}},
        {"name": "run_process", "arguments": {"argv": [sys.executable, "-c", "raise SystemExit(1)"], "evidence_scope": "full"}},
    ])
    assert failed["status"] == "tool_error"

    (tmp_path / "y.py").write_text("y=1\n")
    stale = SessionEngine(tmp_path, "stale", budget=Budget(tool_calls=2), constrained=False).run("repair", [
        {"name": "run_process", "arguments": {"argv": [sys.executable, "-c", "raise SystemExit(0)"], "evidence_scope": "full"}},
        {"name": "patch_file", "arguments": {"path": "y.py", "old": "1", "new": "2"}},
    ])
    assert stale["status"] == "needs_input"
    assert "unverified" in stale["unresolved_items"][0]


def test_syntax_check_does_not_certify_changed_workspace(tmp_path: Path):
    result = SessionEngine(tmp_path, "syntax", budget=Budget(tool_calls=2), constrained=False).run("edit", [
        {"name": "write_file", "arguments": {"path": "x.py", "content": "x=1\n"}},
        {"name": "run_process", "arguments": {"argv": [sys.executable, "-m", "py_compile", "x.py"], "evidence_scope": "syntax"}},
    ])
    assert result["status"] == "needs_input"


def test_budget_after_edit_is_resumable_and_unverified(tmp_path: Path):
    engine = SessionEngine(tmp_path, "budget", budget=Budget(tool_calls=1))
    result = engine.run("edit and test", [
        {"name": "write_file", "arguments": {"path": "x.py", "content": "x=1\n"}},
        {"name": "read_file", "arguments": {"path": "x.py"}},
    ])
    assert result["status"] == "budget_exhausted"
    assert result["resume_token"] == "budget"
    assert engine.inspect()["calls"][1]["state"] == "pending"


def test_interrupted_mutation_is_not_replayed(tmp_path: Path):
    engine = SessionEngine(tmp_path, "interrupt")
    count = 0
    def crash(raw):
        nonlocal count
        count += 1
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        engine.run("mutate", [{"name": "agent_turn"}], crash)
    result = SessionEngine(tmp_path, "interrupt").resume(crash)
    assert result["status"] == "needs_input"
    assert count == 1


def test_two_writers_are_prevented(tmp_path: Path):
    entered = threading.Event(); release = threading.Event(); errors = []
    first = SessionEngine(tmp_path, "locked")
    def slow(raw):
        entered.set(); release.wait(5); return ToolResult(raw["call_id"], "ok")
    thread = threading.Thread(target=lambda: first.run("read", [{"name": "read_file"}], slow))
    thread.start(); assert entered.wait(2)
    try:
        with pytest.raises(SessionBusy): SessionEngine(tmp_path, "locked").resume()
    finally:
        release.set(); thread.join(5)


def test_process_cancellation_stops_delayed_canary(tmp_path: Path):
    broker = ToolBroker(tmp_path, {"process_start", "process_cancel"}, constrained=False)
    script = "import pathlib,time; time.sleep(2); pathlib.Path('canary').write_text('bad')"
    started = broker.dispatch(call(tmp_path, "start", "process_start", {"argv": [sys.executable, "-c", script], "timeout_seconds": 10}))
    assert started.ok
    process_id = started.meta["process_id"]
    cancelled = broker.dispatch(call(tmp_path, "cancel", "process_cancel", {"process_id": process_id}))
    assert cancelled.status == "cancelled"
    time.sleep(2.2)
    assert not (tmp_path / "canary").exists()


def test_durable_session_cancel_stops_owned_process_tree(tmp_path: Path):
    script = "import pathlib,time; time.sleep(2); pathlib.Path('late').write_text('bad')"
    engine = SessionEngine(tmp_path, "cancel-owned", budget=Budget(tool_calls=1), constrained=False)
    started = engine.run("start", [{"name": "process_start", "arguments": {"argv": [sys.executable, "-c", script], "timeout_seconds": 10}}])
    assert started["status"] == "completed"
    SessionEngine(tmp_path, "cancel-owned", constrained=False).cancel()
    time.sleep(2.2)
    assert not (tmp_path / "late").exists()


def test_concurrent_workspaces_keep_cwd_and_files_isolated(tmp_path: Path):
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots: root.mkdir()
    results = {}
    def worker(root):
        broker = ToolBroker(root, {"run_process"}, constrained=False)
        results[root.name] = broker.dispatch(call(root, root.name, "run_process", {"argv": [sys.executable, "-c", "import pathlib;pathlib.Path('canary').write_text(pathlib.Path.cwd().name)"], "evidence_scope": "none"}))
    threads = [threading.Thread(target=worker, args=(root,)) for root in roots]
    [thread.start() for thread in threads]; [thread.join() for thread in threads]
    assert (roots[0] / "canary").read_text() == "a"
    assert (roots[1] / "canary").read_text() == "b"
    assert all(result.ok for result in results.values())


def test_constrained_mode_denies_host_terminal(tmp_path: Path):
    broker = ToolBroker(tmp_path, {"run_process"}, constrained=True)
    result = broker.dispatch(call(tmp_path, "x", "run_process", {"argv": [sys.executable, "-c", "open('bad','w').write('x')"]}))
    assert result.status == "denied"
    assert not (tmp_path / "bad").exists()


def test_provider_401_is_not_retried(tmp_path: Path, monkeypatch):
    import io
    import urllib.error
    calls = []
    def denied(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError("https://provider", 401, "bad key", {}, io.BytesIO(b"invalid"))
    monkeypatch.setattr("urllib.request.urlopen", denied)
    agent = SmaraAutonomousAgent(api_key="bad", workspace_root=tmp_path)
    with pytest.raises(RuntimeError, match="401"):
        agent._call_model_api([{"role": "user", "content": "hi"}])
    assert len(calls) == 1


def test_provider_429_honors_retry_after_then_succeeds(tmp_path: Path, monkeypatch):
    import io
    import urllib.error
    sleeps = []; calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def read(self): return b'{"choices": []}'
    def limited(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError("https://provider", 429, "limited", {"Retry-After": "0.25"}, io.BytesIO(b"limited"))
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", limited)
    monkeypatch.setattr("smara.autonomous_agent.time.sleep", sleeps.append)
    agent = SmaraAutonomousAgent(api_key="x", workspace_root=tmp_path)
    assert agent._call_model_api([{"role": "user", "content": "hi"}]) == {"choices": []}
    assert calls == [1, 1]
    assert sleeps == [0.25]
