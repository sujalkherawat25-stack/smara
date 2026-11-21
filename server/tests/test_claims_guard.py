"""The owner is told a reminder is set or a file is sent only when this turn really did it."""
from src.agent_loop import unbacked_claim


def turn(owner_text, *calls_and_results):
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": owner_text}]
    for i, (name, result) in enumerate(calls_and_results):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": result})
    return msgs


def test_a_reminder_claimed_without_schedule_followup_is_caught():
    note = unbacked_claim(turn("Raat 11 baje yaad dilana shutter band karna hai"), "Theek hai, kal subah 8 baje yaad dila dungi.")
    assert "schedule_followup" in note and "never be reminded" in note
    assert unbacked_claim(turn("kal 10 baje yaad dilana", ("schedule_followup", '{"task_id": "t1", "will_run_at_ist": "2026-11-22 10:00"}')), "Kal 10 baje yaad dila dungi.") == ""


def test_a_standing_rule_saved_to_memory_is_not_a_reminder_claim():
    """'jab bhi bill bhejun, 2 din pehle yaad dila dena' is a rule for later: saving it and saying 'aage se yaad dila dungi' must not push the agent
    into scheduling a made-up reminder now (it once set a daily self-check)."""
    rule = "Ek rule yaad rakh lo: jab bhi main bijli ka bill bhejun, last date se 2 din pehle yaad dila dena."
    saved = turn(rule, ("write_continuum_evidence", '{"saved": true}'))
    assert unbacked_claim(saved, "Yaad rakh liya. Aage se jab bhi bill bhejenge, 2 din pehle khud yaad dila dungi.") == ""
    assert unbacked_claim(turn(rule), "Yaad rakh liya, aage se yaad dila dungi.") != ""                       # nothing saved: still caught
    assert unbacked_claim(turn("kal subah yaad dilana", ("write_continuum_evidence", '{"saved": true}')), "Kal subah yaad dila dungi.") != ""   # a plain reminder ask is not a rule


def test_an_amount_question_answered_with_no_amount_is_sent_back_once_with_the_question():
    """A bill with 'kitna dena hai aur last date kya hai?' answered only with 'reminder set 3 Dec' (6 of 8 runs after a standing rule was saved)."""
    ask = "[The owner attached a file: file_ref=local:x:bill.pdf; caption: Ye lo is mahine ka bijli bill. Kitna dena hai aur last date kya hai?]"
    work = (("read_attachment", '{"kind": "pdf", "text": "Net Payable Rs. 8,803.00"}'), ("schedule_followup", '{"will_run_at_ist": "2026-12-03 09:00"}'))
    note = unbacked_claim(turn(ask, *work), "Reminder set ho gaya - 3 Dec subah 9 baje yaad dila dungi.")
    assert "asked for an amount" in note and "no amount" in note
    assert unbacked_claim(turn(ask, *work), "Dena hai *Rs 8,803*, last date 5 Dec 2026. Reminder 3 Dec subah 9 baje.") == ""
    assert unbacked_claim(turn(ask, *work), "Dena hai ₹12000 tak.") == ""
    assert unbacked_claim(turn(ask, *work), "Last date 5 Dec 2026 hai, reminder 3 Dec ko.") != ""               # a year and dates are not an amount
    assert unbacked_claim(turn(ask, *work), "Kaun sa bill hai, bijli ya paani?") == ""                           # a clarifying question is fine
    assert unbacked_claim(turn("Ye lo bill", *work), "Reminder set ho gaya - 3 Dec subah 9 baje yaad dila dungi.") == ""     # no amount was asked for
    assert unbacked_claim(turn("kitna dena hai?"), "Bill dekhna padega, bhej dijiye.") == ""                       # nothing was looked up this turn: nothing to repeat


def test_a_question_or_a_list_of_reminders_is_not_a_claim():
    assert unbacked_claim(turn("raat 11 baje yaad dilana"), "11 baje ka reminder subah 8 baje pe chalega. Laga doon?") == ""
    assert unbacked_claim(turn("mere reminders dikhao"), "Aapke 3 reminders: ⏰ 22 Nov GSTR-3B, ⏰ 23 Nov Ravi, ⏰ 24 Nov bank statement.") == ""
    assert unbacked_claim(turn("raat 11 baje yaad dilana"), "Aap kya chahte hain?\n1. Subah 8 baje\n2. Aaj 8:55 pm") == ""


def test_a_file_claimed_without_a_successful_send_is_caught():
    note = unbacked_claim(turn("file bhej do", ("run_python", '{"exit_code": 0}')), "File bhej di hai, dekh lijiye.")
    assert "send_file_to_owner" in note
    assert unbacked_claim(turn("file bhej do", ("send_file_to_owner", '{"sent": true, "via": "telegram", "filename": "r.xlsx"}')), "File bhej di hai.") == ""
    assert unbacked_claim(turn("brs bana do", ("bank_reconcile", '{"files_sent_to_owner": ["Bank_reconciliation.xlsx", "report.pdf"]}')), "Excel bhej diya.") == ""
    assert unbacked_claim(turn("file bhej do", ("send_file_to_owner", '{"sent": false, "error": "not sent"}')), "File bhej di hai.") != ""


def test_only_this_turns_actions_count():
    msgs = turn("pehle ka", ("send_file_to_owner", '{"sent": true}')) + [{"role": "assistant", "content": "done"}, {"role": "user", "content": "ek aur file bhej do"}]
    assert unbacked_claim(msgs, "File bhej di hai.") != ""                        # the earlier send does not back today's claim
