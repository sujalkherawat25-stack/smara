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
