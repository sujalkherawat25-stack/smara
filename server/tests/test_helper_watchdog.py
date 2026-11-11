"""The PC helper repairs itself: a per-user scheduled task runs `ensure` every few minutes.

Windows tools are replaced by fakes, so these run anywhere; the real task was exercised on a Windows PC by hand.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import smara_bridge as mb  # noqa: E402


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(mb.platform, "system", lambda: "Windows")
    calls = []
    state = {"xml": None, "register_rc": 0, "installed": True}

    def run(args, env=None, timeout=60):
        calls.append((args, env))
        if args[0] == "schtasks" and args[1] == "/Query":
            return SimpleNamespace(returncode=0 if state["xml"] is not None else 1, stdout=state["xml"] or "", stderr="")
        if args[0] == "powershell.exe":
            return SimpleNamespace(returncode=state["register_rc"], stdout="", stderr="" if state["register_rc"] == 0 else "Access is denied")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mb, "_run_quiet", run)
    monkeypatch.setattr(mb, "startup_installed", lambda: state["installed"])
    monkeypatch.setattr(mb, "log", lambda _m: None)
    return calls, state


def test_watchdog_runs_ensure_with_the_helpers_own_interpreter_and_script():
    program, arguments = mb.watchdog_command()
    assert program == mb.startup_command()[0]
    assert arguments.endswith('"ensure"') and str(Path(mb.__file__).resolve()) in arguments and '"run"' not in arguments


def test_missing_watchdog_is_registered_with_battery_and_hidden_settings(windows):
    calls, state = windows
    assert mb.watchdog_state() == "missing"
    assert mb.ensure_watchdog() == "registered"
    args, env = next(c for c in calls if c[0][0] == "powershell.exe")
    script = args[-1]
    assert "-MultipleInstances IgnoreNew" in script and "-AllowStartIfOnBatteries" in script and "-Hidden" in script
    assert "-RunLevel Limited" in script, "must not need administrator rights"
    assert f"-RepetitionInterval (New-TimeSpan -Minutes {mb.WATCHDOG_MINUTES})" in script and mb.WATCHDOG_TASK in script
    assert env["SMARA_WD_EXE"] == mb.watchdog_command()[0] and env["SMARA_WD_ARGS"] == mb.watchdog_command()[1]
    assert "ensure" in env["SMARA_WD_ARGS"], "paths reach PowerShell through the environment, never spliced into the script"


def test_current_watchdog_is_left_alone(windows):
    calls, state = windows
    state["xml"] = "<Task>" + str(Path(mb.__file__).resolve()).upper() + " ensure</Task>"
    assert mb.ensure_watchdog() == "current"
    assert not [c for c in calls if c[0][0] == "powershell.exe"], "an up-to-date task is never rewritten"


def test_watchdog_for_an_old_install_path_is_replaced(windows):
    calls, state = windows
    state["xml"] = r"<Task>C:\Users\x\smara\bridge\smara_bridge.py ensure</Task>"
    assert mb.watchdog_state() == "stale"
    assert mb.ensure_watchdog() == "registered"


def test_no_watchdog_unless_the_owner_set_the_helper_up(windows):
    calls, state = windows
    state["installed"] = False
    assert mb.ensure_watchdog() == "skipped" and not calls


def test_failing_to_register_reports_and_never_raises(windows):
    _, state = windows
    state["register_rc"] = 1
    assert mb.ensure_watchdog() == "failed"
    assert mb.register_watchdog() is False


def test_not_windows_does_nothing(monkeypatch):
    monkeypatch.setattr(mb.platform, "system", lambda: "Linux")
    assert mb.ensure_watchdog() == "skipped" and mb.register_watchdog() is False and mb.watchdog_state() == "missing"
    mb.remove_watchdog()  # no error


def test_uninstall_startup_removes_the_watchdog(windows, monkeypatch):
    calls, _ = windows
    monkeypatch.setattr(mb, "cmd_stop", lambda: 0)
    import types  # winreg exists only on Windows; a fake keeps this test portable
    monkeypatch.setitem(sys.modules, "winreg", types.SimpleNamespace(
        HKEY_CURRENT_USER=0, KEY_SET_VALUE=0, KEY_READ=0,
        OpenKey=lambda *a, **k: (_ for _ in ()).throw(OSError()), DeleteValue=lambda *a: None))
    assert mb.cmd_uninstall_startup() == 0
    assert any(c[0][:2] == ["schtasks", "/Delete"] and mb.WATCHDOG_TASK in c[0] for c in calls)
