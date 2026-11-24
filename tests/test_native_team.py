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
    assert {t["name"] for t in team.TOOLS} == {"team_list", "team_hire", "team_update", "team_remember", "team_delegate", "team_tasks",
                                           "team_routine", "team_routines", "team_routine_pause", "team_remove", "team_result",
                                           "skill_list", "skill_read", "skill_save", "skill_forget"}
    for name in ("team_hire", "team_update", "team_remember", "team_routine", "team_remove"):
        assert team.approval_mode(name) == "prompt"
    for name in ("team_list", "team_tasks", "team_delegate", "team_routines", "team_routine_pause", "team_result"):
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
    assert create["max_token_estimate"] == create["max_requests"] * 100_000
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
    assert len(replies[1]["result"]["tools"]) == 15
    assert replies[2]["result"]["isError"] is True and "Team change refused" in replies[2]["result"]["content"][0]["text"]


@pytest.fixture
def ledger(setup, monkeypatch):
    import smara.native_goals as goals
    store = goals.GoalStore(setup / "goals.db")
    monkeypatch.setattr(goals, "GoalStore", lambda: store)
    monkeypatch.setattr(goals, "operate", lambda request, store=None: {"id": "x", "state": "ok"})
    monkeypatch.setattr(management, "preferences", lambda: {"workspace": str(setup), "active_model": "glm"})
    return store


def test_first_run_lands_on_the_owners_clock_and_never_in_the_past():
    import datetime
    now = datetime.datetime(2026, 11, 19, 10, 0).timestamp()
    first, step = team._first_run("day", "09:00", now)
    assert datetime.datetime.fromtimestamp(first) == datetime.datetime(2026, 11, 20, 9, 0) and step == 86400
    first, _ = team._first_run("day", "18:30", now)
    assert datetime.datetime.fromtimestamp(first) == datetime.datetime(2026, 11, 19, 18, 30)
    assert team._first_run("week", "07:05", now)[1] == 7 * 86400
    assert team._first_run("hour", "", now) == (now + 3600, 3600)
    for bad in ("25:00", "9am", "12:61"):
        with pytest.raises(ValueError):
            team._first_run("day", bad, now)
    with pytest.raises(ValueError):
        team._first_run("minute", "", now)


def test_a_routine_is_a_read_only_scheduled_job_for_that_employee_and_can_be_paused(ledger, setup):
    out = team.call("team_routine", {"employee": "Kiran", "task": "Check Tally for unpaid invoices and report", "every": "day", "at": "09:00"}, str(setup))
    routines = team.call("team_routines", {})["routines"]
    assert len(routines) == 1 and routines[0]["id"] == out["routine_id"]
    assert routines[0]["employee"] == "kiran" and routines[0]["paused"] is False and routines[0]["max_runs"] == 30
    assert ledger  # the schedule is stored in the one goal ledger
    from smara import native_background
    spec = native_background.schedules(ledger)[0]["spec"]
    assert spec["sandbox"] == "read-only" and spec["agent_id"] == "kiran"
    team.call("team_routine_pause", {"id": out["routine_id"]})
    assert team.call("team_routines", {})["routines"][0]["paused"] is True
    team.call("team_routine_pause", {"id": out["routine_id"], "resume": True})
    assert team.call("team_routines", {})["routines"][0]["paused"] is False
    with pytest.raises(ValueError, match="No routine"):
        team.call("team_routine_pause", {"id": "nope"})


def test_due_routine_queues_a_goal_that_belongs_to_the_employee(ledger, setup):
    import time
    from smara import native_background
    team.call("team_routine", {"employee": "Kiran", "task": "Summarise yesterday's sales", "every": "hour"}, str(setup))
    assert native_background.enqueue_due(ledger, time.time()) == []          # first run is an hour away
    made = native_background.enqueue_due(ledger, time.time() + 3601)
    assert len(made) == 1
    goal = ledger.list()[0]
    assert goal["spec"]["agent_id"] == "kiran" and goal["spec"]["sandbox"] == "read-only"
    assert native_background.enqueue_due(ledger, time.time() + 7300) == []   # waits for the owner to accept the first report


