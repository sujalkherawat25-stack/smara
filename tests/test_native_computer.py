"""Policy tests with a fake backend. Real Windows input is covered by scripts run manually (see docs/computer-use-design.md)."""
import http.server
import threading
import time
import pytest
from smara import native_computer as nc
from smara.native_computer import Computer, grant_path, luhn_card_like, redact
from smara.native_profiles import write_object
from smara.native_windows import parse_chord


def element(index, kind, name, rect=(0, 0, 100, 20), **extra):
    return {"index": index, "type": kind, "name": name, "automation_id": "", "enabled": True, "password": False, "sensitive": False,
            "rect": list(rect), "rel": list(rect), "depth": 0, **extra}


class Fake:
    def __init__(self):
        self.calls, self.elements = [], [element(0, "Edit", "Customer name", (0, 0, 100, 20)), element(1, "Button", "Send invoice", (0, 30, 100, 50)),
                                          element(2, "Button", "Apply", (0, 60, 100, 80)), element(3, "Edit", "Password", (0, 90, 100, 110), password=True, sensitive=True)]
        self.focus = None; self.title = "Fixture"; self.window = {"id": "7", "app": "fixture.exe", "title": "Fixture", "pid": 1, "created": 1.0, "exe": "fixture.exe", "handle": 7}

    def targets(self): return [dict(self.window, foreground=True, minimized=False)]
    def rect(self, _): return [0, 0, 400, 300]
    def observe(self, target, identity, screenshot=True, text=True):
        self.calls.append(("observe", identity))
        return {"elements": [dict(e) for e in self.elements], "focused": self.focus, "image": b"PNG" if screenshot else None, "capture": "window",
                "fingerprint": repr(self.calls[-1:]) + str(len(self.calls)), "rect": [0, 0, 400, 300], "title": self.title, "foreground": True, "truncated": False}
    def focused(self, _): return self.focus
    def click(self, target, x, y, button="left", count=1): self.calls.append(("click", x, y, button, count))
    def click_element(self, target, oid, index, rect, **kw): self.calls.append(("click_element", index, kw))
    def type_text(self, target, text, permitted=lambda: True, check_focus=None):
        if check_focus: check_focus()
        self.calls.append(("type", text))
    def press_key(self, target, chord): self.calls.append(("key", chord))
    def scroll(self, target, x, y, dx, dy): self.calls.append(("scroll", x, y, dx, dy))
    def drag(self, target, *a): self.calls.append(("drag", a))
    def set_value(self, target, oid, index, value): self.calls.append(("set_value", index, value))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARA_DESKTOP_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(nc.time, "sleep", lambda _: None)
    grant = {"id": "g", "enabled": True, "scope": "desktop", "target": {}, "actions": 0, "expires": time.time() + 900}
    write_object(grant_path(tmp_path), grant)
    backend = Fake()
    return Computer(tmp_path, backend), backend, grant, tmp_path


def act(computer, args, confirmed=False):
    obs = computer.observe("7") if computer.latest is None else {"observation_id": computer.latest["observation_id"]}
    return computer.call("computer_act_confirmed" if confirmed else "computer_act", {"observation_id": obs["observation_id"], **args})


def test_observe_gives_numbered_tree_screenshot_and_hides_secret_fields(setup):
    computer, _, _, tmp = setup
    out = computer.call("computer_observe", {"window_id": "7"})
    assert "[0] Edit \"Customer name\"" in out["tree"] and "secret field" in out["tree"] and out["uploaded"] is False
    assert (tmp / ".smara/computer" / (out["observation_id"] + ".png")).read_bytes() == b"PNG"


def test_routine_actions_run_without_confirmation_and_return_fresh_observation(setup):
    computer, backend, _, _ = setup
    first = computer.observe("7")
    result = computer.call("computer_act", {"observation_id": first["observation_id"], "action": "click", "element_index": 0})
    assert result["observation_id"] != first["observation_id"] and ("click_element", 0, {"button": "left", "count": 1}) in backend.calls
    computer.call("computer_act", {"observation_id": result["observation_id"], "action": "type_text", "text": "Sharma"})
    assert ("type", "Sharma") in backend.calls


def test_critical_actions_need_the_confirmed_tool(setup):
    computer, backend, _, _ = setup
    for args in ({"action": "click", "element_index": 1}, {"action": "click", "x": 50, "y": 40}, {"action": "press_key", "key": "Alt+F4"}):
        with pytest.raises(ValueError, match="computer_act_confirmed"): act(computer, args)
    assert not [c for c in backend.calls if c[0] in {"click", "click_element", "key"}]
    act(computer, {"action": "click", "element_index": 1}, confirmed=True)
    assert [c[0] for c in backend.calls if c[0] != "observe"] == ["click_element"]
    assert nc.approval_mode("computer_act") == "approve" and nc.approval_mode("computer_act_confirmed") == "prompt" and nc.approval_mode("excel_save") == "prompt"


