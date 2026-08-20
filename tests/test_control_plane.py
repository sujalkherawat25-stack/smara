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
    assert fake.writes[0]["run_id"] == task["id"]
