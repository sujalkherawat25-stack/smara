import sys
import time

import pytest

from smara.harness import PolicyDenied, ProcessSupervisor
from smara.harness import SessionEngine


def test_foreign_session_cannot_write_or_cancel_process(tmp_path):
    owner = ProcessSupervisor(tmp_path / "owner")
    stranger = ProcessSupervisor(tmp_path / "stranger")
    handle = owner.start([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, None, 40)["process_id"]
    try:
        for action in (lambda: stranger.write(handle, "unwanted\n"), lambda: stranger.cancel(handle), lambda: stranger.poll(handle)):
            with pytest.raises(PolicyDenied):
                action()
            assert owner.processes[handle].poll() is None
    finally:
        owner.cancel(handle)


def test_process_handles_reject_path_traversal(tmp_path):
    supervisor = ProcessSupervisor(tmp_path)
    with pytest.raises(PolicyDenied):
        supervisor.poll("../other/process")


def test_deadline_stops_process_without_polling(tmp_path):
    supervisor = ProcessSupervisor(tmp_path / "processes")
    handle = supervisor.start([sys.executable, "-c", "import time; time.sleep(2); open('late.txt','w').write('bad')"], tmp_path, None, .2)["process_id"]
    try:
        time.sleep(2.3)
        assert supervisor.processes[handle].poll() is not None
        assert not (tmp_path / "late.txt").exists()
        assert supervisor.poll(handle)["status"] == "timed_out"
    finally:
        supervisor.cancel(handle)


def test_finalization_rejects_running_background_process(tmp_path):
    engine = SessionEngine(tmp_path, "running", constrained=False)
    engine.begin_incremental("complete the process")
    supervisor = engine.broker.processes
    handle = supervisor.start([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, None, 40)["process_id"]
    try:
        result = engine.finish_incremental("completed", "done")
        assert result["status"] == "needs_input"
        assert any("running" in item for item in result["unresolved_items"])
    finally:
        supervisor.cancel(handle)
        engine.close()
