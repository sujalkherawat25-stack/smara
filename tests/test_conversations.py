from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from smara.agent_runtime import SmaraAgentRuntime
from smara.profile_memory import explicit_profile_facts, profile_context, profile_summary
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


def test_explicit_profile_facts_survive_a_fresh_conversation_and_remain_scoped(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    facts = explicit_profile_facts("My name is Sujal. I prefer concise reports.")
    assert facts == {"preferred_name": "Sujal", "stated_preference": "concise reports"}
    store.remember_account_facts("acct_1", "default", facts)
    assert store.account_memory_facts("acct_1", "other") == {}
    context = profile_context(store.account_memory_facts("acct_1", "default"))
    assert "preferred name is Sujal" in context

    provider = HistoryProvider()
    turn = asyncio.run(SmaraAgentRuntime(provider).chat_with_tools(
        account_id="acct_1",
        workspace_id="default",
        message="Do you know me?",
        conversation_id="new_after_restart",
        durable_profile_context=context,
    ))
    assert turn.memory_used is True
    assert "preferred name is Sujal" in provider.system


def test_profile_extractor_does_not_treat_a_two_letter_acknowledgement_as_a_name():
    assert "preferred_name" not in explicit_profile_facts("so I am sk")


def test_profile_extractor_never_interprets_an_i_am_statement_as_a_name():
    assert "preferred_name" not in explicit_profile_facts("I am Indian, remember me")


def test_profile_summary_only_uses_verified_account_or_explicit_facts(tmp_path):
    store = TaskStore(str(tmp_path / "smara.db"))
    store.remember_account_facts("acct_1", "default", {"preferred_name": "bad inferred phrase"})
    assert store.forget_account_fact("acct_1", "default", "preferred_name") is True
    summary = profile_summary({"account_display_name": "Sujal Kherawat"})
    assert "Sujal Kherawat" in summary
    assert "bad inferred phrase" not in summary


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
    store.remember_account_facts("acct_1", "work", {"preferred_name": "Sujal"})
    store.delete_account("acct_1")
    assert store.conversations("acct_1") == []
    assert store.cli_devices("acct_1") == []
    assert store.account_memory_facts("acct_1", "work") == {}
