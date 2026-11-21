"""Ageing from a list of open invoices: buckets from the invoice date or past the due date, party-wise, oldest bucket, credit notes."""
import asyncio
import io
from datetime import date, timedelta
from types import SimpleNamespace

from openpyxl import Workbook

from evals import ca_data as cd
from src.agent import actions, ageing, ca_reports, documents, extras
from src.agent.policy import SessionCtx, pre_tool_use
from src.agent.tools import TOOL_NAMES

ASOF = date(2026, 10, 31)


def xlsx(rows) -> bytes:
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_the_planted_register_ages_to_the_independent_key():
    f, t = cd.ageing()
    p = ageing.parse_file("open.xlsx", f["receivables_open.xlsx"])
    x = ageing.age(p["rows"], ASOF, "invoice_date")
    assert x["buckets"] == {k: float(v) for k, v in t["buckets"].items()} and x["total_rupees"] == float(t["total"])
    top = x["top_parties_in_90+"][:3]
    assert [i["party"] for i in top] == t["top90"] and [i["rupees"] for i in top] == [float(a) for a in t["top90_amounts"]]
    star = t["top90"][0]
    row = next(r for r in x["party_wise"] if r["party"] == star)
    assert {b: row[b] for b in x["buckets"]} == {b: float(t["party_buckets"][star].get(b, 0)) for b in x["buckets"]}


def test_day_counts_at_the_edges_of_each_bucket():
    rows = [{"party": "A", "invoice": str(i), "date": ASOF - timedelta(days=d), "due": None, "amount": 100} for i, d in enumerate((0, 30, 31, 60, 61, 90, 91))]
    assert ageing.age(rows, ASOF, "invoice_date")["buckets"] == {"0-30": 2.0, "31-60": 2.0, "61-90": 2.0, "90+": 1.0}


def test_past_due_basis_has_a_not_yet_due_bucket():
    rows = [{"party": "A", "invoice": "1", "date": None, "due": date(2026, 11, 10), "amount": 500}, {"party": "A", "invoice": "2", "date": None, "due": date(2026, 10, 1), "amount": 300}]
    assert ageing.age(rows, ASOF, "due_date")["buckets"] == {"Not yet due": 5.0, "1-30": 3.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}


def test_credit_notes_stay_in_their_bucket_and_are_named_and_undated_rows_are_not_hidden():
    rows = [{"party": "A", "invoice": "1", "date": date(2026, 10, 20), "due": None, "amount": 1000}, {"party": "A", "invoice": "2", "date": date(2026, 10, 25), "due": None, "amount": -300},
            {"party": "B", "invoice": "3", "date": None, "due": None, "amount": 900}]
    x = ageing.age(rows, ASOF, "invoice_date")
    assert x["buckets"]["0-30"] == 7.0 and x["credit_notes_and_advances_rupees"] == -3.0 and x["invoices_without_a_date"] == 1
    text = " ".join(ageing.summary_lines(x))
    assert "credit notes" in text.lower() and "NOT in the buckets" in text


def test_the_tool_sends_the_files_says_how_the_days_were_counted_and_checks_the_file_is_the_owners(monkeypatch):
    f, t = cd.ageing()
    files = {"local:a:open.xlsx": ("open.xlsx", f["receivables_open.xlsx"])}

    async def fetch(app, ref, ctx=None):
        n, d = files[ref]
        return d, "", n
    sent = []

    async def send(app, ctx, args):
        sent.append(args["file_ref"])
        return {"sent": True}

    class Docs:
        async def make_document(self, app, ctx, args):
            return {"file_ref": "sandbox:j:a.pdf", "filename": "a.pdf", "_new_files": ["sandbox:j:a.pdf"]}

    async def excel(app, name, sheets):
        assert [s[0] for s in sheets] == ["Summary", "Party-wise", "Invoice-wise"]
        return f"sandbox:j:{name}", name
    monkeypatch.setattr(actions, "_fetch", fetch)
    monkeypatch.setattr(extras, "send_file_to_owner", send)
    monkeypatch.setattr(documents, "make_document", Docs().make_document)
    monkeypatch.setattr(ca_reports, "excel_file", excel)
    ctx = SessionCtx("s", "t", "TRUSTED", files=tuple(files))
    r = asyncio.run(ageing.ageing_report(SimpleNamespace(), ctx, {"file_ref": "local:a:open.xlsx", "as_on": "2026-10-31"}))
    assert len(sent) == 2 and r["buckets"]["90+"] == 72769930.0 and r["basis"] == "invoice_date" and "invoice date" in r["next"]
    assert "error" in asyncio.run(ageing.ageing_report(SimpleNamespace(), ctx, {"file_ref": "local:a:open.xlsx", "as_on": "tomorrow-ish"}))
    files["local:n:n.xlsx"] = ("n.xlsx", xlsx([["Party", "Invoice Date", "Outstanding"], ["A", "01-10-2026", 100]]))
    ctx2 = SessionCtx("s", "t", "TRUSTED", files=tuple(files))
    assert "no due date column" in asyncio.run(ageing.ageing_report(SimpleNamespace(), ctx2, {"file_ref": "local:n:n.xlsx", "basis": "due_date"}))["error"]
    assert "ageing_report" in TOOL_NAMES and pre_tool_use(ctx, "ageing_report", {"file_ref": "local:a:open.xlsx"}, TOOL_NAMES).verdict == "allow"
    assert pre_tool_use(SessionCtx("s", "t", "UNTRUSTED", party_id="p"), "ageing_report", {"file_ref": "local:a:open.xlsx"}, TOOL_NAMES).verdict == "deny"
