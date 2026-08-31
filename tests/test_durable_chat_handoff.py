from __future__ import annotations

import asyncio
from dataclasses import replace

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


def test_direct_chat_routes_local_github_request_to_durable_handoff(monkeypatch):
    captured: dict = {}

    class AsyncStore:
        async def call(self, name, *args):
            captured["persisted"] = (name, args)

    async def _conversation(*_args):
        return "chat_test", [], ""

    monkeypatch.setattr(api, "_conversation", _conversation)
    monkeypatch.setattr(api, "_async_store", lambda: AsyncStore())
    monkeypatch.setattr(api, "settings", replace(api.settings, hosted_user_integrations_enabled=False))

    def _queue(body, user):
        captured["queued"] = (body.message, user)
        return {"id": "task_github"}, "I created an approval-gated desktop task."

    monkeypatch.setattr(api, "_queue_durable_chat_task", _queue)
    response = asyncio.run(api.chat(ChatRequest(message="List my GitHub repositories"), "acct_1"))

    assert response.message == "I created an approval-gated desktop task."
    assert captured["queued"] == ("List my GitHub repositories", "acct_1")
    assert captured["persisted"][0] == "append_conversation_exchange"
