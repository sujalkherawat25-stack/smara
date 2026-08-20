from pathlib import Path

from smara.store import TaskStore


def test_integration_policy_and_idempotency_are_account_scoped(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    store.configure_integration("acct_1", "gmail", display_name="Work Gmail", policy="observe", granted_scopes=["gmail.readonly"], health="healthy")
    blocked = store.request_integration_action("acct_1", "gmail", "gmail.send", "Send report to client", "send-report-001")
    assert blocked["risk"] == "external"
    assert blocked["status"] == "blocked"
    same = store.request_integration_action("acct_1", "gmail", "gmail.send", "Different text must not duplicate", "send-report-001")
    assert same["id"] == blocked["id"]
    assert store.integrations("acct_2") == []


def test_trusted_does_not_bypass_external_approval_before_executor_exists(tmp_path: Path):
    store = TaskStore(str(tmp_path / "smara.db"))
    store.configure_integration("acct_1", "github", display_name="Repo", policy="trusted", granted_scopes=["repo"], health="healthy")
    action = store.request_integration_action("acct_1", "github", "github.push", "Push a reviewed commit", "push-commit-001")
    assert action["status"] == "awaiting_approval"
