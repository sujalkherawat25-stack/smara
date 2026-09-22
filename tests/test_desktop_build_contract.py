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

    assert "--paths', 'src\\smara'" in script
    assert "pip install" not in script