def test_tally_accept_shortcuts_are_critical(setup):
    computer, backend, _, _ = setup
    backend.window["app"] = "tally.exe"; backend.elements.append(element(4, "Text", "Accept ? Yes or No"))
    with pytest.raises(ValueError, match="computer_act_confirmed"): act(computer, {"action": "press_key", "key": "Control+a"})
    with pytest.raises(ValueError, match="computer_act_confirmed"): act(computer, {"action": "press_key", "key": "Return"})
    act(computer, {"action": "press_key", "key": "Down"})


def test_secrets_are_never_typed_even_when_confirmed(setup):
    computer, backend, _, _ = setup
    backend.focus = {"type": "Edit", "name": "Password", "sensitive": True, "password": True}
    for confirmed in (False, True):
        with pytest.raises(ValueError, match="Refused"): act(computer, {"action": "type_text", "text": "hunter2"}, confirmed)
    backend.focus = {"type": "Edit", "name": "Customer name", "sensitive": False}; computer.latest = None
    with pytest.raises(ValueError, match="card number"): act(computer, {"action": "type_text", "text": "4111 1111 1111 1111"})
    with pytest.raises(ValueError, match="Secret fields"): act(computer, {"action": "set_value", "element_index": 3, "value": "x"})
    assert not [c for c in backend.calls if c[0] in {"type", "set_value"}]
    act(computer, {"action": "type_text", "text": "Invoice 4111"})  # Not Luhn-valid


def test_focus_changing_to_a_secret_field_stops_typing(setup):
    computer, backend, _, _ = setup
    computer.observe("7")
    backend.focus = {"type": "Edit", "name": "One-time password", "sensitive": True}
    with pytest.raises(ValueError, match="Refused"): computer.call("computer_act", {"observation_id": computer.latest["observation_id"], "action": "type_text", "text": "x"})


def test_observation_is_spent_by_every_action_and_never_blindly_replayed(setup):
    computer, backend, _, _ = setup
    first = computer.observe("7")
    args = {"observation_id": first["observation_id"], "action": "press_key", "key": "Tab"}
    computer.call("computer_act", args)
    with pytest.raises(ValueError, match="Stale"): computer.call("computer_act", args)
    assert len([c for c in backend.calls if c[0] == "key"]) == 1


def test_uncertain_failure_consumes_observation_and_allowance(setup):
    computer, backend, _, tmp = setup
    def boom(*a, **k): raise OSError("SendInput blocked")
    backend.press_key = boom
    first = computer.observe("7")
    args = {"observation_id": first["observation_id"], "action": "press_key", "key": "Tab"}
    with pytest.raises(OSError): computer.call("computer_act", args)
    with pytest.raises(ValueError, match="Stale"): computer.call("computer_act", args)
    from smara.native_computer import selected
    assert selected(tmp)["actions"] == 1


def test_stop_expiry_and_allowance_block_everything(setup):
    computer, backend, grant, tmp = setup
    first = computer.observe("7")
    write_object(grant_path(tmp), {**grant, "actions": nc.ACTION_LIMIT})
    with pytest.raises(ValueError, match="allowance"): computer.call("computer_act", {"observation_id": first["observation_id"], "action": "press_key", "key": "Tab"})
    write_object(grant_path(tmp), {"enabled": False})
    for name, args in (("computer_windows", {}), ("computer_observe", {"window_id": "7"}), ("tally_read", {"report": "status"}), ("excel_live", {"action": "list"})):
        with pytest.raises(ValueError, match="stopped or expired"): computer.call(name, args)
    write_object(grant_path(tmp), {**grant, "expires": time.time() - 1})
    with pytest.raises(ValueError, match="expired"): computer.call("computer_windows", {})


def test_window_scope_grant_hides_other_apps(setup):
    computer, backend, grant, tmp = setup
    write_object(grant_path(tmp), {**grant, "scope": "window", "target": {"pid": 99, "created": 1.0}})
    assert computer.call("computer_windows", {})["windows"] == []
    with pytest.raises(ValueError, match="Unknown window"): computer.observe("7")


