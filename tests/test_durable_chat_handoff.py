from __future__ import annotations

from smara import api
from smara.models import ChatRequest


def test_durable_chat_handoff_starts_safe_hosted_planning_before_local_approval(monkeypatch):
    captured: dict = {}

    class Store:
        def create(self, account_id, workspace_id, title, objective, requires_approval, steps):
            captured.update({
                "account_id": account_id,
                "workspace_id": workspace_id,
                "title": title,
                "objective": objective,
                "requires_approval": requires_approval,
                "steps": steps,
            })
            return {"id": "task_plan"}

    monkeypatch.setattr(api, "store", Store())
    task, message = api._queue_durable_chat_task(
        ChatRequest(message="Create a PDF report and save it in my local workspace."),
        "acct_1",
    )

    assert task == {"id": "task_plan"}
    assert captured["account_id"] == "acct_1"
    assert captured["requires_approval"] is False
    assert captured["steps"] == [{"name": "agent.execute", "executor_kind": "hosted"}]
    assert "approve" in message.lower()
