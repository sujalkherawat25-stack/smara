"""Shared ledger and bounded recurrence; no provider calls."""
from concurrent.futures import ThreadPoolExecutor
import time
import pytest
from smara.native_goals import GoalStore
from smara.native_background import add, enable, enqueue_due, schedules


def setup(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = add(store, {"name": "Owned fixture", "interval_seconds": 60, "max_runs": 2,
        "spec": {"workspace": str(tmp_path), "profile_id": "synthetic", "objective": "Inspect public facts",
                 "success_criteria": "Evidence", "max_requests": 2}})
    return store, identity


def test_paused_by_default_and_atomic_occurrence(tmp_path):
    store, identity = setup(tmp_path)
    assert not enqueue_due(store)
    enable(store, identity, True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: enqueue_due(store, time.time() + 10000), range(2)))
    assert sum(map(len, results)) == 1
    assert len(store.list()) == 1 and schedules(store)[0]["runs"] == 1
    assert not enqueue_due(store, time.time() + 20000)  # No backlog or duplicate.


def test_next_wake_requires_prior_acceptance_and_keeps_its_budget(tmp_path):
    store, identity = setup(tmp_path); enable(store, identity, True)
    first = enqueue_due(store, time.time() + 10000)[0]
    goal = store.claim(first)
    store.reserve(goal, 5000)
    store.finish(goal, {"decision": "ready_for_review", "summary": "Evidence"}, "ready_for_review", "Review")
    assert not enqueue_due(store, time.time() + 20000)
    store.action({"action": "accept", "id": first})
    second = enqueue_due(store, time.time() + 20000)[0]
    assert first != second and store.get(first)["requests"] == 1
    assert schedules(store)[0]["runs"] == 2


def test_interrupted_wake_is_not_replayed_after_restart(tmp_path):
    store, identity = setup(tmp_path); enable(store, identity, True)
    first = enqueue_due(store, time.time() + 10000)[0]
    store.claim(first); store.recover()
    assert not enqueue_due(GoalStore(store.path), time.time() + 20000)
    assert store.get(first)["state"] == "paused"


def test_recurring_write_scope_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="read-only"):
        add(GoalStore(tmp_path / "goals.sqlite"), {"name": "Unsafe", "spec": {
            "workspace": str(tmp_path), "profile_id": "synthetic", "objective": "Write",
            "success_criteria": "Result", "sandbox": "workspace-write"}})


def test_explicit_legacy_migration_is_idempotent_and_paused(tmp_path):
    from smara.native_schedule import ScheduleStore
    from smara.native_background import import_legacy
    legacy = ScheduleStore(tmp_path / "old.sqlite")
    identity = legacy.add("Old owned fixture", {"workspace": str(tmp_path), "prompt": "Synthetic research", "profile_id": "synthetic", "interval_seconds": 60, "max_requests": 2})
    legacy.enable(identity, True)
    store = GoalStore(tmp_path / "goals.sqlite")
    assert len(import_legacy(store, legacy.path)) == 1
    assert len(import_legacy(store, legacy.path)) == 1
    assert not schedules(store)[0]["enabled"] and not legacy.list()[0]["enabled"]
    assert not store.list()


def test_legacy_migration_does_not_steal_an_uncertain_run(tmp_path):
    from smara.native_schedule import ScheduleStore
    from smara.native_background import import_legacy
    legacy = ScheduleStore(tmp_path / "old.sqlite")
    identity = legacy.add("Owned uncertain", {"workspace": str(tmp_path), "prompt": "Synthetic", "profile_id": "synthetic", "interval_seconds": 60, "max_requests": 2})
    legacy.enable(identity, True); claimed = legacy.claim()
    store = GoalStore(tmp_path / "goals.sqlite")
    with pytest.raises(ValueError, match="uncertain"): import_legacy(store, legacy.path)
    assert legacy.list()[0]["running"] == claimed["run_id"] and not schedules(store)


def test_worker_health_reports_offline_without_claiming_failure(tmp_path):
    store, _ = setup(tmp_path)
    with store.connect() as db:
        store.set_meta(db, "service", {"heartbeat": time.time()})
        store.set_meta(db, "background_health", {"online": False})
    assert store.service()["running"] and store.service()["online"] is False


def _watch(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    identity = add(store, {"name": "Kiran: watch mail", "interval_seconds": 900, "max_runs": 5, "watch": True,
        "spec": {"workspace": str(tmp_path), "profile_id": "synthetic", "objective": "Check the inbox for GST mail",
                 "success_criteria": "Evidence", "max_requests": 2}})
    enable(store, identity, True)
    return store, identity


def _finish(store, goal_id, summary, state="ready_for_review"):
    import json
    with store.connect() as db:
        db.execute("UPDATE goals SET state=?,checkpoint=? WHERE id=?", (state, json.dumps({"summary": summary}), goal_id))


def test_a_watch_may_not_run_more_often_than_every_15_minutes(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    spec = {"workspace": str(tmp_path), "profile_id": "synthetic", "objective": "x", "success_criteria": "y", "max_requests": 2}
    with pytest.raises(ValueError):
        add(store, {"name": "fast", "interval_seconds": 600, "watch": True, "spec": spec})
    add(store, {"name": "ok", "interval_seconds": 900, "watch": True, "spec": spec})


def test_each_watch_run_gets_its_window_and_the_quiet_marker_instruction(tmp_path):
    store, _ = _watch(tmp_path)
    now = time.time() + 10000
    goal = store.get(enqueue_due(store, now)[0])
    text = goal["spec"]["objective"]
    assert text.startswith("Check the inbox for GST mail") and "recurring watch" in text and "NOTHING_NEW" in text and "arrived after" in text


def test_a_quiet_watch_closes_itself_so_the_next_check_goes_ahead(tmp_path):
    store, identity = _watch(tmp_path)
    t = time.time() + 10000
    first = enqueue_due(store, t)[0]
    _finish(store, first, "NOTHING_NEW: checked the Gmail inbox")
    second = enqueue_due(store, t + 901)
    assert len(second) == 1 and store.get(first)["state"] == "completed"
    assert "closed automatically" in store.get(first)["detail"]


def test_a_watch_with_a_finding_still_waits_for_the_owner(tmp_path):
    store, identity = _watch(tmp_path)
    t = time.time() + 10000
    first = enqueue_due(store, t)[0]
    _finish(store, first, "GST portal mailed: notice dated 19 Nov")
    assert enqueue_due(store, t + 901) == [] and store.get(first)["state"] == "ready_for_review"


def test_an_ordinary_routine_never_closes_itself_even_if_it_says_nothing_new(tmp_path):
    store, identity = setup(tmp_path); enable(store, identity, True)
    t = time.time() + 10000
    first = enqueue_due(store, t)[0]
    _finish(store, first, "NOTHING_NEW")
    assert enqueue_due(store, t + 61) == [] and store.get(first)["state"] == "ready_for_review"


def test_an_old_ledger_gains_the_watch_column(tmp_path):
    store = GoalStore(tmp_path / "goals.sqlite")
    with store.connect() as db:
        db.execute("""CREATE TABLE goal_schedules (id TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, interval INTEGER NOT NULL, max_runs INTEGER NOT NULL,
            runs INTEGER NOT NULL DEFAULT 0, goal_id TEXT, detail TEXT NOT NULL DEFAULT 'Paused until explicitly enabled')""")
    assert schedules(store) == []
    with store.connect() as db:
        assert "watch" in {row["name"] for row in db.execute("PRAGMA table_info(goal_schedules)")}
