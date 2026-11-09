"""Agent roster store: synthetic settings files only, no runtime, no network."""
import json
import pytest
from smara import native_management as management
from smara import native_profiles


@pytest.fixture
def setup(tmp_path, monkeypatch):
    state = tmp_path / "desktop.json"
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(tmp_path / "credentials.json"))
    monkeypatch.setattr(management, "protect", lambda value: "synthetic-protected:" + value)
    monkeypatch.setattr(native_profiles, "unprotect", lambda value: value.removeprefix("synthetic-protected:"))
    state.write_text(json.dumps({"history": "preserve", "workspace": str(tmp_path)}))
    return tmp_path, state


def test_bootstrap_lists_four_named_presets_with_kiran_as_default(setup):
    value = management.manage({"operation": "bootstrap"})
    roster = value["agents"]["roster"]
    assert [agent["name"] for agent in roster] == ["Kiran", "Rohan", "Scout", "Nisha"]
    assert all(agent["preset"] for agent in roster)
    assert value["agents"]["default"] == "kiran"
    assert not {agent["name"] for agent in roster} & {"Munim", "Vasooli", "Browser", "Files"}


def test_presets_carry_only_the_fields_that_do_something(setup):
    value = management.manage({"operation": "bootstrap"})
    for agent in value["agents"]["roster"]:
        assert set(agent) == {"id", "name", "role", "icon", "instructions", "preset"}, agent


def test_save_custom_agent_keeps_unrelated_settings(setup):
    _, state = setup
    value = management.manage({"operation": "agents", "action": "save", "agent": {
        "name": "Meena", "role": "Stock checks", "icon": "📦", "instructions": "Check stock against the sheet."}})
    custom = [agent for agent in value["agents"]["roster"] if not agent["preset"]]
    assert len(custom) == 1 and custom[0]["name"] == "Meena" and custom[0]["instructions"] == "Check stock against the sheet."
    saved = json.loads(state.read_text())
    assert saved["history"] == "preserve" and saved["workspace"] == str(setup[0])


@pytest.mark.parametrize("field, value", [
    ("folders", ["/elsewhere"]), ("origins", ["https://example.com"]), ("terminal", True),
    ("memory_scope", "personal"), ("model_profile", "local:glm"), ("api_key", "synthetic"),
])
def test_permission_and_secret_fields_are_refused_not_stored(setup, field, value):
    # Per-agent permissions are a later phase: a field that does nothing must not be accepted or stored.
    with pytest.raises(ValueError, match="Invalid agent"):
        management.manage({"operation": "agents", "action": "save", "agent": {"name": "Ravi", field: value}})


def test_instructions_are_length_limited(setup):
    with pytest.raises(ValueError, match="at most 1500"):
        management.manage({"operation": "agents", "action": "save", "agent": {"name": "Ravi", "instructions": "x" * 1501}})


def test_editing_a_preset_keeps_it_a_preset_and_delete_restores_the_default(setup):
    management.manage({"operation": "agents", "action": "save", "agent": {
        "id": "scout", "name": "Scout", "role": "Web tasks", "instructions": "Only public sites."}})
    value = management.manage({"operation": "bootstrap"})
    scout = next(agent for agent in value["agents"]["roster"] if agent["id"] == "scout")
    assert scout["instructions"] == "Only public sites." and scout["preset"] is True
    value = management.manage({"operation": "agents", "action": "delete", "agent_id": "scout"})
    scout = next(agent for agent in value["agents"]["roster"] if agent["id"] == "scout")
    assert "Only public sites" not in scout["instructions"]


def test_delete_custom_agent_also_drops_its_chat_assignments(setup):
    management.manage({"operation": "agents", "action": "save", "agent": {"id": "meena", "name": "Meena"}})
    management.manage({"operation": "agents", "action": "assign", "thread_id": "thread-1", "agent_id": "meena"})
    value = management.manage({"operation": "agents", "action": "delete", "agent_id": "meena"})
    assert all(agent["id"] != "meena" for agent in value["agents"]["roster"])
    assert "thread-1" not in value["agents"]["assignments"]


def test_chats_are_assigned_to_agents_and_bad_ids_are_refused(setup):
    value = management.manage({"operation": "agents", "action": "assign", "thread_id": "thread-1", "agent_id": "rohan"})
    assert value["agents"]["assignments"] == {"thread-1": "rohan"}
    with pytest.raises(ValueError, match="Unknown agent"):
        management.manage({"operation": "agents", "action": "assign", "thread_id": "thread-2", "agent_id": "nobody"})
    with pytest.raises(ValueError, match="Invalid chat id"):
        management.manage({"operation": "agents", "action": "assign", "thread_id": "../x", "agent_id": "rohan"})
    with pytest.raises(ValueError, match="Unsupported agent action"):
        management.manage({"operation": "agents", "action": "run", "agent_id": "rohan"})


def test_assignments_are_capped_so_the_file_cannot_grow_without_limit(setup, monkeypatch):
    from smara import native_agents
    monkeypatch.setattr(native_agents, "MAX_THREAD_ASSIGNMENTS", 3)
    for i in range(5):
        value = management.manage({"operation": "agents", "action": "assign", "thread_id": f"t-{i}", "agent_id": "kiran"})
    assert sorted(value["agents"]["assignments"]) == ["t-2", "t-3", "t-4"]
