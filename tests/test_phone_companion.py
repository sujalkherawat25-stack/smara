from pathlib import Path

from smara.store import TaskStore


def test_capture_and_push_subscription_are_account_scoped(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    capture = store.create_capture("acct_1", "text", "Phone note", "Remember this safe idea")
    assert capture["task"]["workspace_id"] == "inbox"
    assert capture["artifact"]["kind"].startswith("capture:text")
    store.save_push_subscription("acct_1", "https://push.example/subscription", "p256dh-key", "auth-key")
    assert len(store.push_subscriptions("acct_1")) == 1
    assert store.push_subscriptions("acct_2") == []
