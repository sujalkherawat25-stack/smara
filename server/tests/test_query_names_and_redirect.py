"""query_spreadsheet gathers the spellings of one party; a specialist tool's work is not redone by hand; a list request is not a chase."""
import json
import tempfile
from pathlib import Path

from evals import ca_data as cd
from src.agent import sheet_engine as se
from src.agent_loop import owes_card, specialist_redirect


def test_group_by_name_gathers_spellings_and_keeps_look_alikes_apart_for_the_owner():
    f, t = cd.dirty_parties()
    p = Path(tempfile.mkdtemp()) / "ledger.xlsx"
    p.write_bytes(f["sales_ledger_oct.xlsx"])
    a = se.query(str(p), [{"group_by": {"column": "Party", "by": "name"}, "measures": [{"op": "sum", "of": "Amount", "as": "total"}], "limit": 40}])["answers"][0]
    rows = {x["Party"]: x for x in a["rows"]}
    assert a["groups"] == 18
    assert all(rows[n]["total"] == v for n, v in t["merged"].items())                         # case, M/s, Messrs, a trailing dot: one party
    assert [rows[n]["total"] for n in ("Zenith Traders", "Zenith Trading Co", "Orion Agencies", "Orion Agency")] == [t["ambiguous"][n] for n in ("Zenith Traders", "Zenith Trading Co", "Orion Agencies", "Orion Agency")]
    assert "M/s Apex Electricals" in rows["Apex Electricals"]["spellings_gathered"] and len(rows["Apex Steel"]["spellings_gathered"]) == 3
    pairs = {frozenset((x["a"], x["b"])) for x in a["names_that_may_be_the_same"]}
    assert frozenset(("Zenith Traders", "Zenith Trading Co")) in pairs and frozenset(("Orion Agencies", "Orion Agency")) in pairs
    assert "keep" in a["names_note"] or "kept apart" in a["names_note"]


def call(name, args, cid):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def result(cid, content):
    return {"role": "tool", "tool_call_id": cid, "content": content}


GST = {"books_file_ref": "local:a:books.xlsx", "gstr2b_file_ref": "local:b:2b.xlsx"}


def test_redoing_a_finished_reconciliation_with_code_is_turned_back_once_then_allowed():
    done = [{"role": "user", "content": "match karo"}, call("gst_reconcile", GST, "c1"), result("c1", '{"counts": {"matched": 358}, "claimable_itc_rupees": 7286766.86}')]
    first = specialist_redirect(done, "run_python", {"code": "x", "file_refs": ["local:a:books.xlsx", "local:b:2b.xlsx"]})
    assert first["redirected"] and "gst_reconcile already did this" in first["error"] and "summary_lines" in first["error"]
    again = done + [call("run_python", {"code": "x"}, "c2"), result("c2", json.dumps(first))]
    assert specialist_redirect(again, "run_python", {"code": "x", "file_refs": ["local:a:books.xlsx", "local:b:2b.xlsx"]}) is None      # the second try runs


def test_other_work_on_the_same_files_and_failed_tool_runs_are_not_turned_back():
    ok = [call("gst_reconcile", GST, "c1"), result("c1", '{"counts": {}}')]
    assert specialist_redirect(ok, "run_python", {"code": "x", "file_refs": ["local:a:books.xlsx"]}) is None              # one of the two files only
    assert specialist_redirect(ok, "run_python", {"code": "x", "file_refs": ["local:z:other.xlsx", "local:y:more.xlsx"]}) is None
    assert specialist_redirect(ok, "open_spreadsheet", {"file_refs": list(GST.values())}) is None
    failed = [call("gst_reconcile", GST, "c1"), result("c1", '{"error": "could not read the file"}')]
    assert specialist_redirect(failed, "run_python", {"code": "x", "file_refs": list(GST.values())}) is None            # it did not work: code is the fallback
    bank = [call("bank_reconcile", {"book_file_ref": "local:1:b.xlsx", "bank_file_ref": "local:2:s.xlsx"}, "c9"), result("c9", '{"reconciles": true}')]
    assert specialist_redirect(bank, "run_python", {"code": "x", "file_refs": ["local:2:s.xlsx", "local:1:b.xlsx"]})["redirected"]


def test_asking_to_see_or_cancel_reminders_is_not_a_request_to_chase_a_customer():
    def msgs(text):
        return [{"role": "user", "content": text}, {"role": "assistant", "content": "ok"}]
    assert not owes_card(msgs("Mere saare reminders dikhao"), "Aapke 3 reminders hain")
    assert not owes_card(msgs("GSTR reminder hata do"), "Hata diya")
    assert owes_card(msgs("Ravi ko payment ka reminder bhejo"), "Draft ready hai")