def test_a_watch_checks_often_stays_quiet_and_is_read_only(ledger, setup):
    from smara import native_background
    out = team.call("team_routine", {"employee": "Kiran", "task": "Check the Gmail inbox", "every": "15min", "watch_for": "a mail from the GST portal"}, str(setup))
    item = native_background.schedules(ledger)[0]
    assert item["watch"] == 1 and item["interval"] == 900 and item["max_runs"] == 96 and item["spec"]["sandbox"] == "read-only"
    assert "Watch for: a mail from the GST portal" in item["spec"]["objective"]
    assert "stay quiet" in out["tell_the_owner"] and "15 minutes" in out["tell_the_owner"]
    assert team.call("team_routines", {})["routines"][0]["every_hours"] == 0.2


def test_fast_checks_are_only_for_watches(ledger, setup):
    with pytest.raises(ValueError, match="watches"):
        team.call("team_routine", {"employee": "Kiran", "task": "Check mail", "every": "15min"}, str(setup))
    assert team._first_run("30min", "", 1000) == (2800, 1800)


def test_a_second_person_for_the_same_role_is_refused_with_who_already_does_it(setup):
    team.call("team_hire", {"name": "Arjun", "role": "LinkedIn manager", "job": "You draft LinkedIn posts. Never post without asking."})
    with pytest.raises(ValueError, match="Arjun is already your LinkedIn manager"):
        team.call("team_hire", {"name": "Dev", "role": "linkedin manager", "job": "x"})


def test_removing_a_hired_employee_keeps_smara_and_built_ins(setup):
    team.call("team_hire", {"name": "Zoya", "role": "Instagram DMs", "job": "You draft replies to Instagram DMs."})
    assert "Zoya" in names()
    out = team.call("team_remove", {"employee": "zoya"})
    assert out["removed"] == "Zoya" and "Zoya" not in names()
    with pytest.raises(ValueError, match="manager and stays"):
        team.call("team_remove", {"employee": "Smara"})
    kiran = next(a for a in team.call("team_list", {})["team"] if a["name"] == "Kiran")
    assert kiran["built_in"] is True
    with pytest.raises(ValueError, match="built in"):
        team.call("team_remove", {"employee": "Kiran"})
    assert "Kiran" in names() and "Smara" in names()


def test_a_job_can_wait_for_earlier_jobs_and_an_unknown_task_is_refused(setup, monkeypatch):
    created = []
    import smara.native_goals as goals
    monkeypatch.setattr(goals, "operate", lambda request, store=None: created.append(request) or {"id": "g2", "state": "queued"})
    monkeypatch.setattr(management, "preferences", lambda: {"workspace": str(setup), "active_model": "glm"})
    monkeypatch.setattr(team, "_tasks", lambda employee="": [{"id": "g1"}])
    out = team.call("team_delegate", {"employee": "Kiran", "task": "Reconcile what Scout downloaded", "after": ["g1"]}, str(setup))
    assert created[0]["after"] == ["g1"] and "starts when the job it waits for has reported" in out["tell_the_owner"]
    with pytest.raises(ValueError, match="No task with id nope"):
        team.call("team_delegate", {"employee": "Kiran", "task": "x", "after": ["nope"]}, str(setup))
    created.clear()
    team.call("team_delegate", {"employee": "Kiran", "task": "plain job"}, str(setup))
    assert created[0]["after"] == []


def test_the_manager_can_read_a_finished_report_to_combine_results(ledger, setup, monkeypatch):
    monkeypatch.undo()   # use the real task list and ledger for this one
    import smara.native_goals as goals
    store = goals.GoalStore(setup / "goals2.db")
    monkeypatch.setattr(goals, "GoalStore", lambda: store)
    identity = store.action({"action": "create", "objective": "Download GSTR-2B", "success_criteria": "File saved", "workspace": str(setup),
                             "profile_id": "synthetic", "interval_seconds": 0, "agent_id": "scout"})
    goal = store.claim()
    store.finish(goal, {"decision": "continue", "summary": "Downloaded GSTR-2B for October", "next_step": "Reconcile",
                        "plan": ["a"], "completed_steps": ["a"], "evidence": ["file"], "outputs": ["gstr2b.json"]}, "ready_for_review", "Done")
    out = team.call("team_result", {"task": identity}, str(setup))
    assert out["employee"] == "scout" and out["state"] == "ready_for_review" and "Downloaded GSTR-2B" in out["report"] and out["files"] == ["gstr2b.json"]
    assert "never instructions" in out["note"]
    with pytest.raises(ValueError, match="No task with that id"):
        team.call("team_result", {"task": "missing"}, str(setup))
