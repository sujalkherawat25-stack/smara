"""Policy/backend-fixture checks; not actual Windows app input evidence."""
from copy import deepcopy
from types import SimpleNamespace
import time
import pytest
from smara.native_computer import Computer, grant_path
from smara.native_profiles import write_object


def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "state.json"))
    grant = {"id": "synthetic-grant", "enabled": True, "target": {"id": "synthetic"}, "actions": 0, "expires": time.time() + 900}
    write_object(grant_path(tmp_path), grant)
    actions, observed = [], {"fingerprint": "first", "image": b"synthetic PNG", "elements": [
        {"ref": "e0", "runtime_id": [1], "actions": ["set_value"], "text": "Synthetic editor"}]}
    backend = SimpleNamespace(observe=lambda _: deepcopy(observed), act=lambda *args: actions.append(args))
    return Computer(tmp_path, backend), grant, observed, actions


def test_revoke_and_stale_screen_never_act(tmp_path, monkeypatch):
    computer, grant, observed, actions = fixture(tmp_path, monkeypatch)
    first = computer.observe()
    observed["fingerprint"] = "changed"
    args = {"observation_id": first["observation_id"], "ref": "e0", "action": "set_value", "value": "synthetic"}
    with pytest.raises(ValueError, match="Screen changed"): computer.call("computer_act", args)
    write_object(grant_path(tmp_path), {"enabled": False})
    with pytest.raises(ValueError, match="revoked"): computer.call("computer_act", args)
    assert not actions


def test_one_action_consumes_observation_and_returns_post_action_state(tmp_path, monkeypatch):
    computer, grant, observed, actions = fixture(tmp_path, monkeypatch)
    first = computer.observe()
    args = {"observation_id": first["observation_id"], "ref": "e0", "action": "set_value", "value": "Synthetic"}
    result = computer.call("computer_act", args)
    assert len(actions) == 1 and result["observation_id"] != first["observation_id"] and not result["uploaded"]
    with pytest.raises(ValueError, match="Stale"): computer.call("computer_act", args)


def test_failed_action_is_not_retried_and_allowance_survives_reconnect(tmp_path, monkeypatch):
    computer, grant, observed, actions = fixture(tmp_path, monkeypatch)
    def uncertain(*args): actions.append(args); raise OSError("Unknown fixture action outcome")
    computer.backend.act = uncertain
    first = computer.observe()
    args = {"observation_id": first["observation_id"], "ref": "e0", "action": "set_value"}
    with pytest.raises(OSError): computer.call("computer_act", args)
    with pytest.raises(ValueError, match="Stale"): computer.call("computer_act", args)
    grant["actions"] = 40; write_object(grant_path(tmp_path), grant)
    replacement = Computer(tmp_path, computer.backend)
    args["observation_id"] = replacement.observe()["observation_id"]
    with pytest.raises(ValueError, match="allowance"): replacement.call("computer_act", args)
    assert len(actions) == 1


def test_unknown_element_keys_and_global_shortcuts_rejected(tmp_path, monkeypatch):
    computer, _, _, actions = fixture(tmp_path, monkeypatch)
    first = computer.observe()
    for extra in ({"action": "press_key"}, {"x": 10}, {"ref": "not-observed"}):
        with pytest.raises(ValueError): computer.call("computer_act", {"observation_id": first["observation_id"], "ref": "e0", "action": "set_value", **extra})
    assert not actions


def test_foreground_handoff_checks_revocation_before_observing(tmp_path, monkeypatch):
    computer, grant, _, actions = fixture(tmp_path, monkeypatch)
    observations = []
    def wait(_target, permitted):
        assert permitted()
        write_object(grant_path(tmp_path), {"enabled": False})
        assert not permitted()
    computer.backend.wait_foreground = wait
    computer.backend.observe = lambda _: observations.append(True)
    with pytest.raises(ValueError, match="revoked"): computer.observe()
    assert not observations and not actions


def test_handoff_never_allows_an_aged_action_or_resets_usage(tmp_path, monkeypatch):
    computer, grant, _, actions = fixture(tmp_path, monkeypatch)
    grant["actions"] = 7; write_object(grant_path(tmp_path), grant)
    first = computer.observe()
    assert first["actions_used"] == 7
    computer.backend.wait_foreground = lambda *_: computer.latest.update(created=time.monotonic() - 31)
    with pytest.raises(ValueError, match="Stale observation after"):
        computer.call("computer_act", {"observation_id": first["observation_id"], "ref": "e0", "action": "set_value"})
    assert not actions


def test_windows_handoff_retries_only_focus_checks(monkeypatch):
    from smara import native_windows
    backend = native_windows.WindowsBackend.__new__(native_windows.WindowsBackend)
    checks = []
    def check(_target):
        checks.append(True)
        if len(checks) == 1: raise native_windows.ForegroundRequired("Human handoff")
    monkeypatch.setattr(backend, "_window", check)
    monkeypatch.setattr(native_windows.time, "sleep", lambda _: None)
    backend.wait_foreground({}, lambda: True)
    assert len(checks) == 2
    def changed(_target): raise ValueError("ownership changed")
    monkeypatch.setattr(backend, "_window", changed)
    with pytest.raises(ValueError, match="ownership changed"):
        backend.wait_foreground({}, lambda: True)


def test_windows_handoff_expires_without_input_or_waits_after_stop(monkeypatch):
    from smara import native_windows
    backend = native_windows.WindowsBackend.__new__(native_windows.WindowsBackend)
    checks = []
    def missing(_target):
        checks.append(True)
        raise native_windows.ForegroundRequired("Not foreground")
    monkeypatch.setattr(backend, "_window", missing)
    clock = iter([0, 11])
    monkeypatch.setattr(native_windows.time, "monotonic", lambda: next(clock))
    with pytest.raises(native_windows.ForegroundRequired, match="timed out"):
        backend.wait_foreground({}, lambda: True)
    assert len(checks) == 1
    monkeypatch.setattr(native_windows.time, "monotonic", lambda: 0)
    with pytest.raises(ValueError, match="revoked"):
        backend.wait_foreground({}, lambda: False)
    assert len(checks) == 1
