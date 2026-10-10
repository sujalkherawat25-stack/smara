"""Synthetic settings/vault tests, not live-provider acceptance."""
import json
import pytest
from smara import native_management as management
from smara import native_profiles


@pytest.fixture
def setup(tmp_path, monkeypatch):
    state = tmp_path / "desktop.json"
    vault = tmp_path / "credentials.json"
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(vault))
    monkeypatch.delenv("SMARA_SEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("SMARA_SEARCH_URL", raising=False)
    monkeypatch.setattr(management, "protect", lambda value: "synthetic-protected:" + value)
    monkeypatch.setattr(native_profiles, "unprotect", lambda value: value.removeprefix("synthetic-protected:"))
    state.write_text(json.dumps({"history": "preserve", "workspace": str(tmp_path)}))
    return tmp_path, state, vault


def test_bootstrap_preserves_original_state_and_hides_unknown_model_fields(setup):
    root, state, _ = setup
    original = {"workspace": str(root), "model_profiles": [{"id": "old", "api_key": "synthetic-sensitive", "label": "Old"}], "history": [1, 2]}
    state.write_text(json.dumps(original))
    value = management.manage({"operation": "bootstrap"})
    assert json.loads(state.read_text()) == original
    assert "synthetic-sensitive" not in json.dumps(value)
    assert value["preferences"]["projects"] == [{"workspace": str(root), "label": root.name}]


def test_project_registration_canonical_dedup_and_no_cross_project_access(setup):
    root, state, _ = setup
    second = root / "project स्मारा 😀"
    second.mkdir()
    first = management.manage({"operation": "add_project", "workspace": str(second), "label": "Coding"})
    assert first["preferences"]["workspace"] == str(second.resolve())
    second_result = management.manage({"operation": "add_project", "workspace": str(second / "."), "label": "Duplicate"})
    assert len(second_result["preferences"]["projects"]) == 2
    not_registered = root / "not registered"
    not_registered.mkdir()
    with pytest.raises(ValueError, match="Add this folder"):
        management.manage({"operation": "select_project", "workspace": str(not_registered)})
    assert json.loads(state.read_text())["history"] == "preserve"


def test_model_and_credential_are_saved_separately_blank_key_edit_reuses_vault(setup):
    root, _, vault = setup
    profile = {"id": "test", "label": "Synthetic", "provider": "local", "model": "synthetic", "base_url": "http://127.0.0.1:9999/v1", "api_key": "synthetic-sensitive"}
    result = management.manage({"operation": "save_model", "profile": profile})
    assert "synthetic-sensitive" not in (root / "native-settings.json").read_text()
    assert "api_key" not in result["profiles"][0]
    saved_vault = vault.read_bytes()
    profile.update({"label": "Updated", "api_key": ""})
    result = management.manage({"operation": "save_model", "profile": profile})
    assert result["profiles"][0]["label"] == "Updated"
    assert saved_vault == vault.read_bytes()
    management.manage({"operation": "select_model", "id": "test"})
    assert native_profiles.load_profiles()[1] == "test"


@pytest.mark.parametrize("endpoint", ["https://user:synthetic-secret@example.com/v1", "https://example.com/?key=synthetic", "http://example.com/v1", "file:///private"])
def test_invalid_model_endpoints_do_not_write_credentials(setup, endpoint):
    _, _, vault = setup
    with pytest.raises(ValueError):
        management.manage({"operation": "save_model", "profile": {"id": "a", "label": "a", "provider": "a", "model": "a", "base_url": endpoint, "api_key": "synthetic"}})
    assert not vault.exists()


def test_unreadable_case_alias_does_not_mask_readable_alias(monkeypatch):
    def decrypt(value):
        if value == "bad": raise ValueError("invalid DPAPI blob")
        return "synthetic-readable"
    monkeypatch.setattr(native_profiles, "unprotect", decrypt)
    assert native_profiles.resolve_credential("CaseKey", {"CaseKey": {"protected": "bad"}, "CASEKEY": {"protected": "good"}}) == "synthetic-readable"


def test_legacy_operation_is_rejected(setup):
    with pytest.raises(ValueError, match="legacy execution"):
        management.manage({"operation": "run_terminal_command"})


def test_edit_preserves_model_context_window(setup):
    root, _, _ = setup
    profile = {"id": "test", "label": "Synthetic", "provider": "local", "model": "synthetic", "base_url": "http://127.0.0.1:9/v1", "api_key": "synthetic", "context_window": 200000}
    management.manage({"operation": "save_model", "profile": profile})
    profile.pop("context_window")
    profile["api_key"] = ""
    result = management.manage({"operation": "save_model", "profile": profile})
    assert result["profiles"][0]["context_window"] == 200000


def test_metadata_save_failure_restores_previous_protected_model_key(setup, monkeypatch):
    _, _, vault = setup
    profile = {"id": "test", "label": "Synthetic", "provider": "local", "model": "synthetic", "base_url": "http://127.0.0.1:9/v1", "api_key": "synthetic-old"}
    management.manage({"operation": "save_model", "profile": profile})
    original_vault = json.loads(vault.read_text())
    original_settings = management.settings_path().read_bytes()
    write = management.write_object
    def fail_metadata(path, value):
        if path == management.settings_path():
            raise OSError("Synthetic disk failure")
        write(path, value)
    monkeypatch.setattr(management, "write_object", fail_metadata)
    profile["api_key"] = "synthetic-new"
    with pytest.raises(OSError, match="Synthetic disk failure"):
        management.manage({"operation": "save_model", "profile": profile})
    assert json.loads(vault.read_text()) == original_vault
    assert management.settings_path().read_bytes() == original_settings


def test_remove_exact_lowercase_legacy_credential_alias(setup):
    _, _, vault = setup
    vault.write_text(json.dumps({"legacy_key": {"protected": "synthetic"}, "LEGACY_KEY": {"protected": "synthetic-other"}}))
    management.manage({"operation": "delete_credential", "name": "legacy_key"})
    assert json.loads(vault.read_text()) == {"LEGACY_KEY": {"protected": "synthetic-other"}}


def test_key_rotation_uses_new_alias_and_does_not_copy_legacy_plaintext_fields(setup):
    root, state, vault = setup
    old = {"id": "test", "label": "Synthetic", "provider": "local", "model": "synthetic", "base_url": "http://127.0.0.1:9/v1", "credential_name": "legacy_key", "auth_header": "authorization"}
    other = {**old, "id": "other", "api_key": "synthetic-do-not-copy"}
    state.write_text(json.dumps({"model_profiles": [old, other], "workspace": str(root)}))
    vault.write_text(json.dumps({"legacy_key": {"protected": "synthetic-protected:old"}}))
    result = management.manage({"operation": "save_model", "profile": {**old, "api_key": "synthetic-new"}})
    updated = next(p for p in result["profiles"] if p["id"] == "test")
    assert updated["credential_name"] == "LEGACY_KEY"
    assert native_profiles.resolve_profile_key(updated, json.loads(vault.read_text())) == "synthetic-new"
    assert "synthetic-do-not-copy" not in management.settings_path().read_text()


def test_saving_search_key_selects_provider_and_keeps_secret_out_of_settings(setup):
    root, _, vault = setup
    result = management.manage({"operation": "save_credential", "name": "TAVILY_API_KEY",
        "provider": "tavily", "secret": "synthetic-tavily-secret"})
    assert result["preferences"]["search_provider"] == "tavily"
    assert result["search"]["configured"] is True
    assert "synthetic-tavily-secret" not in management.settings_path().read_text()
    assert "synthetic-tavily-secret" not in json.dumps(result)
    assert json.loads(vault.read_text())["TAVILY_API_KEY"]["provider"] == "tavily"


def test_search_key_alias_must_match_provider_before_vault_write(setup):
    _, _, vault = setup
    with pytest.raises(ValueError, match="does not match"):
        management.manage({"operation": "save_credential", "name": "EXA_API_KEY",
            "provider": "tavily", "secret": "synthetic-secret"})
    assert not vault.exists()


def test_search_provider_metadata_failure_rolls_back_new_key(setup, monkeypatch):
    _, _, vault = setup
    original_write = management.write_object
    def fail_settings(path, value):
        if path == management.settings_path():
            raise OSError("synthetic metadata failure")
        original_write(path, value)
    monkeypatch.setattr(management, "write_object", fail_settings)
    with pytest.raises(OSError, match="metadata failure"):
        management.manage({"operation": "save_credential", "name": "TAVILY_API_KEY",
            "provider": "tavily", "secret": "synthetic-secret"})
    assert not vault.exists() or json.loads(vault.read_text()) == {}
