import json
import sys
import time
from pathlib import Path

from smara.persistent_terminal import PersistentTerminalStore, _pid_alive


def wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("deadline enforcement did not finish")


def test_persistent_deadline_does_not_need_polling(tmp_path):
    store = PersistentTerminalStore(tmp_path / "state", allowed_roots=[tmp_path])
    started = store.start([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, max_seconds=1)
    try:
        wait_for(lambda: not _pid_alive(started["pid"]))
        entry = wait_for(lambda: next((item for item in json.loads(store.metadata_path.read_text())
                                      if item["status"] == "expired"), None))
        assert entry["exit_code"] is not None
        assert PersistentTerminalStore(store.root).poll(started["session_id"])["status"] == "expired"
    finally:
        store.cancel(started["session_id"])


def test_persistent_exit_code_is_saved_without_polling(tmp_path):
    store = PersistentTerminalStore(tmp_path / "state")
    started = store.start([sys.executable, "-c", "print('real output'); raise SystemExit(7)"], cwd=tmp_path)
    entry = wait_for(lambda: next((item for item in json.loads(store.metadata_path.read_text())
                                  if item["status"] == "failed"), None))
    assert entry["exit_code"] == 7
    fresh = PersistentTerminalStore(store.root)
    result = fresh.poll(started["session_id"])
    assert result["exit_code"] == 7 and "real output" in result["output"]


def test_owned_process_exit_race_does_not_discard_genuine_code(tmp_path, monkeypatch):
    import smara.persistent_terminal as module
    from datetime import datetime, timezone
    store = PersistentTerminalStore(tmp_path / "state")
    log = tmp_path / "process.log"
    log.write_text("real output")
    codes = iter([None, 7])
    class Process:
        def poll(self): return next(codes)
    monkeypatch.setitem(module._PROCESS_HANDLES, "race", Process())
    monkeypatch.setattr(module, "_pid_alive", lambda _: False)
    entry = {"id": "race", "pid": 123, "status": "running", "exit_code": None,
             "log_path": str(log), "started_at": datetime.now(timezone.utc).isoformat(), "max_seconds": 900}
    store._refresh(entry)
    assert entry["status"] == "running"  # wait for the owned handle, not an unknown exit
    store._refresh(entry)
    assert entry["status"] == "failed" and entry["exit_code"] == 7


def test_all_agent_profiles_use_isolated_execution(tmp_path):
    from smara.autonomous_agent import SmaraAutonomousAgent, VALID_TOOLSETS
    for profile in VALID_TOOLSETS:
        agent = SmaraAutonomousAgent(api_key="fixture", workspace_root=tmp_path, profile=profile)
        try:
            assert agent._execution_broker.sandboxed
            assert agent._execution_broker.read_only == (profile in {"worker", "worker_verification"})
        finally:
            agent._browser.shutdown()


def test_readonly_broker_denies_typed_writes(tmp_path):
    from smara.harness import ToolBroker, ToolCall
    broker = ToolBroker(tmp_path, {"write_file"}, sandboxed=True, read_only=True)
    result = broker.dispatch(ToolCall("write", "write_file", {"path": "bad.txt", "content": "blocked"}, str(tmp_path)))
    assert result.status == "denied"
    assert not (tmp_path / "bad.txt").exists()


def test_reconnected_process_supervisors_share_lock(tmp_path):
    from smara.harness import ProcessSupervisor
    first = ProcessSupervisor(tmp_path / "processes")
    reopened = ProcessSupervisor(tmp_path / "processes")
    unrelated = ProcessSupervisor(tmp_path / "other")
    assert first.lock is reopened.lock
    assert first.lock is not unrelated.lock


def test_process_metadata_replacement_never_exposes_partial_json(tmp_path, monkeypatch):
    import smara.harness as module
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    original_replace = module.os.replace
    observed = []
    def inspect_replace(source, destination):
        observed.append(json.loads(path.read_text(encoding="utf-8")))
        assert json.loads(Path(source).read_text(encoding="utf-8")) == {"status": "completed"}
        return original_replace(source, destination)
    monkeypatch.setattr(module.os, "replace", inspect_replace)
    module.ProcessSupervisor._save_metadata(path, {"status": "completed"})
    assert observed == [{"status": "running"}]
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "completed"}
    assert not list(tmp_path.glob("process-*.tmp"))
