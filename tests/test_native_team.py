"""Smara's team tools: hiring, renaming, standing rules and handing out jobs. Synthetic settings files only."""
import json

import pytest

from smara import native_management as management
from smara import native_profiles
from smara import native_team as team


@pytest.fixture
def setup(tmp_path, monkeypatch):
    state = tmp_path / "desktop.json"
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(tmp_path / "credentials.json"))
    monkeypatch.setattr(management, "protect", lambda value: "synthetic-protected:" + value)
    monkeypatch.setattr(native_profiles, "unprotect", lambda value: value.removeprefix("synthetic-protected:"))
    state.write_text(json.dumps({"workspace": str(tmp_path)}))
    monkeypatch.setattr(team, "_tasks", lambda employee="": [])
    return tmp_path


def names():
    return [a["name"] for a in management.manage({"operation": "bootstrap"})["agents"]["roster"]]


def test_changing_the_team_always_asks_the_owner_and_reading_does_not():
    assert {t["name"] for t in team.TOOLS} == {"team_list", "team_hire", "team_update", "team_remember", "team_delegate", "team_tasks"}
    for name in ("team_hire", "team_update", "team_remember"):
        assert team.approval_mode(name) == "prompt"
    for name in ("team_list", "team_tasks", "team_delegate"):
        assert team.approval_mode(name) == "approve"


def test_hire_in_chat_adds_an_employee_who_shows_in_the_roster(setup):
    out = team.call("team_hire", {"name": "Meera", "role": "Mail handler", "job": "You read the shop inbox and draft replies. Never send without asking."})
    assert out["hired"]["name"] == "Meera" and out["hired"]["role"] == "Mail handler"
    assert names()[-1] == "Meera"
    with pytest.raises(ValueError, match="(?i)already someone called meera"):
        team.call("team_hire", {"name": "meera", "role": "x", "job": "y"})
    with pytest.raises(ValueError, match="job description"):
        team.call("team_hire", {"name": "Raj", "role": "Accountant", "job": ""})


def test_rules_are_remembered_once_and_kept_apart_from_the_job(setup):
    team.call("team_hire", {"name": "Meera", "role": "Mail handler", "job": "You read the shop inbox."})
    team.call("team_remember", {"employee": "Meera", "rule": "Always cc the owner on replies to GST officers."})
    out = team.call("team_remember", {"employee": "meera", "rule": "always cc the owner on replies to GST officers."})
    assert out["employee"]["rules"] == ["Always cc the owner on replies to GST officers."]
    assert out["employee"]["job"] == "You read the shop inbox."
    listed = {e["name"]: e for e in team.call("team_list", {})["team"]}
    assert listed["Meera"]["rules"] == ["Always cc the owner on replies to GST officers."]
    # Rewriting the job keeps the rules.
    team.call("team_update", {"employee": "Meera", "job": "You handle the inbox and the calendar."})
    listed = {e["name"]: e for e in team.call("team_list", {})["team"]}
    assert listed["Meera"]["job"] == "You handle the inbox and the calendar." and len(listed["Meera"]["rules"]) == 1


def test_the_manager_can_be_renamed_and_stays_the_manager(setup):
    team.call("team_update", {"employee": "smara", "name": "Munshi"})
    roster = management.manage({"operation": "bootstrap"})["agents"]
    assert roster["roster"][0]["name"] == "Munshi" and roster["default"] == "smara"
    assert team.call("team_list", {})["team"][0]["manager"] is True


def test_unknown_employee_is_refused_with_a_hint(setup):
    with pytest.raises(ValueError, match="team_list"):
        team.call("team_remember", {"employee": "Nobody", "rule": "x"})


def test_delegating_creates_a_background_task_for_that_employee(setup, monkeypatch):
    created = []

    def operate(request, store=None):
        created.append(request)
        return {"id": "g1", "state": "queued"}
    import smara.native_goals as goals
    monkeypatch.setattr(goals, "operate", operate)
    monkeypatch.setattr(management, "preferences", lambda: {"workspace": str(setup), "active_model": "glm"})
    out = team.call("team_delegate", {"employee": "Kiran", "task": "Total the October sales from Tally"}, str(setup))
    assert out["task_id"] == "g1" and out["employee"] == "Kiran"
    create = created[0]
    assert create["action"] == "create" and create["agent_id"] == "kiran" and create["sandbox"] == "read-only"
    assert create["objective"] == "Total the October sales from Tally" and create["max_cycles"] == 6
    assert created[1] == {"action": "start_service"}


def test_the_server_speaks_mcp_and_reports_refusals_as_tool_errors(setup, monkeypatch, capsys):
    import io
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "team_remember", "arguments": {"employee": "Ghost", "rule": "x"}}}]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(json.dumps(line) for line in lines) + "\n"))
    team.main([])
    replies = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert replies[0]["result"]["serverInfo"]["name"] == "smara-team"
    assert len(replies[1]["result"]["tools"]) == 6
    assert replies[2]["result"]["isError"] is True and "Team change refused" in replies[2]["result"]["content"][0]["text"]
