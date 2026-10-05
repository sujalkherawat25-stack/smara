import io
import json
import threading
import subprocess
import sys
import time

import pytest

from smara.app_server import serve
from smara.runtime_session import SQLiteRuntimeSessionStore
from smara.session_protocol import SessionProtocol


def wait_for(check):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Expected protocol state did not arrive")


def setup_turn(tmp_path, mode="ask"):
    store = SQLiteRuntimeSessionStore(tmp_path / "runtime.sqlite3", max_events=32)
    store.create_or_get("chat", request="Create a note")
    protocol = SessionProtocol(store)
    turn = protocol.begin("chat", "Create a note", approval_mode=mode)
    store.checkpoint("chat", status="running")
    return store, protocol, turn


@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_exact_action_approval_survives_client_restart(tmp_path, decision):
    store, protocol, turn = setup_turn(tmp_path)
    output = tmp_path / "note.txt"
    errors = []
    def execute():
        try:
            turn.execute("file_write", {"path": str(output), "content": "approved"},
                         lambda: output.write_text("approved", encoding="utf-8"))
        except RuntimeError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=execute)
    worker.start()
    try:
        approval = wait_for(lambda: protocol.snapshot("chat")["pending_approvals"])[0]
        assert not output.exists()
        # The protocol receipt commits before the legacy envelope checkpoint.
        assert wait_for(lambda: store.get("chat").status == "waiting_approval")
        reconnected = SessionProtocol(SQLiteRuntimeSessionStore(store.path))
        with pytest.raises(ValueError, match="does not match"):
            reconnected.respond("chat", approval["approval_id"], turn.turn_id, "substituted hash", "allow")
        reconnected.respond("chat", approval["approval_id"], turn.turn_id, approval["action_sha256"], decision)
        worker.join(4)
        assert not worker.is_alive()
        assert output.exists() is (decision == "allow")
        assert bool(errors) is (decision == "deny")
        # Duplicate same decisions are idempotent; opposite decisions cannot replace them.
        reconnected.respond("chat", approval["approval_id"], turn.turn_id, approval["action_sha256"], decision)
        with pytest.raises(ValueError, match="different decision"):
            reconnected.respond("chat", approval["approval_id"], turn.turn_id, approval["action_sha256"], "deny" if decision == "allow" else "allow")
    finally:
        protocol.interrupt("chat")
        worker.join(4)


def test_cancelled_action_and_stale_worker_cannot_affect_new_turn(tmp_path):
    store, protocol, first = setup_turn(tmp_path)
    output = tmp_path / "cancelled.txt"
    errors = []
    def execute():
        try:
            first.execute("file_write", {"path": str(output)}, lambda: output.touch())
        except RuntimeError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=execute)
    worker.start()
    approval = wait_for(lambda: protocol.snapshot("chat")["pending_approvals"])[0]
    protocol.interrupt("chat")
    second = protocol.begin("chat", "Next request", approval_mode="auto")
    store.start_turn("chat", request="Next request")
    store.checkpoint("chat", status="running", expected_turn_id=second.turn_id)
    with pytest.raises(ValueError, match="no longer active"):
        protocol.respond("chat", approval["approval_id"], first.turn_id, approval["action_sha256"], "allow")
    worker.join(4)
    assert not worker.is_alive() and errors and not output.exists()
    store.checkpoint("chat", status="completed", result={"answer": "stale"}, expected_turn_id=first.turn_id)
    assert store.get("chat").status == "running"
    assert store.get("chat").result == {}
    second.finish("completed", "new answer")
    assert protocol.snapshot("chat")["turns"][0]["turn_id"] == second.turn_id


