"""Per-employee skills: learned once with the owner's OK, followed the same way, never holding secrets."""
import json

import pytest

from smara import native_management as management
from smara import native_profiles
from smara import native_skills as skills
from smara import native_team as team


@pytest.fixture
def setup(tmp_path, monkeypatch):
    state = tmp_path / "desktop.json"
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(state))
    monkeypatch.setenv("SMARA_DESKTOP_CREDENTIALS", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("SMARA_AGENT_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setattr(management, "protect", lambda value: "synthetic-protected:" + value)
    monkeypatch.setattr(native_profiles, "unprotect", lambda value: value.removeprefix("synthetic-protected:"))
    state.write_text(json.dumps({"workspace": str(tmp_path)}))
    monkeypatch.setattr(team, "_tasks", lambda employee="": [])
    return tmp_path


SKILL = {"employee": "Kiran", "name": "Monthly sales summary", "when_to_use": "When the owner asks for a month's sales summary",
         "inputs": ["the month as YYYY-MM"], "steps": ["1. Read sales_<YYYY-MM>.csv", "2) Sum amount by category", "- Write summary_<YYYY-MM>.md"],
         "checks": ["Category totals add up to the grand total"], "output": "summary_<YYYY-MM>.md with a table", "never": ["Edit the csv"]}


def test_saving_and_changing_skills_asks_the_owner_and_reading_does_not():
    for name in ("skill_save", "skill_forget"):
        assert team.approval_mode(name) == "prompt"
    for name in ("skill_list", "skill_read"):
        assert team.approval_mode(name) == "approve"


def test_a_skill_is_learned_per_employee_and_read_back_as_numbered_steps(setup):
    out = team.call("skill_save", SKILL)
    assert out["saved"]["version"] == 1 and "learned" in out["tell_the_owner"]
    listed = team.call("skill_list", {"employee": "kiran"})["skills"]
    assert [s["name"] for s in listed] == ["Monthly sales summary"]
    assert team.call("skill_list", {"employee": "Rohan"})["skills"] == []           # another employee does not have it
    text = team.call("skill_read", {"employee": "Kiran", "name": "monthly sales summary"})["skill"]
    assert "1. Read sales_<YYYY-MM>.csv\n2. Sum amount by category\n3. Write summary_<YYYY-MM>.md" in text
    assert "Never:\n- Edit the csv" in text
    assert team.call("skill_list", {"employee": "kiran"})["skills"][0]["uses"] == 1


def test_a_correction_updates_the_same_skill_and_keeps_history(setup):
    team.call("skill_save", SKILL)
    out = team.call("skill_save", {**SKILL, "steps": SKILL["steps"] + ["Round totals to whole rupees"], "change_note": "Owner wants whole rupees"})
    assert out["saved"]["version"] == 2 and "version 2" in out["tell_the_owner"]
    record = skills.find("kiran", "Monthly sales summary")
    assert len(record["steps"]) == 4 and [h["note"] for h in record["history"]] == ["Learned", "Owner wants whole rupees"]
    assert len(skills.all_for("kiran")) == 1


def test_a_skill_never_stores_secrets(setup):
    for bad in ("Log in with password: Hunter2secret", "Enter OTP 482913", "Pay with card 4111 1111 1111 1111"):
        with pytest.raises(ValueError, match="never stores passwords"):
            team.call("skill_save", {**SKILL, "steps": [bad]})
    assert skills.all_for("kiran") == []


def test_bad_input_is_refused_with_a_reason(setup):
    with pytest.raises(ValueError, match="steps"):
        team.call("skill_save", {**SKILL, "steps": []})
    with pytest.raises(ValueError, match="No skill called"):
        team.call("skill_read", {"employee": "Kiran", "name": "Payroll"})
    with pytest.raises(ValueError):
        skills.all_for("../../etc")


def test_the_brief_lists_skills_for_every_chat_and_task(setup):
    team.call("skill_save", SKILL)
    brief = skills.brief("kiran")
    assert "- Monthly sales summary: When the owner asks" in brief and "employee id for skill tools is kiran" in brief
    assert "follow its steps in order, exactly" in brief
    view = management.manage({"operation": "agents", "action": "list"})["agents"]["roster"]
    kiran = next(a for a in view if a["id"] == "kiran")
    assert kiran["skills"][0]["name"] == "Monthly sales summary" and "Monthly sales summary" in kiran["skills_brief"]
    from smara.native_goal_runtime import goal_instructions
    import smara.native_goal_runtime as runtime
    assert "Monthly sales summary" in goal_instructions({"agent_id": "kiran"}, "BASE") or runtime.assigned_agent({"agent_id": "kiran"}) is None


def test_the_settings_window_can_list_and_forget(setup):
    team.call("skill_save", SKILL)
    listed = management.manage({"operation": "agents", "action": "skills", "agent_id": "kiran"})["skills"]
    assert listed[0]["steps_text"][0] == "Read sales_<YYYY-MM>.csv"
    assert management.manage({"operation": "agents", "action": "forget_skill", "agent_id": "kiran", "name": "Monthly sales summary"})["skills"] == []
