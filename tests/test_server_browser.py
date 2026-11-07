"""The server's own browser: a normal headed Chromium on a virtual screen, with the shop's own persistent profile, set up
for India. No faked identity: the user agent is only overridden in the headless fallback (to drop "HeadlessChrome")."""
import json
from pathlib import Path

import pytest

from src.box import tools


def test_a_headed_browser_keeps_its_own_user_agent_and_is_set_up_for_india():
    ctx = tools.context_options(headless=False)
    assert "user_agent" not in ctx, "a real headed browser reports its real user agent"
    assert ctx["locale"] == "en-IN" and ctx["timezone_id"] == "Asia/Kolkata"
    assert tools.launch_options(headless=False)["headless"] is False


def test_only_the_headless_fallback_overrides_the_user_agent():
    assert "HeadlessChrome" not in tools.context_options(headless=True)["user_agent"]
    assert tools.launch_options(headless=True)["headless"] is True


def test_each_shop_gets_its_own_profile_folder_and_odd_ids_get_none(tmp_path, monkeypatch):
    monkeypatch.setenv("BROWSER_PROFILE_ROOT", str(tmp_path))
    a, b = tools.profile_dir("shop-a_1"), tools.profile_dir("shop-b")
    assert a == tmp_path / "shop-a_1" and b == tmp_path / "shop-b" and a.is_dir() and a != b
    for bad in ("../etc", "a/b", "", None, "x" * 65, "a b"):
        assert tools.profile_dir(bad) is None, bad


def test_profiles_are_off_unless_configured(monkeypatch):
    monkeypatch.delenv("BROWSER_PROFILE_ROOT", raising=False)
    assert tools.profile_dir("shop-a") is None


def test_no_virtual_screen_when_there_is_a_display_or_no_xvfb(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    assert tools.start_virtual_display() is None
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(tools.shutil, "which", lambda _name: None)
    assert tools.start_virtual_display() is None and "DISPLAY" not in __import__("os").environ


def test_the_worker_is_told_which_shop_it_browses_for(monkeypatch):
    from src.box import browser_session
    seen = {}

    class Proc:
        def __init__(self, args, **_kw):
            seen["args"] = args
            self.stdout = type("S", (), {"readline": staticmethod(lambda: json.dumps({"ready": True}) + "\n")})()
        def kill(self): pass
    monkeypatch.setattr(browser_session.subprocess, "Popen", Proc)
    browser_session.BrowserSessions()._start("shop-a")
    assert seen["args"][-2:] == ["--tenant", "shop-a"]
    browser_session.BrowserSessions()._start(None)
    assert "--tenant" not in seen["args"]
