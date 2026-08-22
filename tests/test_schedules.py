from datetime import datetime, timedelta, timezone

from smara.store import TaskStore


def test_due_schedule_creates_one_task_and_advances(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    due = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    schedule = store.create_schedule(
        "acct_1", "default", "Daily brief", "Prepare the brief", 3600, due, True,
        [{"name": "execute_task", "depends_on": []}],
    )

    fired = store.fire_due_schedules()
    assert len(fired) == 1
    assert fired[0]["schedule_id"] == schedule["id"]
    assert store.list("acct_1")[0]["title"] == "Daily brief"
    assert store.fire_due_schedules() == []
    current = store.schedule(schedule["id"], "acct_1")
    assert current["last_task_id"] == fired[0]["task_id"]
    assert current["enabled"] is True


def test_schedule_is_account_scoped_and_can_be_stopped(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    schedule = store.create_schedule("acct_1", "default", "Private", "Do it", 3600, due, False, [{"name": "execute_task", "depends_on": []}])
    assert store.schedules("acct_2") == []
    try:
        store.cancel_schedule(schedule["id"], "acct_2")
    except KeyError:
        pass
    else:
        raise AssertionError("another account must not cancel this schedule")
    store.cancel_schedule(schedule["id"], "acct_1")
    assert store.fire_due_schedules() == []
