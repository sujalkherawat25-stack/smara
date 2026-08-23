import inspect
from datetime import datetime, timezone

from smara import store
from smara.api import _as_datetime


def test_live_database_url_selects_postgres_store(monkeypatch, tmp_path):
    chosen = object()
    monkeypatch.setattr(store, "PostgresTaskStore", lambda url: chosen)

    actual = store.open_task_store(
        database_url="postgresql://smara:secret@postgres/smara",
        database_path=str(tmp_path / "local.db"),
    )

    assert actual is chosen


def test_no_database_url_keeps_sqlite_for_local_development(tmp_path):
    actual = store.open_task_store(database_url="", database_path=str(tmp_path / "local.db"))
    assert isinstance(actual, store.TaskStore)


def test_postgres_claim_uses_a_skip_locked_row_lease():
    assert "FOR UPDATE OF s SKIP LOCKED" in inspect.getsource(store.PostgresTaskStore.claim_one)
    assert "s.executor_kind IN ('hosted','sandbox')" in inspect.getsource(store.PostgresTaskStore.claim_one)


def test_research_list_parameterizes_like_pattern_for_postgres():
    source = inspect.getsource(store.TaskStore.research_tasks)
    assert "s.name LIKE ?" in source
    assert '"research.%"' in source


def test_api_accepts_postgres_timestamp_objects():
    timestamp = datetime.now(timezone.utc)
    assert _as_datetime(timestamp) is timestamp
