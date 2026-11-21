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
