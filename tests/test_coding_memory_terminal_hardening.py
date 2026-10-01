from pathlib import Path

import pytest

from smara.dual_plane_memory import DualPlaneMemoryBridge
from smara.local_syntarus import LocalSyntarus
from smara.local_syntarus import coding_context_for_turn
from smara.persistent_terminal import PersistentTerminalStore


@pytest.mark.parametrize("text", ["x" * 40000, "🙂हैलो" * 5000], ids=["long-ascii", "long-unicode"])
def test_terminal_poll_does_not_skip_long_unicode_output(tmp_path, text):
    log = tmp_path / "output.log"
    log.write_bytes(text.encode())
    store = PersistentTerminalStore(tmp_path)
    entry = {"log_path": str(log), "offset": 0}
    parts = []
    while entry["offset"] < log.stat().st_size:
        piece = store._read_output(entry, max_chars=73)
        assert piece
        assert len(piece) <= 73
        parts.append(piece)
    assert "".join(parts) == text


def test_memory_has_no_shared_key_or_invented_bootstrap(tmp_path, monkeypatch):
    monkeypatch.delenv("SYNTARUS_API_KEY", raising=False)
    monkeypatch.delenv("SMARA_USER_ID", raising=False)
    bridge = DualPlaneMemoryBridge(tmp_path)
    assert bridge.list_facts() == []
    assert bridge.coding_engine.adr_manager.list_adrs() == []
    assert bridge._resolve_continuum_config()[1] is None
    assert bridge.get_status().plane_2_continuum.status == "unconfigured"
    assert not bridge.sync_to_continuum()["success"]


def test_memory_user_and_workspace_scoping(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTARUS_API_KEY", "fixture-private-key")
    monkeypatch.setenv("SMARA_USER_ID", "user-one")
    first = LocalSyntarus(tmp_path / "one")
    second = LocalSyntarus(tmp_path / "two")
    assert first.user_id == second.user_id == "user-one"
    assert first.agent_id != second.agent_id
    monkeypatch.setenv("SYNTARUS_API_KEY", "sk_mem_community")
    assert not LocalSyntarus(tmp_path).configured


def test_local_recall_not_mislabeled_as_cloud(tmp_path, monkeypatch):
    monkeypatch.delenv("SYNTARUS_API_KEY", raising=False)
    bridge = DualPlaneMemoryBridge(tmp_path)
    bridge.remember_fact("Tests", "Use pytest for release verification")
    result = bridge.recall("pytest")
    assert not result.continuum_memories
    assert "Local saved notes" in result.fused_context
    assert "Use pytest" in result.fused_context


def test_coding_recall_never_calls_cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTARUS_API_KEY", "private-fixture")
    monkeypatch.setenv("SMARA_USER_ID", "private-user")
    monkeypatch.setattr(LocalSyntarus, "search", lambda *a, **k: pytest.fail("Coding prompts must stay local"))
    bridge = DualPlaneMemoryBridge(tmp_path)
    bridge.remember_fact("Tests", "Use pytest for release verification")
    assert "pytest" in coding_context_for_turn(tmp_path, "pytest")


def test_sdk_memory_receipts_and_workspace_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTARUS_API_KEY", "private-fixture")
    monkeypatch.setenv("SMARA_USER_ID", "private-user")
    calls = []
    class Client:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def search(self, query, **kwargs):
            calls.append(kwargs)
            return {"context": "Remember the verified test command"}
        def add(self, **kwargs):
            calls.append(kwargs)
            return {"event_id": "queued-event"}
    adapter = LocalSyntarus(tmp_path)
    monkeypatch.setattr(adapter, "client", lambda: Client())
    assert adapter.search("test command") == ["Remember the verified test command"]
    assert adapter.add("Tests", "pytest", "note") == {"event_id": "queued-event"}
    assert all(call["user_id"] == "private-user" and call["agent_id"] == adapter.agent_id for call in calls)
    assert calls[-1]["idempotency_key"].startswith("smara-memory-")
