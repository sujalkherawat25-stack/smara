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
