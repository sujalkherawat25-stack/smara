from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build-smara-desktop.ps1"


def test_desktop_bundle_excludes_development_only_dependency_stacks() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "--exclude-module" in script
    for module in ("pandas", "pyarrow", "datasets", "pytest", "tkinter", "_tkinter"):
        assert f"'{module}'" in script


def test_desktop_build_freezes_checkout_without_installing_into_shared_venv() -> None:
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "--paths', 'src'" in script
    assert "scripts\\native_desktop_entry.py" in script
    assert "'src\\smara\\desktop_executor.py'" not in script
    assert "pip install" not in script


def test_native_desktop_does_not_compile_or_register_old_execution_bridge() -> None:
    manifest = (REPO_ROOT / "apps/desktop/src-tauri/Cargo.toml").read_text()
    shell = (REPO_ROOT / "apps/desktop/src-tauri/src/native_main.rs").read_text()
    assert 'autobins = false' in manifest and 'path = "src/native_main.rs"' in manifest
    assert 'run_terminal_command' not in shell and 'stream_chat' not in shell
    assert 'secret reads and legacy execution are unavailable' in shell


def test_native_transport_overrides_metadata_stderr_null_before_spawn() -> None:
    transport = (REPO_ROOT / "apps/desktop/src-tauri/src/native_runtime.rs").read_text()
    assert 'command.stdin(Stdio::piped()).stderr(Stdio::piped()).current_dir(&root)' in transport
    assert transport.index('stderr(Stdio::piped())') < transport.index('command.spawn()')
