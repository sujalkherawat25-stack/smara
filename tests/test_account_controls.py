from pathlib import Path

from smara.store import TaskStore


def test_account_export_is_scoped_and_deletion_removes_smara_data(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    task = store.create("acct_1", "work", "Private", "Keep isolated", False)
    store.create("acct_2", "work", "Other", "Other data", False)
    exported = store.audit_export("acct_1")
    assert [row["id"] for row in exported["tasks"]] == [task["id"]]
    assert "encrypted_secret" not in str(exported)
    store.delete_account("acct_1")
    assert store.list("acct_1") == []
    assert len(store.list("acct_2")) == 1
    with store._connect() as connection:
        for table in ("task_events", "task_steps", "task_runs", "approvals", "artifacts", "research_evidence"):
            assert connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE task_id=?",
                (task["id"],),
            ).fetchone()["count"] == 0
        assert connection.execute(
            "SELECT COUNT(*) AS count FROM executor_leases lease JOIN task_steps step ON step.id=lease.step_id WHERE step.task_id=?",
            (task["id"],),
        ).fetchone()["count"] == 0
