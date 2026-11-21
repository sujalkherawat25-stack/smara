"""A tool result is cut at 6,000 characters before the model reads it. The reconciliation, bank and ageing tools used to put their sentences
to quote and their instruction at the END of a 15,000-character result, so the model never saw them and redid the work with its own code
(and got other figures). Now the answer comes first and the whole result fits."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from evals import ca_data as cd
from src.agent import actions, ageing, bank, ca_reports, documents, extras, gst
from src.agent.policy import SessionCtx
from src.agent.tools import bounded


@pytest.fixture
def world(monkeypatch):
    f, _ = cd.gst_pair()
    bf, _ = cd.bank_recon()
    af, _ = cd.ageing()
    files = {"g1": ("purchase_register_oct.xlsx", f["purchase_register_oct.xlsx"]), "g2": ("GSTR2B_102026.xlsx", f["GSTR2B_102026.xlsx"]),
             "b1": ("bank_book_oct.xlsx", bf["bank_book_oct.xlsx"]), "b2": ("bank_statement_oct.xlsx", bf["bank_statement_oct.xlsx"]),
             "a1": ("receivables_open.xlsx", af["receivables_open.xlsx"])}

    async def fetch(app, ref, ctx=None):
        n, d = files[ref]
        return d, "", n

    async def send(app, ctx, args):
        return {"sent": True}

    async def make(app, ctx, args):
        return {"file_ref": "sandbox:j:r.pdf", "filename": "r.pdf", "_new_files": ["sandbox:j:r.pdf"]}

    async def excel(app, name, sheets):
        return f"sandbox:j:{name}", name
    monkeypatch.setattr(actions, "_fetch", fetch)
    monkeypatch.setattr(extras, "send_file_to_owner", send)
    monkeypatch.setattr(documents, "make_document", make)
    monkeypatch.setattr(ca_reports, "excel_file", excel)
    return SessionCtx("s", "t", "TRUSTED", files=tuple(files)), SimpleNamespace()


def check(r):
    shown = bounded(r)
    assert "[truncated]" not in shown, len(json.dumps(r, default=str))
    assert list(r)[0] == "summary_lines" and list(r)[1] == "next" and "_new_files" in r or "file_refs" in r
    assert shown.index("summary_lines") < 20 and shown.index('"next"') < 4000


def test_the_gst_result_arrives_whole_with_the_sentences_first(world):
    ctx, app = world
    r = asyncio.run(gst.gst_reconcile(app, ctx, {"books_file_ref": "g1", "gstr2b_file_ref": "g2"}))
    check(r)
    assert any("ITC claimable this month: Rs 72,86,766.86" in x for x in r["summary_lines"]) and r["claimable_itc_rupees"] == 7286766.86


def test_the_bank_result_arrives_whole_with_the_sentences_first(world):
    ctx, app = world
    r = asyncio.run(bank.bank_reconcile(app, ctx, {"book_file_ref": "b1", "bank_file_ref": "b2"}))
    check(r)
    assert any("Corrected balance as per books: Rs 8,91,139.00" in x for x in r["summary_lines"]) and r["reconciles"]


def test_the_ageing_result_arrives_whole_with_the_sentences_first(world):
    ctx, app = world
    r = asyncio.run(ageing.ageing_report(app, ctx, {"file_ref": "a1", "as_on": "2026-10-31"}))
    check(r)
    assert any("90+ days Rs 7,27,69,930.00" in x for x in r["summary_lines"])


def test_compact_keeps_the_front_and_cuts_the_lists_when_a_result_is_too_big():
    big = {"summary_lines": ["a"], "next": "n", "counts": {"x": 1}, "rows_a": [{"k": "v" * 200}] * 40, "rows_b": [{"k": "w" * 200}] * 40, "_new_files": ["sandbox:j:f.xlsx"]}
    out = ca_reports.compact(big, ("summary_lines", "next", "counts"))
    assert list(out)[:3] == ["summary_lines", "next", "counts"] and len(json.dumps(out)) <= ca_reports.RESULT_LIMIT + 400
    assert out["_new_files"] == ["sandbox:j:f.xlsx"] and (len(out["rows_a"]) <= 5 and len(out["rows_b"]) <= 5)
