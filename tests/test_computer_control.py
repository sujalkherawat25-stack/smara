"""Owner control over computer use: Pause / Resume / Stop (also mid-action), the allowed-apps list, and the audit trail."""
import time

import pytest

from smara import agent_audit, computer_control as control, native_computer as nc
from smara.native_computer import Computer, grant_path
from smara.native_profiles import write_object
from test_native_computer import Fake


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("SMARA_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setattr(nc.time, "sleep", lambda _: None)
    grant = {"id": "g", "enabled": True, "scope": "desktop", "target": {}, "actions": 0, "created": time.time(), "expires": time.time() + 900}
    write_object(grant_path(tmp_path), grant)
    backend = Fake()
    return Computer(tmp_path, backend), backend, tmp_path


def test_pause_holds_every_step_until_resume(env):
    computer, _, _ = env
    computer.call("computer_windows", {})
    control.pause("bar")
    with pytest.raises(ValueError, match="paused computer control"):
        computer.call("computer_windows", {})
    with pytest.raises(ValueError, match="paused"):
        computer.call("computer_observe", {"window_id": "7"})
    control.resume("bar")
    assert computer.call("computer_windows", {})["windows"]


def test_stop_revokes_grants_made_before_it_and_a_new_grant_works(env, tmp_path):
    computer, _, _ = env
    control.stop("hotkey")
    with pytest.raises(ValueError, match="pressed Stop"):
        computer.call("computer_windows", {})
    time.sleep(0.01)
    write_object(grant_path(tmp_path), {"id": "g2", "enabled": True, "scope": "desktop", "actions": 0, "created": time.time(),
                                        "expires": time.time() + 900})
    assert computer.call("computer_windows", {})["windows"]


def test_a_damaged_control_file_holds_the_agent(env):
    computer, _, _ = env
    control.control_path().write_text("{not json", encoding="utf-8")
    assert control.state()["paused"] is True
    with pytest.raises(ValueError, match="paused"):
        computer.call("computer_windows", {})


def test_pause_cuts_in_while_typing(env):
    computer, backend, _ = env

    def typing(target, text, permitted=lambda: True, check_focus=None):
        backend.calls.append(("type", text[:3]))
        control.pause("bar")                       # the owner presses Pause on the bar mid-way
        if backend.halt():                         # the real backend checks this every few characters and pointer steps
            raise ValueError("Stopped: the owner paused or stopped computer control")
        backend.calls.append(("type", text[3:]))
    backend.type_text = typing
    obs = computer.call("computer_observe", {"window_id": "7"})
    with pytest.raises(ValueError, match="Stopped.*Outcome may be partial"):
        computer.call("computer_act", {"observation_id": obs["observation_id"], "action": "type_text", "text": "Sharma Traders"})
    assert ("type", "Sha") in backend.calls and ("type", "rma Traders") not in backend.calls
    assert backend.halt is None                   # the hook is removed after the step


def test_windows_backend_halt_check():
    from smara.native_windows import WindowsBackend
    backend = object.__new__(WindowsBackend)
    backend.halt = None
    backend._check_halt()
    backend.halt = lambda: True
    with pytest.raises(ValueError, match="Stopped"):
        backend._check_halt()


def test_app_grants_reach_only_the_allowed_apps(env, tmp_path):
    computer, backend, _ = env
    write_object(grant_path(tmp_path), {"id": "g3", "enabled": True, "scope": "apps", "apps": ["excel.exe"], "actions": 0,
                                        "created": time.time(), "expires": time.time() + 900})
    assert computer.call("computer_windows", {})["windows"] == []             # fixture.exe is not on the list
    backend.window.update(app="excel.exe", exe="excel.exe")
    assert [w["app"] for w in computer.call("computer_windows", {})["windows"]] == ["excel.exe"]


def test_allowed_apps_list_defaults_saves_and_validates(env):
    assert "excel.exe" in control.allowed_apps() and "cmd.exe" not in control.allowed_apps()
    assert control.save_apps(["TallyPrime.exe", "excel.exe", "excel.exe"]) == ["tallyprime.exe", "excel.exe"]
    assert control.allowed_apps() == ["tallyprime.exe", "excel.exe"]
    for bad in (["C:\\Windows\\cmd.exe"], ["notepad"], "excel.exe", ["x.exe"] * 41):
        with pytest.raises(ValueError):
            control.save_apps(bad)


def test_every_step_is_audited_with_its_screenshot(env):
    computer, _, _ = env
    obs = computer.call("computer_observe", {"window_id": "7"})
    nc.audit_call("computer_observe", {"window_id": "7"}, obs)
    out = computer.call("computer_act", {"observation_id": obs["observation_id"], "action": "type_text", "text": "Rs 500"})
    nc.audit_call("computer_act", {"observation_id": obs["observation_id"], "action": "type_text", "text": "Rs 500"}, out)
    nc.audit_call("computer_act", {"observation_id": "x", "action": "click", "element_index": 1}, None, "Critical action (clicks 'Send invoice')")
    rows = agent_audit.entries()
    assert [r["action"] for r in rows] == ["computer_act", "computer_act", "computer_observe"]
    assert rows[0]["outcome"].startswith("error: Critical") and not rows[0]["screenshot"]
    assert rows[1]["target"] == "fixture.exe · Fixture" and rows[1]["detail"]["text"] == "Rs 500" and rows[1]["screenshot"]
    assert agent_audit.screenshot_path(rows[2]["screenshot"]).read_bytes() == b"PNG"
    assert control.last_activity()["action"] == "type_text"
