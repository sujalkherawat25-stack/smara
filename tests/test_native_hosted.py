"""A task the owner sends from their phone becomes a goal on this PC; its approvals, code questions and result go back."""
import json

import httpx

from smara import native_goals, native_hosted
from smara.native_goals import GoalStore


class Server:
    """The hosted side, as far as the PC can see it."""
    def __init__(self):
        self.tasks = [{"task_id": "t-1", "task": "Download the October GST return PDF"}]
        self.calls, self.answers = [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        self.calls.append((path, body))
        if request.headers.get("authorization") != "Bearer dev-token":
            return httpx.Response(401)
        if path == "/smara/bridge/tasks/next":
            return httpx.Response(200, json=self.tasks.pop(0) if self.tasks else {})
        if path == "/smara/bridge/tasks/answers":
            out, self.answers = self.answers, []
            return httpx.Response(200, json={"answers": out})
        return httpx.Response(200, json={"ok": True})

    def sent(self, suffix):
        return [b for p, b in self.calls if p.endswith(suffix)]


def setup(tmp_path, monkeypatch):
    store = GoalStore(tmp_path / "goals.sqlite")
    server = Server()
    http = httpx.Client(transport=httpx.MockTransport(server))
    conn = native_hosted.HostedConnector(store, http, "https://ai.example.com/smara", "dev-token")
    monkeypatch.setattr(conn, "defaults", lambda: {"workspace": str(tmp_path), "profile_id": "synthetic"})
    monkeypatch.setattr(native_goals, "operate",
                        lambda req, store: store.action({k: v for k, v in req.items() if k not in ("use_apps",)} | {"use_apps": True}))
    return store, server, conn


def test_a_phone_task_runs_as_a_goal_and_its_approval_and_result_go_back(tmp_path, monkeypatch):
    store, server, conn = setup(tmp_path, monkeypatch)
    assert conn.pick_up(wait=0)
    started = server.sent("/t-1/started")
    goal_id = started[0]["goal_id"]
    goal = store.claim()
    assert goal["id"] == goal_id and "GST return" in goal["spec"]["objective"] and goal["spec"]["sandbox"] == "workspace-write"

    request_id = store.pending(goal, {"id": 15, "method": "item/commandExecution/requestApproval", "params": {"command": ["dir", "C:\\Reports"]}})
    conn.relay()
    asked = server.sent("/t-1/ask")
    assert asked == [{"request_id": request_id, "summary": "run a command on your PC. Smara recognised no delete, change or internet step in it, but it cannot "
                      "check every effect." + chr(10) * 2 + "Full command: dir C:\\Reports"}]

    server.answers = [{"task_id": "t-1", "request_id": request_id, "kind": "approval", "answer": "allow"}]
    assert conn.apply_answers() == 1
    assert store.consume_response(goal, request_id)["result"] == {"decision": "accept"}

    store.finish(store.get(goal_id) | {"lease": goal["lease"]}, {"summary": "Saved GSTR3B_Oct.pdf in Downloads"}, "ready_for_review", "")
    conn.relay()
    result = server.sent("/t-1/result")
    assert result and result[0]["state"] == "done" and "GSTR3B_Oct.pdf" in result[0]["summary"]
    conn.relay()
    assert len(server.sent("/t-1/result")) == 1                   # reported once


def test_a_code_question_is_asked_in_the_chat_and_the_reply_answers_it(tmp_path, monkeypatch):
    store, server, conn = setup(tmp_path, monkeypatch)
    conn.pick_up(wait=0)
    goal = store.claim()
    request_id = store.pending(goal, {"id": 16, "method": "item/tool/requestUserInput", "params": {
        "questions": [{"id": "otp", "header": "Login", "question": "Enter the OTP sent to your phone for the GST portal"}]}})
    conn.relay()
    ask = server.sent("/t-1/ask")[0]
    assert ask["kind"] == "otp" and "OTP" in ask["summary"]
    server.answers = [{"task_id": "t-1", "request_id": request_id, "kind": "otp", "answer": "482913"}]
    conn.apply_answers()
    assert store.consume_response(goal, request_id)["result"] == {"answers": {"otp": {"answers": ["482913"]}}}


def test_a_phone_task_that_runs_out_of_work_asks_the_owner_and_carries_on(tmp_path, monkeypatch):
    from smara.native_budget import BudgetLimitError
    import pytest
    store, server, conn = setup(tmp_path, monkeypatch)
    conn.pick_up(wait=0)
    goal = store.claim()
    assert goal["spec"]["max_requests"] == native_hosted.STEPS                 # a phone task gets a real allowance
    for _ in range(native_hosted.STEPS):
        store.reserve(goal, 100_000)                                            # ~one desktop model step each
    with pytest.raises(BudgetLimitError):
        store.reserve(goal, 100_000)
    store.finish(store.get(goal["id"]) | {"lease": goal["lease"]}, {"summary": "Cycle interrupted before the final checkpoint."},
                 "paused", "Cumulative goal inference budget reached; extend its limits before resuming")
    conn.relay()
    ask = server.sent("/t-1/ask")
    assert len(ask) == 1 and ask[0]["request_id"].startswith("budget:") and "keep working" in ask[0]["summary"]
    assert not server.sent("/t-1/result")                                       # not reported as failed

    server.answers = [{"task_id": "t-1", "request_id": ask[0]["request_id"], "kind": "approval", "answer": "allow"}]
    conn.apply_answers()
    again = store.get(goal["id"])
    assert again["state"] == "queued" and again["spec"]["max_requests"] == native_hosted.STEPS + native_hosted.EXTRA
    goal = store.claim()
    store.reserve(goal, 100_000)                                                # it can work again

    store.finish(store.get(goal["id"]) | {"lease": goal["lease"]}, {"summary": ""}, "paused", "Cumulative goal budget reached")
    conn.relay()
    second = server.sent("/t-1/ask")[-1]
    server.answers = [{"task_id": "t-1", "request_id": second["request_id"], "kind": "approval", "answer": "deny"}]
    conn.apply_answers()
    result = server.sent("/t-1/result")
    assert result and result[0]["state"] == "failed" and "you chose to stop it" in result[0]["summary"]
    assert "Cycle interrupted" not in result[0]["summary"]


def test_a_failed_task_says_why_in_plain_words(tmp_path, monkeypatch):
    store, server, conn = setup(tmp_path, monkeypatch)
    conn.pick_up(wait=0)
    goal = store.claim()
    store.finish(store.get(goal["id"]) | {"lease": goal["lease"]}, {"summary": "Cycle interrupted before the final checkpoint."},
                 "blocked", "The project folder is no longer available")
    conn.relay()
    text = server.sent("/t-1/result")[0]["summary"]
    assert "The project folder is no longer available" in text and "Cycle interrupted" not in text


def _finish_with(tmp_path, monkeypatch, checkpoint):
    store, server, conn = setup(tmp_path, monkeypatch)
    conn.pick_up(wait=0)
    goal = store.claim()
    store.finish(store.get(goal["id"]) | {"lease": goal["lease"]}, checkpoint, "ready_for_review", "")
    conn.relay()
    return goal, server.sent("/t-1/result")[0]["summary"]


def test_a_result_that_points_at_evidence_reaches_the_phone_with_it(tmp_path, monkeypatch):
    rows = [f"2026-11-2{i} | File | report{i}.pdf | {i}00 bytes" for i in range(5)]
    _, text = _finish_with(tmp_path, monkeypatch, {
        "summary": "Listing succeeded. The newest entries are in evidence below, newest first.",
        "evidence": rows + ["PowerShell process exited with code 0; the command was read-only"]})
    assert all(row in text for row in rows) and "exited with code" not in text


def test_a_one_line_note_over_many_recorded_results_gets_them(tmp_path, monkeypatch):
    rows = [f"process{i} PID {i} CPU {i}.0" for i in range(12)]
    _, text = _finish_with(tmp_path, monkeypatch, {"summary": "Returned the top 12 processes by CPU.", "evidence": rows})
    assert all(row in text for row in rows)


def test_a_complete_reply_is_sent_as_written(tmp_path, monkeypatch):
    complete = "It is a Udyam registration certificate. I left the ID numbers out, as you asked."
    _, text = _finish_with(tmp_path, monkeypatch, {"summary": complete, "evidence": ["Page 1 shows number UDYAM-XX-00-0000000"]})
    assert text == complete


def test_a_reply_carrying_its_own_list_below_is_not_repeated(tmp_path, monkeypatch):
    listed = "The files are listed below:@one.pdf@two.pdf@three.pdf".replace("@", chr(10))
    _, text = _finish_with(tmp_path, monkeypatch, {"summary": listed, "evidence": ["Ran Get-ChildItem"]})
    assert text == listed


def test_a_long_result_is_cut_on_a_whole_line_and_saved_in_full(tmp_path, monkeypatch):
    rows = chr(10).join(f"row {i:04d} " + "x" * 40 for i in range(200))
    goal, text = _finish_with(tmp_path, monkeypatch, {"summary": rows, "evidence": ["observed"], "outputs": ["dues.xlsx"]})
    saved = tmp_path / f"phone-result-{goal['id'][:8]}.txt"
    assert len(text) <= native_hosted.PHONE_CHARS and str(saved) in text and "Files on your PC: dues.xlsx" in text
    assert text.split(chr(10) * 2)[0].endswith("x" * 40)
    assert "row 0199" in saved.read_text(encoding="utf-8-sig")


def test_phone_tasks_say_the_summary_is_the_reply(tmp_path, monkeypatch):
    store, _, conn = setup(tmp_path, monkeypatch)
    conn.pick_up(wait=0)
    assert '"summary" is the whole reply' in store.claim()["spec"]["objective"]


def test_no_project_or_model_tells_the_owner_instead_of_failing_silently(tmp_path, monkeypatch):
    store, server, conn = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(conn, "defaults", lambda: None)
    conn.pick_up(wait=0)
    assert server.sent("/t-1/result")[0]["state"] == "failed" and store.claim() is None


def test_signed_out_pc_does_not_poll(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_BRIDGE_HOME", str(tmp_path / "bridge"))
    assert native_hosted.credentials() is None                     # never signed in
    (tmp_path / "bridge").mkdir()
    (tmp_path / "bridge" / "config.json").write_text(json.dumps({"server": "https://ai.example.com/smara", "token": "dev-token"}))
    assert native_hosted.credentials() == ("https://ai.example.com/smara", "dev-token")
    import hashlib
    (tmp_path / "bridge" / "status.json").write_text(json.dumps({"signed_out": True,
                                                                 "signed_out_for": hashlib.sha256(b"dev-token").hexdigest()[:16]}))
    assert native_hosted.credentials() is None


def test_phone_tasks_get_their_own_project_and_the_owners_selection_stays(tmp_path, monkeypatch):
    from smara import native_management
    from pathlib import Path
    monkeypatch.setattr(native_management, "settings_path", lambda: tmp_path / "native-settings.json")
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "desktop.json"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    mine = tmp_path / "shop"; mine.mkdir()
    native_management.manage({"operation": "add_project", "workspace": str(mine), "label": "shop"})
    native_management.manage({"operation": "save_project_options", "workspace": str(mine),
                              "options": {"tools_enabled": True, "workers_enabled": False, "browser_origins": ["*"]}})
    phone = native_hosted.phone_project()
    prefs = native_management.preferences()
    assert Path(prefs["workspace"]).resolve() == mine.resolve()                   # the owner's project is still selected
    entry = next(p for p in prefs["projects"] if p["workspace"] == phone)
    assert entry["label"] == "Phone tasks" and entry["options"]["browser_origins"] == ["*"]
    assert native_hosted.phone_project() == phone and len(prefs["projects"]) == 2  # created once


def test_phone_tasks_get_the_browser_even_when_the_owners_project_has_none(tmp_path, monkeypatch):
    from smara import native_management
    from pathlib import Path
    monkeypatch.setattr(native_management, "settings_path", lambda: tmp_path / "native-settings.json")
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "desktop.json"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    mine = tmp_path / "shop"; mine.mkdir()
    native_management.manage({"operation": "add_project", "workspace": str(mine), "label": "shop"})
    phone = native_hosted.phone_project()
    entry = next(p for p in native_management.preferences()["projects"] if p["workspace"] == phone)
    assert entry["options"]["browser_origins"] == ["*"] and entry["options"]["tools_enabled"] is True


def test_the_packaged_app_runs_the_browser_helper_without_python(tmp_path):
    """`smara-desktop.exe --browser-bridge <command>` is the helper inside the app (same entry the packaged exe uses)."""
    import os, subprocess, sys
    from pathlib import Path
    src = Path(native_hosted.__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, "-c", "import sys; from smara.native_management import main; sys.exit(main())",
                          "--browser-bridge", "status"], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PYTHONPATH": str(src), "SMARA_BRIDGE_HOME": str(tmp_path / "bridge")})
    assert out.returncode == 0 and "Not paired." in out.stdout
