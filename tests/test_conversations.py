from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from smara.agent_runtime import SmaraAgentRuntime
from smara.store import TaskStore


class HistoryProvider:
    def __init__(self):
        self.system = ""

    async def complete(self, *, system: str, message: str) -> str:
        self.system = system
        return "I remember the project context."


def test_conversation_exchange_is_ordered_bounded_and_account_scoped(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    store.append_conversation_exchange("chat_1", "acct_1", "work", "My project is Atlas.", "Understood.", "model-1")
    store.append_conversation_exchange("chat_1", "acct_1", "work", "What is it called?", "Atlas.", "model-1")

    history = store.conversation_history("chat_1", "acct_1", "work")
    assert [(turn["sequence"], turn["role"]) for turn in history] == [
        (1, "user"), (2, "assistant"), (3, "user"), (4, "assistant")
    ]
    assert history[-1]["content"] == "Atlas."
    assert len(store.conversation_history("chat_1", "acct_1", "work", max_chars=1_000)) == 4
    with pytest.raises(KeyError):
        store.conversation_history("chat_1", "acct_2", "work")
    with pytest.raises(KeyError):
        store.conversation_history("chat_1", "acct_1", "private")


def test_runtime_receives_recent_conversation_context():
    provider = HistoryProvider()
    runtime = SmaraAgentRuntime(provider)
    turn = asyncio.run(runtime.chat(
        account_id="acct_1",
        workspace_id="work",
        message="What is the project?",
        conversation_id="chat_1",
        conversation_history=[
            {"role": "user", "content": "My project is Atlas."},
            {"role": "assistant", "content": "Understood."},
        ],
    ))
    assert turn.message == "I remember the project context."
    assert "User: My project is Atlas." in provider.system
    assert "Smara: Understood." in provider.system


def test_long_conversation_compacts_older_turns_into_bounded_summary(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    for number in range(18):
        store.append_conversation_exchange(
            "chat_long", "acct_1", "work", f"question {number}", f"answer {number}"
        )
    summary = store.conversation_summary("chat_long", "acct_1", "work")
    recent = store.conversation_history("chat_long", "acct_1", "work", limit=40)
    assert "question 0" in summary and "answer 8" in summary
    assert len(summary) <= 8_000
    assert recent[0]["sequence"] == 19
    assert recent[-1]["sequence"] == 36


def test_cli_device_can_be_listed_and_revoked_without_storing_jti(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    store.register_cli_device("acct_1", "Laptop", "secret-jti", expires)

    assert store.cli_device_active("acct_1", "secret-jti") is True
    devices = store.cli_devices("acct_1")
    assert len(devices) == 1 and devices[0]["name"] == "Laptop"
    assert "secret-jti" not in str(devices)
    store.revoke_cli_device("acct_1", devices[0]["id"])
    assert store.cli_device_active("acct_1", "secret-jti") is False
    with pytest.raises(KeyError):
        store.revoke_cli_device("acct_2", devices[0]["id"])


def test_account_deletion_removes_conversations_and_cli_devices(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    store.append_conversation_exchange("chat_1", "acct_1", "work", "hello", "hi")
    store.register_cli_device("acct_1", "Laptop", "jti", expires)
    store.delete_account("acct_1")
    assert store.conversations("acct_1") == []
    assert store.cli_devices("acct_1") == []
