import json
import sys
import pytest
from smara.native_profiles import load_profiles, resolve_profile_key, resolve_credential


def test_profiles_do_not_silently_choose_a_default(tmp_path):
    assert load_profiles(tmp_path / "desktop.json") == ([], "", {})


def test_native_reads_selected_profile_without_old_engines(tmp_path):
    (tmp_path / "desktop-ui.json").write_text(json.dumps({"local_model_profiles": [{"id": "synthetic"}], "model_profile": "local:synthetic"}))
    profiles, selected, vault = load_profiles(tmp_path / "desktop.json")
    assert profiles == [{"id": "synthetic"}] and selected == "synthetic" and vault == {}


def test_invalid_profile_file_is_not_ignored(tmp_path):
    (tmp_path / "desktop.json").write_text("[]")
    with pytest.raises(RuntimeError, match="must be an object"):
        load_profiles(tmp_path / "desktop.json")


def test_explicit_credential_alias_and_unreadable_alias_recovery(monkeypatch):
    monkeypatch.setattr("smara.native_profiles.unprotect", lambda _: (_ for _ in ()).throw(OSError("unreadable synthetic alias")))
    credentials = {"EXPLICIT_TEST_KEY": {"protected": "broken"}, "MODEL_API_KEY_SYNTHETIC": "synthetic-secret"}
    assert resolve_profile_key({"id": "synthetic", "credential_name": "EXPLICIT_TEST_KEY"}, credentials) == "synthetic-secret"
    assert resolve_credential("../../private", credentials) == ""


def test_native_profile_reader_does_not_import_legacy_loop():
    # Run isolated from tests that intentionally exercise the legacy engine.
    import subprocess
    result = subprocess.run([sys.executable, "-c", "import sys; import smara.native_profiles; assert 'smara.cli' not in sys.modules; assert 'smara.desktop_executor' not in sys.modules"], capture_output=True)
    assert result.returncode == 0
