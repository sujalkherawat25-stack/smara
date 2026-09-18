from pathlib import Path

from smara.runtime_session import RuntimeSession, SQLiteRuntimeSessionStore, session_store_for_state


def test_runtime_session_lifecycle_and_events(tmp_path: Path):
    store = SQLiteRuntimeSessionStore(tmp_path / "sessions.sqlite3", max_events=32)
    session = store.create_or_get("chat-1", request="hello", workspace_id="ws")
    assert session.status == "created"
    store.checkpoint("chat-1", status="running", event="turn.started")
    store.checkpoint("chat-1", status="completed", result={"answer": "ok"}, event="turn.completed")
    resumed = store.resume("chat-1")
    assert resumed.status == "completed"
    assert resumed.result["answer"] == "ok"
    assert [event.kind for event in store.events("chat-1")] == ["turn.started", "turn.completed"]
    assert RuntimeSession.from_dict({"session_id": "legacy"}).workspace_id == "default"


def test_state_store_is_stable_sibling(tmp_path: Path):
    store = session_store_for_state(tmp_path / "state.json")
    assert store.path.name == "runtime-sessions.sqlite3"


def test_runtime_session_cancel_is_idempotent_and_wins_late_completion(tmp_path: Path):
    store = SQLiteRuntimeSessionStore(tmp_path / "sessions.sqlite3", max_events=32)
    store.create_or_get("cancel-1", request="long work")
    store.checkpoint("cancel-1", status="running", event="turn.started")
    cancelled = store.request_cancel("cancel-1", "operator stopped it")
    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested is True
    # A provider response that races with cancellation cannot resurrect it.
    late = store.checkpoint("cancel-1", status="completed", result={"answer": "late"}, event="turn.completed")
    assert late.status == "cancelled"
    assert late.unresolved == ["operator stopped it"]
    assert [event.kind for event in store.events("cancel-1")] == ["turn.started", "turn.cancel_requested", "turn.cancelled"]
    assert store.request_cancel("cancel-1").status == "cancelled"


def test_runtime_snapshot_replays_after_cursor_and_reports_gap(tmp_path: Path):
    store = SQLiteRuntimeSessionStore(tmp_path / "sessions.sqlite3", max_events=32)
    store.create_or_get("replay-1", request="replay")
    for index in range(3):
        store.append_event("replay-1", "stream.status", {"index": index})
    snapshot = store.snapshot("replay-1", after=1, limit=1)
    assert [item["sequence"] for item in snapshot["events"]] == [2]
    assert snapshot["cursor"] == 2 and snapshot["has_more"] is True
    assert store.snapshot("replay-1", after=2)["events"][0]["sequence"] == 3


def test_runtime_snapshot_detects_compacted_event_gap(tmp_path: Path):
    store = SQLiteRuntimeSessionStore(tmp_path / "sessions.sqlite3", max_events=32)
    store.create_or_get("gap-1", request="gap")
    for index in range(40):
        store.append_event("gap-1", "stream.status", {"index": index})
    # max_events is clamped to 32, so sequence 1 is no longer replayable.
    try:
        store.snapshot("gap-1", after=1)
    except ValueError as exc:
        assert "no longer replayable" in str(exc)
    else:  # pragma: no cover - protects the event-retention contract
        raise AssertionError("expected a replay gap")
