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


async def test_several_jobs_run_at_the_same_time(tmp_path):
    """Three 2-second jobs finish in about 2 seconds with 3 workers, not 6: one long job no longer blocks the rest."""
    import asyncio
    import os
    import subprocess

    runner_py = Path(runner.__file__)
    env = {**os.environ, "SANDBOX_JOBS": str(tmp_path), "SANDBOX_WORKERS": "3"}
    proc = subprocess.Popen([sys.executable, str(runner_py)], env=env)
    try:
        sb = Sandbox(str(tmp_path), mode="container")
        started = time.monotonic()
        results = await asyncio.gather(*(sb.run("import time; time.sleep(2); print('done')", {}, timeout_s=30) for _ in range(3)))
        took = time.monotonic() - started
    finally:
        proc.terminate()
        proc.wait(10)
    assert all("done" in r.get("stdout", "") for r in results), results
    assert took < 5.5, f"jobs ran one after another ({took:.1f}s)"
