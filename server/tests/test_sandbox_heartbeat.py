"""The sandbox heartbeat keeps beating while a long job runs, so /healthz does not go 503 for a healthy sandbox."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox"))
import runner  # noqa: E402

from src.box.sandbox import Sandbox  # noqa: E402


def test_heartbeat_continues_while_the_main_loop_is_busy(tmp_path):
    runner.start_heartbeat(tmp_path, every_s=0.05)
    time.sleep(0.2)
    first = float((tmp_path / ".heartbeat").read_text())
    time.sleep(0.3)                                  # the "long job": nothing else writes the file
    assert float((tmp_path / ".heartbeat").read_text()) > first


def test_a_stale_heartbeat_still_means_unhealthy(tmp_path):
    sb = Sandbox.__new__(Sandbox)
    sb.mode, sb.jobs = "container", tmp_path
    runner.write_heartbeat(tmp_path); (tmp_path / ".heartbeat").write_text(str(time.time() - 120))
    assert sb.healthy() is False
    (tmp_path / ".heartbeat").write_text(str(time.time()))
    assert sb.healthy() is True
