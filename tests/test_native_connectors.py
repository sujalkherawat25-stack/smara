"""Owner-added connectors: stored safely, every tool asks first, tokens reach only the runtime's environment."""
import json

import pytest

from smara import native_connectors as nc


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(nc, "protect", lambda v: v[::-1])
    monkeypatch.setattr(nc, "unprotect", lambda v: v[::-1])
    return tmp_path


def test_a_web_connector_is_saved_without_its_token_showing_and_reaches_the_runtime_by_environment(store):
    out = nc.save({"label": "Gmail", "kind": "http", "url": "https://mail.example.com/mcp", "token": "s3cret-token"})
    item = out["connectors"][0]
    assert item["id"] == "gmail" and item["has_token"] and "s3cret-token" not in json.dumps(out)
    assert "s3cret-token" not in (store / "connectors.json").read_text()                  # protected at rest
    settings, env = nc.runtime_settings()
    assert settings["mcp_servers.conn_gmail.url"] == "https://mail.example.com/mcp"
    assert settings["mcp_servers.conn_gmail.default_tools_approval_mode"] == "prompt"     # every tool asks the owner
    assert settings["mcp_servers.conn_gmail.required"] is False
    variable = settings["mcp_servers.conn_gmail.bearer_token_env_var"]
    assert env[variable] == "s3cret-token" and "s3cret-token" not in json.dumps(settings)


def test_editing_without_a_token_keeps_the_old_one_and_clear_removes_it(store):
    nc.save({"label": "Notion", "kind": "http", "url": "https://n.example.com/mcp", "token": "t1"})
    nc.save({"id": "notion", "label": "Notion 2", "kind": "http", "url": "https://n.example.com/mcp"})
    assert nc.runtime_settings()[1][nc.env_name("notion", "BEARER")] == "t1"
    nc.save({"id": "notion", "label": "Notion 2", "kind": "http", "url": "https://n.example.com/mcp", "clear_token": True})
    assert nc.runtime_settings()[1] == {}


@pytest.mark.parametrize("url", ["http://evil.example/mcp", "https://user:pw@x.example/", "ftp://x.example", "not a url", ""])
def test_unsafe_addresses_are_refused(store, url):
    with pytest.raises(ValueError):
        nc.save({"label": "X", "kind": "http", "url": url})


def test_local_http_is_allowed_for_development(store):
    nc.save({"label": "Local", "kind": "http", "url": "http://127.0.0.1:8765/mcp"})
    assert nc.runtime_settings()[0]["mcp_servers.conn_local.url"] == "http://127.0.0.1:8765/mcp"


def test_a_local_command_connector_passes_secrets_by_environment_name_only(store):
    nc.save({"label": "GitHub", "kind": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"], "env": {"GITHUB_TOKEN": "ghp_x"}})
    settings, env = nc.runtime_settings()
    assert settings["mcp_servers.conn_github.command"] == "npx" and settings["mcp_servers.conn_github.args"][0] == "-y"
    names = settings["mcp_servers.conn_github.env_vars"]
    assert names == ["SMARA_CONN_GITHUB_GITHUB_TOKEN"] and env[names[0]] == "ghp_x" and "ghp_x" not in json.dumps(settings)
    assert nc.listing()["connectors"][0]["env_names"] == ["GITHUB_TOKEN"]


@pytest.mark.parametrize("command", ["powershell", "cmd", "C:/Windows/System32/cmd.exe", "bash -c 'x'", ""])
def test_only_known_launchers_may_be_run(store, command):
    with pytest.raises(ValueError):
        nc.save({"label": "Bad", "kind": "stdio", "command": command})


def test_names_cannot_shadow_smara_tools_or_repeat_or_exceed_the_limit(store):
    for label in ("Smara team", "playwright"):
        with pytest.raises(ValueError):
            nc.save({"label": label, "kind": "http", "url": "https://x.example/"})
    nc.save({"label": "One", "kind": "http", "url": "https://x.example/"})
    with pytest.raises(ValueError, match="already exists"):
        nc.save({"label": "One", "kind": "http", "url": "https://y.example/"})
    for i in range(2, nc.MAX_CONNECTORS + 1):
        nc.save({"label": f"Svc {i}", "kind": "http", "url": "https://x.example/"})
    with pytest.raises(ValueError, match="At most"):
        nc.save({"label": "Too many", "kind": "http", "url": "https://x.example/"})


def test_disable_remove_and_the_prompt_line(store):
    nc.save({"label": "Sheets", "kind": "http", "url": "https://s.example/mcp"})
    assert "Sheets" in nc.prompt_line() and "asks the owner first" in nc.prompt_line()
    nc.toggle("sheets", False)
    assert nc.runtime_settings() == ({}, {}) and nc.prompt_line() == ""
    nc.remove("sheets")
    assert nc.listing()["connectors"] == []


def test_a_broken_record_is_skipped_not_fatal(store):
    (store / "connectors.json").write_text(json.dumps({"connectors": [{"id": "bad", "kind": "http", "url": "http://evil.example"}, {"nope": 1}]}))
    assert nc.runtime_settings() == ({}, {})


def test_unknown_requests_are_refused(store):
    with pytest.raises(ValueError):
        nc.manage({"operation": "connectors", "action": "save", "label": "x", "kind": "http", "url": "https://x.example/", "cmd": "rm"})
    with pytest.raises(ValueError):
        nc.manage({"operation": "connectors", "action": "wipe"})
