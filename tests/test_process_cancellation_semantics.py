import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from smara.harness import (
    Budget,
    PolicyDenied,
    ProcessSupervisor,
    SessionEngine,
    ToolBroker,
    ToolCall,
)


def test_cancel_running_15s_canary(tmp_path: Path):
    broker = ToolBroker(tmp_path, {"process_start", "process_cancel", "process_poll"}, constrained=False)
    canary_file = tmp_path / "canary_15s.txt"
    script_file = tmp_path / "canary_script.py"
    script_file.write_text(
        "import pathlib, time\n"
        "time.sleep(15)\n"
        f"pathlib.Path(r'{canary_file}').write_text('bad')\n",
        encoding="utf-8"
    )

    start_call = ToolCall("c1", "process_start", {"argv": [sys.executable, str(script_file)], "timeout_seconds": 30}, str(tmp_path))
    started = broker.dispatch(start_call)
    assert started.ok
    process_id = started.meta["process_id"]

    # Verify process is actively running
    poll_call = ToolCall("c2", "process_poll", {"process_id": process_id}, str(tmp_path))
    polled = broker.dispatch(poll_call)
    assert polled.meta.get("exit_code") is None

    # Cancel the running process
    cancel_call = ToolCall("c3", "process_cancel", {"process_id": process_id}, str(tmp_path))
    cancelled = broker.dispatch(cancel_call)
    assert cancelled.status == "cancelled"
    assert cancelled.meta["cancellation_status"] == "confirmed_cancelled"
    assert cancelled.meta["termination_attempted"] is True
    assert cancelled.meta["tree_terminated"] is True

    time.sleep(1.0)
    assert not canary_file.exists()


def test_cancel_process_tree_child_canary(tmp_path: Path):
    broker = ToolBroker(tmp_path, {"process_start", "process_cancel", "process_poll"}, constrained=False)
    child_canary = tmp_path / "child_canary.txt"
    child_script = tmp_path / "child_script.py"
    child_script.write_text(
        "import time, pathlib\n"
        "time.sleep(15)\n"
        f"pathlib.Path(r'{child_canary}').write_text('bad')\n",
        encoding="utf-8"
    )
    parent_script = tmp_path / "parent_script.py"
    parent_script.write_text(
        "import subprocess, sys, time\n"
        f"p = subprocess.Popen([sys.executable, r'{child_script}'])\n"
        "time.sleep(30)\n",
        encoding="utf-8"
    )

    started = broker.dispatch(ToolCall("c1", "process_start", {"argv": [sys.executable, str(parent_script)], "timeout_seconds": 45}, str(tmp_path)))
    assert started.ok
    process_id = started.meta["process_id"]

    time.sleep(0.5)  # allow child to spawn
    # Verify parent is running
    polled = broker.dispatch(ToolCall("c2", "process_poll", {"process_id": process_id}, str(tmp_path)))
    assert polled.meta.get("exit_code") is None

    cancelled = broker.dispatch(ToolCall("c3", "process_cancel", {"process_id": process_id}, str(tmp_path)))
    assert cancelled.status == "cancelled"
    assert cancelled.meta["tree_terminated"] is True

    time.sleep(1.0)
    assert not child_canary.exists()


def test_cancel_after_natural_completion_returns_already_completed(tmp_path: Path):
    supervisor = ProcessSupervisor(tmp_path / "processes")
    handle = supervisor.start([sys.executable, "-c", "import sys; sys.exit(42)"], tmp_path, None, 10)["process_id"]

    # Wait for process to exit naturally
    for _ in range(50):
        if supervisor.processes[handle].poll() is not None:
            break
        time.sleep(0.1)
    assert supervisor.processes[handle].poll() == 42

    # Cancel after natural exit
    result = supervisor.cancel(handle)
    assert result["status"] == "already_completed"
    assert result["cancellation_status"] == "already_completed"
    assert result["termination_attempted"] is False
    assert result["tree_terminated"] is False
    assert result["exit_code"] == 42

    # Idempotent repeat
    repeat = supervisor.cancel(handle)
    assert repeat["status"] == "already_completed"
    assert repeat["cancellation_status"] == "already_completed"
    assert repeat["termination_attempted"] is False


def test_uncertain_cancellation_blocks_finalization(tmp_path: Path):
    engine = SessionEngine(tmp_path, "session_uncertain", constrained=False)
    engine.begin_incremental("test uncertain cancellation")
    supervisor = engine.broker.processes
    handle = supervisor.start([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, None, 40)["process_id"]

    # Simulate failed kill where process poll remains None
    with patch.object(ProcessSupervisor, "kill", lambda self, proc: None), \
         patch.object(supervisor.processes[handle], "poll", return_value=None):
        cancel_res = supervisor.cancel(handle)
        assert cancel_res["status"] == "cancellation_uncertain"
        assert cancel_res["cancellation_status"] == "cancellation_uncertain"
        assert cancel_res["tree_terminated"] is False

    # Finalization must be rejected because process is not confirmed reconciled
    finish_res = engine.finish_incremental("completed", "all done")
    assert finish_res["status"] == "needs_input"
    assert any("cancellation_uncertain" in item for item in finish_res["unresolved_items"])

    # Clean up real process
    supervisor.cancel(handle)
    engine.close()


def test_idempotent_running_cancellation(tmp_path: Path):
    supervisor = ProcessSupervisor(tmp_path / "processes")
    handle = supervisor.start([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, None, 40)["process_id"]

    res1 = supervisor.cancel(handle)
    assert res1["status"] == "cancelled"
    assert res1["cancellation_status"] == "confirmed_cancelled"
    assert res1["termination_attempted"] is True

    res2 = supervisor.cancel(handle)
    assert res2["status"] == "cancelled"
    assert res2["cancellation_status"] == "confirmed_cancelled"

    res3 = supervisor.cancel(handle)
    assert res3["status"] == "cancelled"
    assert res3["cancellation_status"] == "confirmed_cancelled"


def test_session_engine_incremental_accepts_cancelled_processes(tmp_path: Path):
    engine = SessionEngine(tmp_path, "session_cancel_ok", constrained=False)
    engine.begin_incremental("run and cancel process")
    supervisor = engine.broker.processes
    handle = supervisor.start([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, None, 40)["process_id"]

    supervisor.cancel(handle)
    finish_res = engine.finish_incremental("completed", "successfully cancelled and done")
    assert finish_res["status"] == "completed"
    assert len(finish_res["unresolved_items"]) == 0
    engine.close()


def test_cancellation_race_20_repetitions(tmp_path: Path):
    """Run 20 rapid start-and-cancel cycles to detect concurrency races or deadlocks."""
    for i in range(20):
        sub_dir = tmp_path / f"iter_{i}"
        sub_dir.mkdir(parents=True, exist_ok=True)
        supervisor = ProcessSupervisor(sub_dir / "processes")
        canary = sub_dir / "canary.txt"
        script_file = sub_dir / "race_script.py"
        script_file.write_text(
            "import time, pathlib\n"
            "time.sleep(10)\n"
            f"pathlib.Path(r'{canary}').write_text('bad')\n",
            encoding="utf-8"
        )
        handle = supervisor.start(
            [sys.executable, str(script_file)],
            sub_dir, None, 30
        )["process_id"]

        res = supervisor.cancel(handle)
        assert res["status"] in ("cancelled", "already_completed")
        assert not canary.exists()