def test_replay_keeps_long_input_and_all_items_after_legacy_retention(tmp_path):
    store, protocol, turn = setup_turn(tmp_path, "auto")
    with pytest.raises(ValueError, match="active turn"):
        protocol.begin("chat", "Concurrent turn")
    for index in range(40):
        turn.execute("file_read", {"index": index}, lambda: "content")
        store.append_event("chat", "legacy.progress", {"index": index})
    turn.finish("completed", "answer " * 12000)
    fresh = SessionProtocol(SQLiteRuntimeSessionStore(store.path))
    cursor, events = 0, []
    while True:
        page = fresh.snapshot("chat", after=cursor, limit=7)
        events.extend(page["events"])
        cursor = page["cursor"]
        if not page["has_more"]:
            break
    assert len({event["sequence"] for event in events}) == len(events)
    assert sum(event["kind"] == "item.started" for event in events) == 42
    assert events[-1]["kind"] == "turn.completed"
    assert fresh.snapshot("chat", after=cursor)["events"] == []
    assert fresh.snapshot("chat")["items"][-1]["payload"]["text"] == "answer " * 12000
    with pytest.raises(ValueError, match="ahead"):
        fresh.snapshot("chat", after=cursor + 1)


@pytest.mark.parametrize("status,expected", [("completed", "completed"), ("budget_exhausted", "failed")])
def test_stdio_server_accepts_approval_while_real_file_action_is_paused(tmp_path, status, expected):
    output = io.StringIO()
    target = tmp_path / "server-note.txt"
    def frames():
        return [json.loads(line) for line in output.getvalue().splitlines()]
    def requests():
        yield json.dumps({"id": 0, "method": "thread/list"}) + "\n"
        yield json.dumps({"id": 1, "method": "initialize"}) + "\n"
        yield json.dumps({"id": 2, "method": "thread/create", "params": {"thread_id": "server-chat"}}) + "\n"
        yield json.dumps({"id": 3, "method": "turn/start", "params": {"thread_id": "server-chat", "request": "Write note", "approval_mode": "ask"}}) + "\n"
        requested = wait_for(lambda: next((frame["params"] for frame in frames() if frame.get("method") == "session/event" and frame["params"]["kind"] == "approval.requested"), None))
        assert not target.exists()
        yield json.dumps({"id": 4, "method": "approval/respond", "params": {"thread_id": "server-chat", **requested["payload"], "decision": "allow"}}) + "\n"
        wait_for(lambda: any(frame.get("method") == "session/event" and frame["params"]["kind"] == "turn.completed" for frame in frames()))
        yield json.dumps({"id": 5, "method": "thread/read", "params": {"thread_id": "server-chat"}}) + "\n"
    def runner(_request, _root, *, protocol_turn, **_kwargs):
        protocol_turn.execute("file_write", {"path": str(target)}, lambda: target.write_text("done", encoding="utf-8"))
        return {"status": status, "answer": "Note written", "unresolved_work": []}
    serve(tmp_path, requests(), output, runner=runner)
    assert target.read_text() == "done"
    assert next(frame for frame in frames() if frame.get("id") == 0)["error"]["code"] == "invalid_request"
    final = next(frame for frame in frames() if frame.get("id") == 5)["result"]
    assert final["thread"]["status"] == expected
    assert final["turns"][0]["status"] == expected
    assert final["thread"]["result"]["answer"] == "Note written"
    assert len([item for item in final["items"] if item["kind"] == "tool_execution"]) == 1


def test_exited_owner_recovers_without_replaying_action(tmp_path):
    path = tmp_path / "orphan.sqlite3"
    code = "from smara.runtime_session import SQLiteRuntimeSessionStore; from smara.session_protocol import SessionProtocol; import sys; store=SQLiteRuntimeSessionStore(sys.argv[1]); store.create_or_get('orphan'); SessionProtocol(store).begin('orphan','Unfinished work')"
    result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    protocol = SessionProtocol(SQLiteRuntimeSessionStore(path))
    old = protocol.snapshot("orphan")["turns"][0]["turn_id"]
    new = protocol.begin("orphan", "Inspect current state")
    assert new.turn_id != old
    events = protocol.snapshot("orphan")["events"]
    assert any(event["kind"] == "turn.interrupted" for event in events)
    assert not any(item["kind"] == "tool_execution" for item in protocol.snapshot("orphan")["items"])
    new.finish("completed", "Recovered")


def test_delta_and_reconnect_are_observations_not_execution(tmp_path):
    store, protocol, turn = setup_turn(tmp_path, "auto")
    turn.text_delta("First ")
    cursor = protocol.snapshot("chat")["cursor"]
    turn.text_delta("second")
    turn.finish("completed", "First second")
    resumed = protocol.dispatch("thread/resume", {"thread_id": "chat", "after": cursor})
    assert [event["kind"] for event in resumed["events"]] == ["item.delta", "item.completed", "turn.completed"]
    assert protocol.history("chat")[-1] == {"role": "assistant", "content": "First second"}
    assert store.get("chat").request == "Create a note"


