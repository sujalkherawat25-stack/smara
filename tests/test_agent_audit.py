"""The owner's local audit log: what is written, what is scrubbed, what the runtime hook records, and deletion."""
import datetime
import json

import pytest

from smara import agent_audit as audit


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_AUDIT_DIR", str(tmp_path / "audit"))
    return tmp_path / "audit"


def test_record_and_list_newest_first_with_filters():
    audit.record("desktop", "browser", "browser_navigate", task="t1", target="url=https://gst.gov.in")
    audit.record("bridge", "browser", "click", task="j9", target='[4] button "Search"')
    audit.record("desktop", "computer", "computer_act", task="t1")
    rows = audit.entries()
    assert [r["action"] for r in rows] == ["computer_act", "click", "browser_navigate"]
    assert [r["action"] for r in audit.entries(task="t1", kind="browser")] == ["browser_navigate"]
    assert [r["source"] for r in audit.entries(source="bridge")] == ["bridge"]
    assert set(rows[0]) == set(audit.FIELDS)


def test_secrets_cards_and_secret_fields_are_never_written(folder):
    audit.record("desktop", "browser", "browser_type", target="api_key=sk-abcdefghijklmnopqrstu",
                 detail={"arguments": {"text": "card 4111 1111 1111 1111 ok", "password": "hunter2", "otp": 123456,
                                       "note": "Bearer abcdefghijklmnop1234"}})
    raw = next(folder.glob("*.jsonl")).read_text(encoding="utf-8")
    for leaked in ("sk-abcdefghijklmnopqrstu", "4111 1111 1111 1111", "hunter2", "123456", "abcdefghijklmnop1234"):
        assert leaked not in raw
    row = audit.entries()[0]
    assert row["detail"]["arguments"]["password"] == "[removed]" and "[card number removed]" in row["detail"]["arguments"]["text"]


def test_non_card_numbers_stay_readable():
    assert audit.scrub("Invoice 1234567890123 total 1,499") == "Invoice 1234567890123 total 1,499"   # fails Luhn: not a card


def test_screenshots_stay_inside_the_audit_folder(folder, tmp_path):
    entry = audit.record("desktop", "computer", "computer_observe", screenshot=b"\x89PNG fake")
    assert audit.screenshot_path(entry["screenshot"]).read_bytes() == b"\x89PNG fake"
    shot = audit.manage({"operation": "audit", "action": "shot", "path": entry["screenshot"]})
    assert shot["png_base64"]
    (tmp_path / "x.png").write_bytes(b"no")
    for bad in ("../x.png", "shots/../../x.png", "2026-01-01.desktop.jsonl"):
        with pytest.raises(ValueError):
            audit.screenshot_path(bad)


def test_delete_a_day_or_everything(folder):
    entry = audit.record("desktop", "browser", "browser_click", screenshot=b"png")
    today = datetime.date.today().isoformat()
    old = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    (folder / f"{old}.bridge.jsonl").write_text(json.dumps({"ts": 1, "action": "click"}) + "\n", encoding="utf-8")
    assert [d["day"] for d in audit.manage({"operation": "audit", "action": "days"})["days"]] == [today, old]
    assert audit.prune() == 1 and [d["day"] for d in audit.days()] == [today]
    audit.manage({"operation": "audit", "action": "delete", "day": today})
    assert audit.days() == [] and not (folder / entry["screenshot"]).exists()
    audit.record("desktop", "browser", "browser_click")
    audit.manage({"operation": "audit", "action": "delete", "all": True})
    assert not folder.exists()
    with pytest.raises(ValueError):
        audit.manage({"operation": "audit", "action": "delete"})
    with pytest.raises(ValueError):
        audit.manage({"operation": "audit", "action": "list", "day": "../../etc"})


def _call(server, tool, arguments, status="completed", thread="th1", text="ok"):
    return {"method": "item/completed", "params": {"threadId": thread, "item": {
        "type": "mcpToolCall", "id": "i", "server": server, "tool": tool, "status": status, "arguments": arguments,
        "result": {"content": [{"type": "text", "text": text}]}, "error": None, "durationMs": 120}}}


def test_runtime_hook_records_tool_calls_approvals_and_downloads(tmp_path):
    downloads = tmp_path / "browser"
    downloads.mkdir()
    (downloads / "page-1.png").write_bytes(b"old")
    hook = audit.RuntimeAuditor(download_dir=downloads)
    hook.from_runtime({"id": 7, "method": "mcpServer/elicitation/request",
                       "params": {"threadId": "th1", "serverName": "playwright", "message": "Allow browser_click on Download?"}})
    hook.from_owner({"id": 7, "result": {"action": "accept", "content": {}}})
    (downloads / "gstr2b.json").write_text("{}")
    hook.from_runtime(_call("playwright", "browser_click", {"element": "Download", "ref": "e12"}))
    (downloads / "page-2.png").write_bytes(b"screenshot")                      # the tool's own screenshot, not a download
    hook.from_runtime(_call("playwright", "browser_take_screenshot", {}))
    hook.from_runtime(_call("smara_computer", "computer_act", {"observation_id": "o", "action": "type_text", "text": "Rs 500"}))
    hook.from_runtime({"method": "item/completed", "params": {"item": {"type": "agentMessage", "text": "hi"}}})  # not a tool
    rows = list(reversed(audit.entries()))
    assert [(r["kind"], r["action"]) for r in rows] == [
        ("approval", "approved"), ("browser", "browser_click"), ("browser", "download_saved"),
        ("browser", "browser_take_screenshot")]          # computer steps are logged by the computer server itself, with a screenshot
    assert rows[0]["detail"]["asked"].startswith("Allow browser_click") and rows[0]["task"] == "th1"
    assert rows[1]["target"] == "element=Download" and rows[1]["outcome"].startswith("completed")
    assert rows[2]["target"] == "gstr2b.json"


def test_runtime_hook_never_raises_on_odd_messages():
    hook = audit.RuntimeAuditor()
    for message in (None, [], {"method": "item/completed", "params": None}, {"id": 1}, {"id": 2, "result": None}):
        hook.from_runtime(message)
        hook.from_owner(message)
