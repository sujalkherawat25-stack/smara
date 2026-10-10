from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest


def test_legacy_calculator_uses_bounded_numeric_ast_only():
    from smara.agent_tools import calculate

    assert calculate("(12 + 8) * 3") == "60"
    assert calculate("sqrt(81) + pi") == str(9 + 3.141592653589793)
    assert calculate("2 ^ 5") == "32"
    assert "Math Error" in calculate("__import__('os').getcwd()")
    assert "Math Error" in calculate("2 ** 13")
    assert "Math Error" in calculate("9" * 501)


def test_dynamic_python_tools_are_disabled_without_explicit_unsafe_opt_in(tmp_path, monkeypatch):
    from smara.tool_synthesis import DynamicToolError, DynamicToolSynthesizer

    monkeypatch.delenv("SMARA_UNSAFE_ALLOW_DYNAMIC_PYTHON", raising=False)
    tools = DynamicToolSynthesizer(tmp_path)
    code = "def run(payload):\n    return {'value': payload.get('value')}\n"

    with pytest.raises(DynamicToolError, match="not sandboxed"):
        tools.synthesize_tool("echo_value", "Return a value", code)

    smoke_ok, _, smoke_message = tools.smoke_test(code, {"value": "safe fixture"})
    assert smoke_ok is False
    assert "not sandboxed" in smoke_message
    result = tools.execute_dynamic_tool("echo_value", {})
    assert result["status"] == "failed"
    assert "not sandboxed" in result["error"]
    assert tools.list_dynamic_tools() == []


def test_dynamic_python_compatibility_requires_and_honors_explicit_opt_in(tmp_path, monkeypatch):
    from smara.tool_synthesis import DynamicToolSynthesizer

    monkeypatch.setenv("SMARA_UNSAFE_ALLOW_DYNAMIC_PYTHON", "1")
    tools = DynamicToolSynthesizer(tmp_path)
    code = "def run(payload):\n    return {'value': payload.get('value')}\n"
    tools.synthesize_tool("echo_value", "Return a value", code, sample_payload={"value": "fixture"})

    result = tools.execute_dynamic_tool("echo_value", {"value": "checked"})
    assert result["status"] == "success"
    assert result["result"] == {"value": "checked"}


def test_legacy_python_bridge_is_disabled_without_the_same_explicit_opt_in(monkeypatch):
    from smara.desktop_executor import _main

    monkeypatch.delenv("SMARA_UNSAFE_ALLOW_DYNAMIC_PYTHON", raising=False)
    with pytest.raises(SystemExit, match="executes arbitrary code"):
        _main(["--python-bridge"])


def test_distributables_exclude_legacy_execution_modules():
    root = Path(__file__).resolve().parents[1]
    required = {
        "agent_tools.py", "autonomous_agent.py", "cli.py", "desktop_executor.py",
        "local_agent_runtime.py", "self_healing.py", "swarm.py", "tool_synthesis.py",
    }
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    wheel_exclusions = {
        Path(path).name
        for path in project["tool"]["hatch"]["build"]["targets"]["wheel"]["exclude"]
    }
    assert required <= wheel_exclusions

    build_script = (root / "scripts" / "build-smara-desktop.ps1").read_text(encoding="utf-8")
    script_exclusions = set(re.findall(r"^\s*'(smara\.[^']+)'\s*$", build_script, re.MULTILINE))
    assert {f"smara.{name.removesuffix('.py')}" for name in required} <= script_exclusions

    cli_script = (root / "scripts" / "build-smara-cli.ps1").read_text(encoding="utf-8")
    cli_exclusions = set(re.findall(r"'((?:smara\.)[^']+)'", cli_script))
    assert {f"smara.{name.removesuffix('.py')}" for name in required} <= cli_exclusions


def test_release_version_is_aligned_across_installers():
    root = Path(__file__).resolve().parents[1]
    version = (root / "release" / "VERSION").read_text(encoding="utf-8").strip()
    project_version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    desktop_version = json.loads((root / "apps" / "desktop" / "package.json").read_text(encoding="utf-8"))["version"]
    lock_version = json.loads((root / "apps" / "desktop" / "package-lock.json").read_text(encoding="utf-8"))["version"]
    tauri_version = json.loads((root / "apps" / "desktop" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    cargo_packages = tomllib.loads((root / "apps" / "desktop" / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8"))["package"]
    cargo_version = next(item["version"] for item in cargo_packages if item["name"] == "smara-desktop")
    assert {version, project_version, desktop_version, lock_version, tauri_version, cargo_version} == {version}
