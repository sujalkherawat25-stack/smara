import time
import sqlite3

import pytest

from smara.research_watch import ResearchWatchStore, compare_refreshes


@pytest.mark.parametrize("hours", [0.001, 0.5, 0.999, 8761])
def test_watch_rejects_intervals_outside_one_hour_to_one_year(tmp_path, hours):
    with pytest.raises(ValueError, match="1 hour to 1 year"):
        ResearchWatchStore(tmp_path).add("Track source", hours)


def test_failed_refreshes_do_not_hide_last_successful_baseline(tmp_path):
    store = ResearchWatchStore(tmp_path)
    watch = store.add("Track source", baseline=_result(
        [{"claim": "Original claim", "supported": True, "citations": []}], []))
    for _ in range(25):
        store.run(watch["id"], runner=lambda *_: {"status": "tool_error"})
    run = store.run(watch["id"], runner=lambda *_: _result(
        [{"claim": "New claim", "supported": True, "citations": []}], []))
    assert not run["diff"]["baseline"]
    assert run["diff"]["not_repeated_claims"] == ["Original claim"]


def test_watch_closes_real_database_connections(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    connections = []

    def track_connect(*args, **kwargs):
        db = original_connect(*args, **kwargs)
        connections.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", track_connect)
    store = ResearchWatchStore(tmp_path)
    watch = store.add("Track source")
    store.run(watch["id"], runner=lambda *_: _result([], []))
    store.history(watch["id"])
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            db.execute("SELECT 1")


def _result(claims, sources, answer="research answer"):
    return {
        "status": "completed",
        "session_id": "session-test",
        "answer": answer,
        "research_review": {
            "claims": claims,
            "evidence": sources,
            "failures": [],
        },
    }


def test_refresh_comparison_identifies_new_unsupported_and_not_repeated_claims():
    previous = _result(
        [{"claim": "A is supported", "supported": True, "citations": [{"url": "https://a.test"}]},
         {"claim": "B is supported", "supported": True, "citations": []}],
        [{"url": "https://a.test", "sha256": "old"}],
    )
    current = _result(
        [{"claim": "A is supported", "supported": False, "citations": []},
         {"claim": "C is new", "supported": True, "citations": [{"url": "https://c.test"}]}],
        [{"url": "https://a.test", "sha256": "changed"}, {"url": "https://c.test", "sha256": "c1"}],
    )
    diff = compare_refreshes(previous["research_review"], current)
    assert diff["new_claims"] == ["C is new"]
    assert diff["unsupported_claims"] == ["A is supported"]
    assert diff["not_repeated_claims"] == ["B is supported"]
    assert diff["sources_added"] == ["https://c.test"]
    assert diff["sources_changed"] == ["https://a.test"]
    assert "not proof" in diff["staleness_note"]


def test_watch_store_keeps_baseline_runs_and_persists_refresh_diffs(tmp_path):
    store = ResearchWatchStore(tmp_path)
    baseline = _result([{"claim": "Original supported claim", "supported": True,
                         "citations": [{"url": "https://source.test"}]}],
                       [{"url": "https://source.test", "sha256": "one", "cited": True}])
    watch = store.add("Track the source", 24, baseline=baseline)
    assert watch["enabled"] and watch["next_run_at"] > 0
    refreshed = _result([{"claim": "Updated supported claim", "supported": True,
                          "citations": [{"url": "https://new.test"}]}],
                        [{"url": "https://new.test", "sha256": "two", "cited": True}])
    def delayed_runner(_topic, _workspace):
        time.sleep(0.02)
        return refreshed

    run = store.run(watch["id"], runner=delayed_runner)
    assert run["status"] == "completed"
    assert run["diff"]["new_claims"] == ["Updated supported claim"]
    assert run["diff"]["not_repeated_claims"] == ["Original supported claim"]
    history = store.history(watch["id"])
    assert [item["status"] for item in history] == ["completed", "baseline"]
    assert history[0]["completed_at"] >= history[0]["started_at"]
    assert history[0]["completed_at"] - history[0]["started_at"] >= 0.015
    assert store.list()[0]["last_error"] is None


def test_due_watches_run_once_and_pause_preserves_audit_history(tmp_path):
    store = ResearchWatchStore(tmp_path)
    watch = store.add("Daily check", 24)
    result = store.run_due(runner=lambda _topic, _workspace: _result([], []))
    assert len(result) == 1
    assert result[0]["watch_id"] == watch["id"]
    assert store.run_due(runner=lambda *_: {}) == []
    store.remove(watch["id"])
    assert not store.get(watch["id"])["enabled"]
    assert store.history(watch["id"])
