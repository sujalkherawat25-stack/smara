from pathlib import Path

from smara.local_gateway import GatewayLedger, LocalGateway
from smara.local_scheduler import LocalScheduleStore


def test_gateway_delivery_is_idempotent(tmp_path: Path):
    calls = []
    gateway = LocalGateway(GatewayLedger(tmp_path / "gateway.sqlite3"), lambda text, session: calls.append(text) or "reply")
    first = gateway.receive(channel="test", sender="u", text="hello", idempotency_key="same")
    second = gateway.receive(channel="test", sender="u", text="hello", idempotency_key="same")
    assert first["status"] == "delivered"
    assert second["status"] == "already_delivered"
    assert calls == ["hello"]


def test_scheduler_claims_and_repeats(tmp_path: Path):
    store = LocalScheduleStore(tmp_path / "schedule.sqlite3")
    row = store.add("heartbeat", {"x": 1}, 5, next_run_at=0)
    seen = []
    result = store.tick(lambda item: seen.append(item["id"]), now=10)
    assert result[0]["status"] == "completed"
    assert seen == [row["id"]]
    assert store.list()[0]["running"] is False
