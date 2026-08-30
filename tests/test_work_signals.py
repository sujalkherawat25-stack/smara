from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from smara.store import TaskStore
from smara.work_signals import WorkSignalBus, wait_for_signal


def test_events_after_uses_durable_cursor_and_enforces_ownership(tmp_path: Path):
    store = TaskStore(str(tmp_path / "signals.db"))
    task = store.create("acct_a", "default", "Signals", "watch", False)
    first = store.events(task["id"], "acct_a")
    assert len(first) == 1
    store.append_event(task["id"], "step.started", '{"step":1}')
    store.append_event(task["id"], "step.completed", '{"step":1}')

    after = store.events_after(task["id"], "acct_a", first[0]["id"])
    assert [event["type"] for event in after] == ["step.started", "step.completed"]
    assert store.events_after(task["id"], "acct_a", after[-1]["id"]) == []
    with pytest.raises(KeyError):
        store.events_after(task["id"], "acct_b", first[0]["id"])


def test_missing_cursor_repairs_by_replaying_current_events(tmp_path: Path):
    store = TaskStore(str(tmp_path / "signals.db"))
    task = store.create("acct_a", "default", "Signals", "watch", False)
    store.append_event(task["id"], "step.started")
    events = store.events_after(task["id"], "acct_a", "missing-cursor")
    assert len(events) == 2


def test_signal_bus_without_redis_is_a_safe_noop():
    WorkSignalBus("").publish("task.created", task_id="task_1")
    assert asyncio.run(wait_for_signal("", timeout=0.05)) is False


def test_task_mutations_emit_advisory_signals(tmp_path: Path):
    class Recorder:
        def __init__(self):
            self.items = []

        def publish(self, kind, *, task_id=None):
            self.items.append((kind, task_id))

    recorder = Recorder()
    store = TaskStore(str(tmp_path / "signals.db"), signal_bus=recorder)
    task = store.create("acct_a", "default", "Signals", "watch", False)
    store.append_event(task["id"], "step.completed")
    assert recorder.items[0] == ("task.created", task["id"])
    assert recorder.items[-1] == ("step.completed", task["id"])


def test_disabled_signal_path_never_opens_redis():
    assert asyncio.run(wait_for_signal("redis://unused", timeout=0.05, enabled=False)) is False
