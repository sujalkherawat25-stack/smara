"""Regression coverage for governed procedural memory and integration lifecycle."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from smara.plugins import PluginManager
from smara.mcp_client import _remote_url_allowed
from smara.skills_system import SkillLifecycleManager


GOOD_GATE = {
    "passed": True, "overall_rate": 1.0, "category_rates": {"held_out": 1.0},
    "false_completions": 0, "safety_violations": 0, "reproducible": True,
    "independent_runs": 3, "triggers": ["weather", "forecast"],
}


def _skill(workspace: Path, name: str, version: str, body: str = "Read sources and summarize evidence.") -> None:
    path = workspace / ".smara" / "skills" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\nversion: {version}\nrisk: read_only\n---\n\n{body}\n",
        encoding="utf-8",
    )


def test_versioned_skill_validation_automatic_reuse_and_rollback(tmp_path: Path):
    manager = SkillLifecycleManager(tmp_path)
    _skill(tmp_path, "weather-research", "1.0.0")
    manager.submit_candidate("weather-research", version="1.0.0", risk="read_only", automatic_reuse=True, gate=GOOD_GATE)
    first = manager.promote("weather-research")
    assert first["version"] == "1.0.0"
    assert manager.reuse_decision("weather-research", "weather forecast") ["eligible"] is True

    _skill(tmp_path, "weather-research", "1.1.0")
    manager.submit_candidate("weather-research", version="1.1.0", risk="read_only", automatic_reuse=True, gate=GOOD_GATE)
    manager.promote("weather-research", version="1.1.0")
    assert manager.list()[0]["active_version"] == "1.1.0"
    assert manager.rollback("weather-research", "1.0.0")["version"] == "1.0.0"
    assert manager.list()[0]["active_version"] == "1.0.0"


def test_skill_validation_blocks_secret_and_automatic_reuse_needs_held_out_runs(tmp_path: Path):
    manager = SkillLifecycleManager(tmp_path)
    _skill(tmp_path, "unsafe-playbook", "1.0.0", "token: sk-this-looks-like-a-secret-123456")
    manager.submit_candidate("unsafe-playbook", version="1.0.0", risk="read_only", automatic_reuse=True, gate=GOOD_GATE)
    with pytest.raises(ValueError, match="validation"):
        manager.promote("unsafe-playbook")

    _skill(tmp_path, "manual-only", "1.0.0")
    weak = {**GOOD_GATE, "independent_runs": 2}
    manager.submit_candidate("manual-only", version="1.0.0", risk="read_only", automatic_reuse=True, gate=weak)
    manager.promote("manual-only")
    assert manager.reuse_decision("manual-only", "weather forecast")["eligible"] is False


def _manifest(name: str, version: str, endpoint: str = "https://mcp.example.test/mcp") -> dict:
    return {"name": name, "version": version, "kind": "mcp", "tools": ["search"], "endpoint": endpoint}


def test_plugin_install_update_rollback_and_health(tmp_path: Path):
    manager = PluginManager(tmp_path)
    first = tmp_path / "first.json"; first.write_text(json.dumps(_manifest("research", "1.0.0")), encoding="utf-8")
    assert manager.install(first)["status"] == "installed"
    assert manager.set_enabled("research", True)["enabled"] is True
    second = tmp_path / "second.json"; second.write_text(json.dumps(_manifest("research", "1.1.0")), encoding="utf-8")
    assert manager.update("research", second)["version"] == "1.1.0"
    assert manager.rollback("research", "1.0.0")["version"] == "1.0.0"

    with patch("smara.mcp_client.MCPRemoteServer.start", return_value=True):
        # Health uses a real transport object but never calls tools/call.
        health = manager.health("research")[0]
    assert health["status"] == "healthy"
    assert health["tool_count"] == 0
    manager.remove("research")
    assert manager.discover() == []


def test_plugin_rejects_non_https_or_non_semver(tmp_path: Path):
    manager = PluginManager(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_manifest("bad", "1", "http://localhost/mcp")), encoding="utf-8")
    with pytest.raises(ValueError):
        manager.install(bad)


def test_plugin_signature_verification_and_untrusted_enable(tmp_path: Path, monkeypatch):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from smara.plugins import PluginManager

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    value = _manifest("signed", "1.0.0")
    value["signing_key"] = "fixture-key"
    value["signature"] = base64.urlsafe_b64encode(key.sign(PluginManager.canonical_manifest(value))).decode().rstrip("=")
    path = tmp_path / "signed.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setenv("SMARA_PLUGIN_TRUST_KEYS", json.dumps({"fixture-key": base64.urlsafe_b64encode(public).decode().rstrip("=")}))
    manager = PluginManager(tmp_path)
    assert manager.install(path)["trust"]["status"] == "verified"
    assert manager.set_enabled("signed", True)["enabled"] is True

    monkeypatch.setenv("SMARA_PLUGIN_TRUST_KEYS", "{}")
    other = tmp_path / "other.json"
    value["name"] = "other"
    value["signature"] = base64.urlsafe_b64encode(key.sign(PluginManager.canonical_manifest(value))).decode().rstrip("=")
    other.write_text(json.dumps(value), encoding="utf-8")
    manager.install(other)
    with pytest.raises(ValueError, match="not trusted"):
        manager.set_enabled("other", True)


def test_remote_mcp_rejects_private_ip_literals():
    assert _remote_url_allowed("https://203.0.113.20/mcp") is False
    assert _remote_url_allowed("https://10.0.0.7/mcp") is False
    assert _remote_url_allowed("https://mcp.example.test/mcp") is True
