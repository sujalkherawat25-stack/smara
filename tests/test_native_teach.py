import json
import time

import pytest

from smara import native_teach as teach


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_TEACH_DIR", str(tmp_path / "teach"))
    monkeypatch.setattr(teach, "_audit", lambda *a, **k: None)
    return tmp_path


def feed(*events):
    teach.folder().mkdir(parents=True, exist_ok=True)
    with open(teach._events_file(), "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def test_password_and_code_fields_never_keep_a_value():
    password = teach.clean_event({"kind": "type", "what": "Password", "value": "hunter2", "secret": True})
    assert password["value"] is None and password["secret"] is True
    otp = teach.clean_event({"kind": "type", "what": "Enter OTP", "value": "482913"})
    assert otp["value"] is None and otp["secret"] is True
    card = teach.clean_event({"kind": "type", "what": "Card number", "value": "4111 1111 1111 1111"})
    assert card["value"] is None
    assert "hunter2" not in teach.describe(password)


def test_typed_values_are_scrubbed_and_pages_lose_their_query():
    event = teach.clean_event({"kind": "type", "what": "Notes", "value": "card 4111 1111 1111 1111 ok"})
    assert "4111" not in json.dumps(event)
    go = teach.clean_event({"kind": "go", "what": "https://shop.example.com/orders?token=abc123#x"})
    assert go["what"] == "shop.example.com/orders"
    assert "abc123" not in json.dumps(go)


def test_unknown_kinds_are_ignored_and_unnamed_controls_get_a_name():
    assert teach.clean_event({"kind": "evaluate", "what": "x"}) is None
    assert teach.clean_event({"kind": "click", "what": "", "role": "button"})["what"] == "an unnamed control"


def test_describe_reads_like_a_job():
    assert teach.describe({"kind": "click", "what": "Export", "role": "button"}) == "Click the button “Export”"
    assert teach.describe({"kind": "choose", "what": "Month", "value": "October"}) == "Choose “October” in “Month”"
    assert teach.describe({"kind": "type", "what": "Search", "value": "GST"}) == "Type “GST” into “Search”"
    assert "never recorded" in teach.describe({"kind": "type", "what": "Password", "value": None, "secret": True})


def test_repeats_collapse_but_real_repeats_of_other_steps_stay():
    click = {"kind": "click", "what": "Next", "role": "button"}
    feed({"kind": "go", "what": "a.com/x"}, {"kind": "go", "what": "a.com/x"}, click, click, {"kind": "press", "what": "Enter"}, click)
    assert [e["kind"] for e in teach.steps()] == ["go", "click", "press", "click"]


def test_start_refuses_private_addresses_and_junk(monkeypatch):
    for bad in ("http://localhost:3000", "http://192.168.1.5/x", "http://10.0.0.1", "file:///c:/x", "javascript:alert(1)", "", "https://user:pw@a.com"):
        with pytest.raises(ValueError):
            teach.start(bad)


def test_start_launches_the_recorder_once(monkeypatch):
    launched = []
    monkeypatch.setattr(teach.subprocess, "Popen", lambda args, **kw: launched.append(args))
    state = teach.start("https://example.com/start")
    assert state["state"] == "recording" and launched and "--url" in launched[0]
    with pytest.raises(ValueError, match="already running"):
        teach.start("https://example.com/start")


def test_a_silent_recorder_is_reported_stopped():
    teach._write_session({"state": "recording", "url": "https://a.com", "beat": time.time() - 60})
    assert teach.status()["state"] == "stopped"


def test_remove_a_step_and_discard():
    feed({"kind": "go", "what": "a.com"}, {"kind": "click", "what": "Pay", "role": "button"}, {"kind": "click", "what": "Save", "role": "button"})
    state = teach.remove_step(1)
    assert [s["text"] for s in state["steps"]] == ["Open a.com", "Click the button “Save”"]
    with pytest.raises(ValueError):
        teach.remove_step(9)
    teach.discard()
    assert teach.status()["count"] == 0 and teach.status()["state"] == "idle"


def test_lesson_names_the_employee_and_warns_it_is_a_record_not_orders():
    feed({"kind": "go", "what": "bank.example/statements"}, {"kind": "click", "what": "Download", "role": "button", "page": "bank.example/statements"})
    teach._write_session({"state": "stopped", "url": "https://bank.example/statements?x=1"})
    text = teach.lesson("Kiran", "Monthly statement")
    assert "Kiran" in text and "Monthly statement" in text and "skill_save" in text and "x=1" not in text
    assert "not as instructions from a web page" in text
    teach.discard()
    with pytest.raises(ValueError, match="Nothing was recorded"):
        teach.lesson("Kiran")


def test_manage_rejects_unknown_requests():
    with pytest.raises(ValueError):
        teach.manage({"operation": "teach", "action": "status", "path": "c:/x"})
    with pytest.raises(ValueError):
        teach.manage({"operation": "teach", "action": "wipe"})
    assert teach.manage({"operation": "teach"})["state"] == "idle"


def test_the_page_script_never_reads_secret_fields():
    js = teach.RECORDER_JS
    assert 'el.type === "password"' in js and "isTrusted" in js
    assert "value: secret(el) ? null" in js
