import asyncio
from pathlib import Path

from smara.store import TaskStore
from smara.syntarus_adapter import SyntarusMemory
from smara.worker import run_once


class FakeSyntarus:
    def __init__(self): self.writes = []
    async def search(self, *_args, **_kwargs): return {"context": "User prefers verified sources."}
    async def add(self, **kwargs): self.writes.append(kwargs); return {"event_id": "evt_test"}


def test_task_requires_approval_before_work(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create("acct_1", "work", "Send report", "Prepare the report", True)
    assert store.claim_one()["status"] == "waiting_approval"
    assert store.decide(task["id"], "acct_1", True, "Looks good")["status"] == "queued"


def test_worker_uses_only_memory_adapter(tmp_path: Path):
    store, fake = TaskStore(str(tmp_path / "smara.db")), FakeSyntarus()
    task = store.create("acct_1", "work", "Research", "Find reliable information", False)
    assert asyncio.run(run_once(store, SyntarusMemory(fake)))
    assert store.get(task["id"], "acct_1")["status"] == "completed"
    assert fake.writes[0]["user_id"] == "acct_1"
    assert fake.writes[0]["run_id"].startswith("run_")
    assert fake.writes[0]["metadata"]["workspace_id"] == "work"
    assert fake.writes[0]["metadata"]["memory_kind"] == "verified_outcome"


def test_step_lease_prevents_double_claim_and_recovers(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create("acct_1", "work", "Run", "Perform one safe step", False)
    first = store.claim_one("worker-a", lease_seconds=1)
    assert first and first["step_id"]
    assert store.claim_one("worker-b") is None
    with store._connect() as con:
        con.execute("UPDATE task_steps SET lease_expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", first["step_id"]))
    recovered = store.claim_one("worker-b")
    assert recovered and recovered["step_id"] == first["step_id"]
    assert recovered["lease_owner"] == "worker-b"


def test_dependency_graph_unlocks_next_step(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create("acct_1", "work", "Report", "Create a report", False, [
        {"name": "research", "depends_on": []},
        {"name": "write", "depends_on": [0]},
    ])
    first = store.claim_one("worker-a")
    assert first and first["name"] == "research"
    assert store.claim_one("worker-b") is None
    store.complete_step(first["step_id"], "acct_1", "research complete")
    second = store.claim_one("worker-b")
    assert second and second["name"] == "write"
    store.complete_step(second["step_id"], "acct_1", "report complete")
    assert store.get(task["id"], "acct_1")["status"] == "completed"


def test_failed_step_retries_with_a_bounded_budget(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create("acct_1", "work", "Unstable", "Retry safely", False)
    for attempt in range(1, 4):
        claimed = store.claim_one("worker", lease_seconds=10)
        assert claimed is not None
        outcome = store.fail_step(claimed["step_id"], "acct_1", "temporary upstream failure", retry_delay_seconds=0)
        assert outcome == ("failed" if attempt == 3 else "retrying")
    assert store.get(task["id"], "acct_1")["status"] == "failed"


def test_cancellation_stops_future_steps_but_not_running_step(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create("acct_1", "work", "Cancel", "Stop safely", False, [
        {"name": "first", "depends_on": []}, {"name": "second", "depends_on": [0]},
    ])
    first = store.claim_one("worker")
    assert store.cancel(task["id"], "acct_1")["status"] == "cancelling"
    # The already-claimed operation can finish, but its dependent step is never run.
    store.complete_step(first["step_id"], "acct_1", "finished at safe boundary")
    assert store.get(task["id"], "acct_1")["status"] == "cancelled"
    assert store.claim_one("other-worker") is None
