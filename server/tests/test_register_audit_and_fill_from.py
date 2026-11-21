"""audit_gst_register (the six checks an accountant makes on a sales register) and edit_spreadsheet's fill_from (a lookup that never guesses),
both against planted-error files whose answer keys were derived twice (evals/verify_truth.py)."""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook

from evals import ca_data as cd
from evals import ca_small as cs
from src.agent import actions, ca_reports, documents, extras, register_audit as ra
from src.agent import sheet_engine as se
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES, bounded


def test_the_audit_finds_exactly_the_planted_problems_and_no_others():
    f, t = cd.sales_register()
    p = ra.parse_file("sales_register_q2.xlsx", f["sales_register_q2.xlsx"])
    a = ra.audit(p["rows"], p["cols"])
    assert a["own_state_code"] == "07" and a["rows_checked"] == t["rows"]
    want = {"wrong_head": sorted(t["wrong_head_igst_on_intra"] + t["wrong_head_cgst_sgst_on_inter"]), "total_mismatch": t["total_mismatch"], "entered_twice": t["duplicates"],
            "b2b_missing_gstin": t["b2b_missing_gstin"], "invalid_gstin": t["invalid_gstin"], "rate_mismatch": t["rate_mismatch"]}
    for k, v in want.items():
        assert sorted(r["invoice"] for r, _ in a["found"][k]) == v, k
    everything = {r["invoice"] for lst in a["found"].values() for r, _ in lst}
    assert not (everything & set(t["credit_notes_not_errors"]))                         # a credit note is not an error


def test_the_own_state_can_be_given_and_a_register_without_a_type_column_still_checks_gstins():
    rows = [{"row": 2, "invoice": "A1", "date": "", "party": "x", "gstin": "07AAAPA1234A1ZZ", "type": "", "place": "07", "place_text": "Delhi", "taxable": 10000, "cgst": 0, "sgst": 0,
             "igst": 1800, "total": 11800, "rate": 18.0},
            {"row": 3, "invoice": "A2", "date": "", "party": "y", "gstin": "", "type": "", "place": "27", "place_text": "Maharashtra", "taxable": 10000, "cgst": 900, "sgst": 900, "igst": 0,
             "total": 11800, "rate": 18.0}]
    a = ra.audit(rows, {"cgst", "sgst", "igst", "place"}, own_state="Delhi")
    assert a["own_state_code"] == "07" and a["counts"]["wrong_head"] == 2 and a["counts"]["invalid_gstin"] == 1 and a["counts"]["b2b_missing_gstin"] == 0


def test_the_tool_sends_a_problems_only_excel_and_its_result_arrives_whole(monkeypatch):
    f, t = cd.sales_register()
    files = {"r1": ("sales_register_q2.xlsx", f["sales_register_q2.xlsx"])}

    async def fetch(app, ref, ctx=None):
        n, d = files[ref]
        return d, "", n
    sent, sheets_seen = [], []

    async def send(app, ctx, args):
        sent.append(args["file_ref"])
        return {"sent": True}

    async def make(app, ctx, args):
        return {"file_ref": "sandbox:j:r.pdf", "filename": "r.pdf", "_new_files": ["sandbox:j:r.pdf"]}

    async def excel(app, name, sheets):
        sheets_seen.extend(sheets)
        return f"sandbox:j:{name}", name
    monkeypatch.setattr(actions, "_fetch", fetch)
    monkeypatch.setattr(extras, "send_file_to_owner", send)
    monkeypatch.setattr(documents, "make_document", make)
    monkeypatch.setattr(ca_reports, "excel_file", excel)
    ctx = SessionCtx("s", "t", "TRUSTED", files=tuple(files))
    r = asyncio.run(ra.audit_gst_register(SimpleNamespace(), ctx, {"file_ref": "r1"}))
    assert len(sent) == 2 and r["counts"]["wrong_head"] == 17 and list(r)[0] == "summary_lines" and "[truncated]" not in bounded(r)
    problems = next(s for s in sheets_seen if s[0] == "Problems")[2]
    assert len(problems) == sum(r["counts"].values()) == 46 and "never recheck" in r["next"]            # only the problem entries go to the Excel
    assert "audit_gst_register" in TOOL_NAMES and pre_tool_use(ctx, "audit_gst_register", {"file_ref": "r1"}, TOOL_NAMES).verdict == "allow"
    assert pre_tool_use(SessionCtx("s", "t", "UNTRUSTED", party_id="p"), "audit_gst_register", {"file_ref": "r1"}, TOOL_NAMES).verdict == "deny"


def test_fill_from_fills_what_is_certain_and_leaves_the_rest_empty_and_listed(tmp_path):
    f, t = cs.gstin_fill()
    (tmp_path / "s.xlsx").write_bytes(f["sales_with_master.xlsx"])
    r = se.apply(str(tmp_path / "s.xlsx"), str(tmp_path / "o.xlsx"),
                 [{"action": "fill_from", "sheet": "Sales", "column": "GSTIN", "match_on": "Party Name", "from_sheet": "Party Master", "from_value": "GSTIN"}],
                 owner_words="Sales sheet mein jahan GSTIN khali hai wahan Party Master sheet se bhar do, party name match karke")
    assert r["saved"] and r["verified"] and r["cells_to_change"] == len(t["fill"])
    c = r["changes"][0]
    assert sorted(x["name"] for x in c["left_empty_two_values_in_the_other_sheet"]) == t["ambiguous"]
    assert sorted(x["name"] for x in c["left_empty_not_found_in_the_other_sheet"]) == t["absent"]
    ws = load_workbook(tmp_path / "o.xlsx")["Sales"]
    by = {ws.cell(row=i, column=1).value: ws.cell(row=i, column=4).value for i in range(2, ws.max_row + 1)}
    assert all(by[k] == v for k, v in t["fill"].items())
    assert not any(by.get(i) for i in t["ambiguous_invoices"] + t["absent_invoices"])                    # never guessed
    again = se.apply(str(tmp_path / "o.xlsx"), str(tmp_path / "o2.xlsx"),
                     [{"action": "fill_from", "sheet": "Sales", "column": "GSTIN", "match_on": "Party Name", "from_sheet": "Party Master"}],
                     owner_words="GSTIN khali hai wahan Party Master se bhar do")
    assert again.get("cells_to_change", 0) == 0 or again["changes"][0]["rows_changed"] == 0           # filling twice changes nothing: only empty cells are touched


def test_fill_from_names_what_is_wrong_instead_of_failing_quietly(tmp_path):
    f, _ = cs.gstin_fill()
    (tmp_path / "s.xlsx").write_bytes(f["sales_with_master.xlsx"])
    bad = se.apply(str(tmp_path / "s.xlsx"), str(tmp_path / "x.xlsx"), [{"action": "fill_from", "sheet": "Sales", "column": "GSTIN", "match_on": "Party Name", "from_sheet": "Nope"}],
                   owner_words="GSTIN bharo")
    assert "no sheet called 'Nope'" in bad["error"] and bad["nothing_changed"]
