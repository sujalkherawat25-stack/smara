"""Offline bootstrap dispatch tests; not a native/model success substitute."""
import io
from pathlib import Path

import pytest

from smara import native_runtime


def test_protocol_stdio_preserves_utf8_despite_windows_codepages():
    expected = "स्मारा 😀 → 日本語"
    source = io.TextIOWrapper(io.BytesIO((expected + "\n").encode("utf-8")), encoding="cp1252")
    raw = io.BytesIO()
    destination = io.TextIOWrapper(raw, encoding="cp1252")
    native_runtime.configure_protocol_stdio(source, destination)
    assert source.readline() == expected + "\n"
    destination.write(expected + "\n")
    destination.flush()
    assert raw.getvalue().decode("utf-8") == expected + "\n"


def test_protocol_stdio_leaves_explicit_text_streams_usable():
    source, destination = io.StringIO("synthetic\n"), io.StringIO()
    native_runtime.configure_protocol_stdio(source, destination)
    destination.write(source.readline())
    assert destination.getvalue() == "synthetic\n"


def test_settings_output_is_utf8_with_unicode_project_paths(monkeypatch):
    import json
    from smara import native_management
    value = {"preferences": {"workspace": "स्मारा 😀 → 日本語"}}
    monkeypatch.setattr(native_management, "bootstrap", lambda: value)
    source = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    raw = io.BytesIO()
    destination = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(native_runtime.sys, "stdin", source)
    monkeypatch.setattr(native_runtime.sys, "stdout", destination)
    assert native_runtime.main(["settings"]) == 0
    destination.flush()
    assert json.loads(raw.getvalue().decode("utf-8")) == value


@pytest.mark.parametrize("option", ["--help", "-h"])
def test_help_documents_smara_integrations_without_profile_or_inference(tmp_path, monkeypatch, capsys, option):
    calls = []
    monkeypatch.setattr(native_runtime, "native_binary", lambda: Path("owned-native.exe"))
    monkeypatch.setattr(native_runtime, "runtime_home", lambda: tmp_path / "home")
    monkeypatch.setattr(native_runtime.subprocess, "call", lambda argv, **kwargs: calls.append((argv, kwargs)) or 0)
    monkeypatch.setattr("smara.native_profiles.load_profiles", lambda: pytest.fail("Offline help read model profiles"))
    assert native_runtime.main([option]) == 0
    assert calls[0][0] == ["owned-native.exe", option]
    output = capsys.readouterr().out
    for command in ("source-status", "--smara-tools", "schedule --help", "tools-serve", "settings"):
        assert command in output
    assert "smara legacy" not in output
    assert "not an installed Codex" in output


def test_help_preserves_a_native_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(native_runtime, "native_binary", lambda: Path("owned-native.exe"))
    monkeypatch.setattr(native_runtime, "runtime_home", lambda: tmp_path / "home")
    monkeypatch.setattr(native_runtime.subprocess, "call", lambda *args, **kwargs: 9)
    assert native_runtime.main(["--help"]) == 9
    assert "Smara integration commands" not in capsys.readouterr().out


def test_version_is_not_replaced_with_product_release_number(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(native_runtime, "native_binary", lambda: Path("owned-native.exe"))
    monkeypatch.setattr(native_runtime, "runtime_home", lambda: tmp_path / "home")
    monkeypatch.setattr(native_runtime.subprocess, "call", lambda *args, **kwargs: 0)
    assert native_runtime.main(["--version"]) == 0
    assert capsys.readouterr().out == ""


def test_old_engine_is_not_dispatchable_from_primary_cli(monkeypatch, capsys):
    monkeypatch.setattr(native_runtime, "native_binary", lambda: pytest.fail("Retired command launched a runtime"))
    assert native_runtime.main(["legacy", "anything"]) == 1
    assert "old execution engine is retired" in capsys.readouterr().err