def test_cli_protocol_stdio_is_machine_readable_without_provider(tmp_path):
    request = '\n'.join(json.dumps(value) for value in [
        {"id": 1, "method": "initialize"},
        {"id": 2, "method": "thread/create", "params": {"thread_id": "stdio"}},
        {"id": 3, "method": "thread/read", "params": {"thread_id": "stdio"}},
    ]) + '\n'
    result = subprocess.run([sys.executable, "-m", "smara.cli", "--workspace", str(tmp_path), "app-server"],
                            input=request, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    frames = [json.loads(line) for line in result.stdout.splitlines()]
    assert [frame["id"] for frame in frames] == [1, 2, 3]
    assert frames[0]["result"]["version"] == frames[2]["result"]["version"] == 1
    assert frames[2]["result"]["thread_id"] == "stdio"


def test_canonical_dispatch_freezes_approved_arguments(tmp_path):
    from smara.autonomous_agent import SmaraAutonomousAgent
    store, protocol, turn = setup_turn(tmp_path)
    approved = tmp_path / "approved.txt"
    substituted = tmp_path / "substituted.txt"
    arguments = {"file_path": str(approved), "content": "approved write"}
    agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, profile="coding", protocol_turn=turn)
    results = []
    worker = threading.Thread(target=lambda: results.append(agent.execute_tool("file_write", arguments)))
    worker.start()
    try:
        approval = wait_for(lambda: protocol.dispatch("approval/list", {"thread_id": "chat"})["pending_approvals"])[0]
        arguments["file_path"] = str(substituted)
        protocol.respond("chat", approval["approval_id"], turn.turn_id, approval["action_sha256"], "allow")
        worker.join(4)
        assert not worker.is_alive()
        assert approved.read_text() == "approved write"
        assert not substituted.exists()
    finally:
        protocol.interrupt("chat")
        worker.join(4)
        agent._browser.shutdown()
        agent._cancel_owned_processes()


def test_external_thread_keeps_canonical_execution_and_explicit_resume(tmp_path, monkeypatch):
    from smara import app_adapter
    from smara.harness import SessionEngine
    from smara.runtime_session import SQLiteRuntimeSessionStore
    store = SQLiteRuntimeSessionStore(tmp_path / "desktop" / "runtime.sqlite3")
    store.create_or_get("stable-thread", request="Patch the calculator", workspace_id=str(tmp_path))
    protocol = SessionProtocol(store)
    observed = []
    def run(self, task, **kwargs):
        engine = self.session_engine
        engine.begin_incremental(task)
        observed.append((engine.session_id, task, engine.get("usage")))
        engine.checkpoint([{"role": "user", "content": task}], {"phase": "model"})
        if len(observed) == 1:
            engine.set("usage", {"model_calls": 1, "billed_tokens": 100, "tool_calls": 0, "dollars": .01})
        return {"session": engine.finish_incremental("needs_input", "Policy still needed")}
    monkeypatch.setattr(app_adapter.SmaraAutonomousAgent, "run", run)
    turn = protocol.begin("stable-thread", "Patch the calculator")
    result = app_adapter.run_canonical_task("Patch the calculator", tmp_path, protocol_turn=turn,
        model_settings={"model": "unit-provider", "base_url": "https://provider.test/v2", "api_key": "fixture"})
    assert protocol.snapshot("stable-thread")["can_resume"] is False
    turn.finish("needs_input", result["answer"])
    assert protocol.snapshot("stable-thread")["can_resume"] is True
    assert result["runtime"]["session"]["session_id"] == "stable-thread"
    assert protocol.recovery("stable-thread", tmp_path)["session_id"] == result["session_id"]
    resumed = app_adapter.resume_canonical_task("stable-thread", tmp_path, protocol=protocol)
    assert resumed["session_id"] == result["session_id"]
    assert observed[1][2]["billed_tokens"] == 100
    assert store.get("stable-thread").request == "Patch the calculator"
    assert len(protocol.snapshot("stable-thread")["turns"]) == 2
    assert len(store.list()) == 1
