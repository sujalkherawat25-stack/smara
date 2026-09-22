import json
from pathlib import Path

from smara.desktop_executor import _main as desktop_main
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


def test_desktop_runtime_session_list_is_bounded_and_machine_readable(tmp_path: Path, capsys):
    state_path = tmp_path / "state.json"
    store = session_store_for_state(state_path)
    store.create_or_get("older", request="first")
    store.create_or_get("newer", request="second")
    store.checkpoint("newer", status="completed", result={"answer": "x" * 20_000})

    assert desktop_main(["--state", str(state_path), "--runtime-session-list", "--runtime-limit", "1"]) == 0
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert len(payload) == 1
    assert payload[0]["session_id"] == "newer"
    assert payload[0]["result"] == {}
    assert len(raw) < 2_000


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


def test_new_turn_refreshes_stable_conversation_envelope(tmp_path: Path):
    store = SQLiteRuntimeSessionStore(tmp_path / "sessions.sqlite3", max_events=32)
    store.create_or_get("chat-1", request="inspect the folder", model_profile="old-model")
    store.checkpoint("chat-1", status="completed", result={"answer": "old answer"}, event="turn.completed")
    store.request_cancel("chat-1", "old cancellation")

    refreshed = store.start_turn(
        "chat-1",
        request="what is today's news?",
        model_profile="new-model",
        research_mode="quick",
    )

    assert refreshed.request == "what is today's news?"
    assert refreshed.model_profile == "new-model"
    assert refreshed.research_mode == "quick"
    assert refreshed.status == "created"
    assert refreshed.result == {}
    assert refreshed.unresolved == []
    assert refreshed.cancel_requested is False
    assert refreshed.cancel_reason is None


def test_legacy_envelope_request_reconciles_from_latest_turn_event(tmp_path: Path):
    store = SQLiteRuntimeSessionStore(tmp_path / "sessions.sqlite3", max_events=32)
    store.create_or_get("legacy-chat", request="inspect the folder")
    store.checkpoint(
        "legacy-chat",
        status="running",
        event="turn.started",
        event_payload={"request": "what is today's news?"},
    )
    store.checkpoint("legacy-chat", status="completed", result={"answer": "news"})

    assert store.get("legacy-chat").request == "what is today's news?"
    assert store.list(limit=1)[0].request == "what is today's news?"
    assert store.snapshot("legacy-chat")["session"]["request"] == "what is today's news?"


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