def test_window_moved_or_closed_is_rejected_before_input(setup):
    computer, backend, _, _ = setup
    first = computer.observe("7")
    backend.rect = lambda _: [5, 5, 405, 305]
    with pytest.raises(ValueError, match="moved"): computer.call("computer_act", {"observation_id": first["observation_id"], "action": "click", "x": 1, "y": 1})
    assert not backend.calls[1:] or backend.calls[-1][0] == "observe"


def test_malformed_action_arguments_rejected(setup):
    computer, backend, _, _ = setup
    first = computer.observe("7")["observation_id"]
    for bad in ({"action": "click"}, {"action": "click", "element_index": 0, "x": 1, "y": 2}, {"action": "type_text", "text": "a", "x": 1}, {"action": "fly"},
                {"action": "press_key", "key": "Win+r"}, {"action": "press_key", "key": "Meta+r"}, {"action": "type_text", "text": "a\x07"}, {"action": "click", "element_index": 99}):
        with pytest.raises(ValueError): computer.call("computer_act", {"observation_id": first, **bad})
        computer.latest["created"] = time.monotonic()
    assert [c for c in backend.calls if c[0] != "observe"] == []


def test_chord_parser():
    assert parse_chord("Control_L+Shift+a") == ([0x11, 0x10], ord("A"))
    assert parse_chord("Return") == ([], 0x0D) and parse_chord("F2")[1] == 0x71 and parse_chord("KP_5")[1] == 0x65 and parse_chord("ctrl+plus")[1] == 0xBB
    for bad in ("Win+r", "Super", "a+b", "Control+", "Bogus"):
        with pytest.raises(ValueError): parse_chord(bad)


def test_card_detection_and_redaction():
    assert luhn_card_like("pay 4111-1111-1111-1111 now") and not luhn_card_like("Invoice 1234567890123")
    assert "GOCSPX" not in redact("client idGOCSPX-SyntheticFixtureValue000000") and "[redacted-secret]" in redact("password: hunter22")
    assert redact("Ledger: Sundry Debtors 1,250.00") == "Ledger: Sundry Debtors 1,250.00"


def test_mcp_tool_list_has_distinct_approval_classes():
    names = {t["name"]: nc.approval_mode(t["name"]) for t in nc.TOOLS}
    assert names["computer_act"] == "approve" and names["computer_act_confirmed"] == "prompt" and names["tally_read"] == "approve"


# ---- Tally read path against a fake local server -------------------------------------------------
TALLY_XML = (b'<?xml version="1.0"?><ENVELOPE><BODY><DATA><COLLECTION>'
             b'<LEDGER NAME="Cash"><NAME>Cash</NAME><PARENT>Cash-in-Hand</PARENT><CLOSINGBALANCE>-1250.00</CLOSINGBALANCE></LEDGER>'
             b'<LEDGER NAME="Sharma &amp; Co"><NAME>Sharma &amp; Co</NAME><PARENT>Sundry Debtors</PARENT><CLOSINGBALANCE>&#4; 500.00</CLOSINGBALANCE></LEDGER>'
             b'</COLLECTION></DATA></BODY></ENVELOPE>')


@pytest.fixture
def tally_server():
    seen = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
            self.send_response(200); self.send_header("Content-Length", str(len(TALLY_XML))); self.end_headers(); self.wfile.write(TALLY_XML)
        def log_message(self, *a): pass
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], seen
    server.shutdown()


def test_tally_read_parses_tally_xml_and_only_sends_export_requests(setup, tally_server):
    port, seen = tally_server
    computer = setup[0]
    out = computer.call("tally_read", {"report": "ledgers", "company": "Demo & Co", "port": port})
    assert out["read_only"] and out["row_count"] == 2 and out["rows"][1]["NAME"] == "Sharma & Co" and out["rows"][1]["CLOSINGBALANCE"] == "500.00"
    assert "<TALLYREQUEST>Export</TALLYREQUEST>" in seen[0] and "Demo &amp; Co" in seen[0] and "Import" not in seen[0]
    computer.call("tally_read", {"report": "vouchers", "voucher_type": 'Sales"', "from_date": "20250401", "to_date": "20260331", "port": port})
    assert "<SVFROMDATE>20250401</SVFROMDATE>" in seen[1] and '= "Sales"</SYSTEM>' in seen[1]
    with pytest.raises(ValueError, match="YYYYMMDD"): computer.call("tally_read", {"report": "vouchers", "from_date": "1-4-2025", "port": port})
    with pytest.raises(ValueError, match="Invalid"): computer.call("tally_read", {"report": "ledgers", "host": "evil"})


def test_tally_unreachable_is_a_clear_error(setup):
    with pytest.raises(ValueError, match="not reachable"): setup[0].call("tally_read", {"report": "status", "port": 1})
