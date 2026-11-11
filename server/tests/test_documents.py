"""Documents and skills: formatting, safety limits, ledger-backed figures, and the skills' own accuracy."""
import importlib.util
import io
import json
import os
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sandbox"))
import smara_docs  # noqa: E402

from src import tenants  # noqa: E402
from src.agent import documents as D  # noqa: E402
from src.agent import skills  # noqa: E402
from src.agent.money import inr  # noqa: E402
from src.agent.policy import SessionCtx, pre_tool_use  # noqa: E402
from src.agent.tools import TOOL_NAMES  # noqa: E402

URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


# ------------------------------------------------------------------------------------------ money
CASES = {0: "₹0.00", 5: "₹5.00", 999.5: "₹999.50", 1000: "₹1,000.00", 12345.6: "₹12,345.60", 185750: "₹1,85,750.00",
         1234567.891: "₹12,34,567.89", 12345678.5: "₹1,23,45,678.50", -4200.05: "-₹4,200.05", 100000: "₹1,00,000.00"}


@pytest.mark.parametrize("value,text", CASES.items())
def test_indian_grouping(value, text):
    assert inr(value) == text and smara_docs.inr(value) == text      # the server and the sandbox must agree


def test_no_decimals_and_no_symbol():
    assert inr(125000, decimals=0) == "₹1,25,000" and inr(2500, symbol=False) == "2,500.00"


# ------------------------------------------------------------------------------------------ rendering
SPEC = {"title": "Statement", "subtitle": "<b>Ravi & Sons</b>", "shop": {"name": "Shop <script>x</script>", "line": ""}, "meta": [["Date", "06 Nov 2026"]],
        "footer": "Prepared by Smara", "blocks": [
            {"type": "stats", "items": [{"label": "Owed", "value": "₹1,000.00", "tone": "bad"}]},
            {"type": "table", "columns": [{"label": "Invoice"}, {"label": "Amount", "kind": "money"}], "rows": [["A-1", 1000], ["A-2", None]],
             "total_row": ["Total", 1000]},
            {"type": "bar_chart", "items": [{"label": "Old", "value": 500}, {"label": "None", "value": 0}]},
            {"type": "evil", "text": "dropped"}, {"type": "note", "text": "line1\nline2"}]}


def test_html_is_escaped_and_complete():
    html = smara_docs.build_html(SPEC)
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;b&gt;Ravi" in html
    assert "₹1,000.00" in html and "A-1" in html and "line1<br>line2" in html
    assert html.count("<i style=") == 1                      # the zero bar draws nothing
    assert "–" in html                                       # an empty money cell and a zero value show a dash


def test_unknown_block_types_are_dropped_by_render(tmp_path):
    out = tmp_path / "x.docx"
    smara_docs.render(SPEC, str(out), "docx")
    from docx import Document
    text = "\n".join(p.text for p in Document(str(out)).paragraphs) + "\n".join(c.text for t in Document(str(out)).tables for r in t.rows for c in r.cells)
    assert "dropped" not in text and "A-1" in text and "₹1,000.00" in text and "Ravi & Sons" not in text.replace("<b>Ravi & Sons</b>", "")


@pytest.mark.skipif(importlib.util.find_spec("weasyprint") is None, reason="WeasyPrint (needs pango) is only in the sandbox image")
def test_pdf_has_text_and_page_numbers(tmp_path):
    out = tmp_path / "x.pdf"
    res = smara_docs.render({**SPEC, "blocks": SPEC["blocks"] * 40}, str(out), "pdf")
    from pypdf import PdfReader
    text = PdfReader(str(out)).pages[0].extract_text()
    assert res["pages"] > 1 and "A-1" in text and "Page 1 of" in text


# ------------------------------------------------------------------------------------------ custom block limits
def test_custom_blocks_are_validated_and_trimmed():
    out = D.clean_custom({"blocks": [{"type": "paragraph", "text": "x" * 10_000},
                                     {"type": "table", "columns": [{"label": str(i)} for i in range(30)], "rows": [[1] * 40] * 900}]})
    assert len(out[0]["text"]) == D.MAX_TEXT
    assert len(out[1]["columns"]) == D.MAX_COLS and len(out[1]["rows"]) == D.MAX_ROWS and all(len(r) == D.MAX_COLS for r in out[1]["rows"])
    for bad in ({"blocks": []}, {"blocks": [{"type": "script"}]}, {"blocks": ["text"]}, {"blocks": [{"type": "table", "columns": []}]}, {}):
        with pytest.raises(ValueError):
            D.clean_custom(bad)


# ------------------------------------------------------------------------------------------ skills
def test_skills_are_well_formed():
    found = skills.all_skills()
    assert {"collections-followup", "supplier-payments-43bh", "gst-purchase-reconciliation", "owner-daily-report",
            "making-documents", "tally-and-file-import"} <= set(found)
    for name, s in found.items():
        assert 40 < len(s["description"]) < 400 and len(s["body"]) > 800, name
        assert len(s["body"]) < skills.MAX_BODY, f"{name} would be cut off when loaded"


def test_skills_only_call_tools_that_exist():
    called = set()
    for s in skills.all_skills().values():
        called |= set(re.findall(r"`([a-z_]+)\(", s["body"].replace("`mh.", "`mh_")))
    called = {c for c in called if not c.startswith("mh_")}
    assert called, "expected the skills to name tools"
    assert called <= TOOL_NAMES, f"unknown tools in skills: {called - TOOL_NAMES}"


@pytest.mark.skipif(not URL, reason="needs the app modules' database settings")
async def test_load_skill_tool_and_unknown_name():
    ok = await skills.load_skill(None, None, {"name": "making-documents"})
    assert "make_document" in ok["instructions"]
    bad = await skills.load_skill(None, None, {"name": "../../etc/passwd"})
    assert "error" in bad and "making-documents" in bad["available"]


def test_prompt_lists_skills_but_not_for_customer_sessions():
    from src.agent import prompt
    assert "collections-followup" in skills.index()
    src = Path(prompt.__file__).read_text(encoding="utf-8")
    assert 'trust != "UNTRUSTED" and (listing := skills.index())' in src


# ------------------------------------------------------------------------------------------ permissions
def test_documents_are_for_the_owner_not_for_customer_sessions():
    owner = SessionCtx(id="s", tenant_id="t", trust="TRUSTED")
    assert pre_tool_use(owner, "make_document", {"kind": "owner_report"}, TOOL_NAMES).verdict == "allow"
    assert pre_tool_use(owner, "load_skill", {"name": "making-documents"}, TOOL_NAMES).verdict == "allow"
    cust = SessionCtx(id="s", tenant_id="t", trust="UNTRUSTED", party_id="p")
    assert pre_tool_use(cust, "make_document", {"kind": "owner_report"}, TOOL_NAMES).verdict == "deny"
    assert pre_tool_use(cust, "load_skill", {"name": "x"}, TOOL_NAMES).verdict == "deny"


# ------------------------------------------------------------------------------------------ ledger-backed documents
class FakeSandbox:
    def __init__(self, fail=False):
        self.runs, self.fail = [], fail

    async def run(self, code, inputs, timeout_s=60):
        spec = json.loads(inputs["spec.json"])
        self.runs.append(spec)
        if self.fail:
            return {"exit_code": 1, "stderr": "boom", "job_id": "a" * 32}
        return {"exit_code": 0, "job_id": "a" * 32, "outputs": [spec["_filename"]], "result": {"pages": 1}}


@pytest.fixture
async def world():
    from src.db import migrate, open_pool
    from src.ingest.importer import import_rows
    from src.ingest.sheet_mapper import SheetRow
    from src.ingest.tally_sync import PayableIn, SyncBody, apply_sync
    pool = await open_pool(URL)
    await migrate(pool)
    t = await tenants.create(pool, name="Balaji Traders", owner_phone="91" + str(uuid.uuid4().int)[:10], upi_vpa="balaji@okaxis", upi_name="Balaji")
    today = date.today()
    await import_rows(pool, t.id, [SheetRow("Ravi Traders", "919812345678", "I-1", 18575000, today - timedelta(days=52)),
                                   SheetRow("Ravi Traders", "919812345678", "I-2", 4200050, today + timedelta(days=5)),
                                   SheetRow("Other Co", "919800000001", "O-1", 100000, today - timedelta(days=2))])
    await apply_sync(pool, t.id, None, SyncBody(payables=[
        PayableIn(supplier="Kota Steel", bill_ref="P-1", bill_date=today - timedelta(days=20), amount_rupees=50000, msme_class="small"),
        PayableIn(supplier="Big Corp", bill_ref="P-2", bill_date=today - timedelta(days=20), amount_rupees=9000, msme_class="other")]))
    async with pool.connection() as c:
        ravi = (await (await c.execute("SELECT id FROM parties WHERE tenant_id=%s AND canonical_name='Ravi Traders'", (t.id,))).fetchone())["id"]
    other = await tenants.create(pool, name="Someone Else", owner_phone="91" + str(uuid.uuid4().int)[:10])
    app = SimpleNamespace(state=SimpleNamespace(pool=pool, sandbox=FakeSandbox()))
    yield app, t, str(ravi), other
    await pool.close()


def ctx(tenant):
    return SessionCtx(id="s", tenant_id=tenant.id, trust="TRUSTED")


@needs_db
async def test_statement_figures_come_from_the_ledger(world):
    app, t, ravi, _ = world
    out = await D.make_document(app, ctx(t), {"kind": "statement", "party_id": ravi})
    assert out["figures"] == {"customer": "Ravi Traders", "open_invoices": 2, "outstanding_rupees": 227750.5, "overdue_rupees": 185750.0}
    spec = app.state.sandbox.runs[0]
    stats = spec["blocks"][0]["items"]
    assert stats[0]["value"] == "₹2,27,750.50" and stats[1]["value"] == "₹1,85,750.00"
    table = spec["blocks"][1]
    assert table["total_row"][3] == 227750.5 and len(table["rows"]) == 2
    assert "balaji@okaxis" in json.dumps(spec)
    assert out["file_ref"].startswith("sandbox:") and out["filename"].startswith("Statement_Ravi_Traders_") and out["_new_files"] == [out["file_ref"]]


@needs_db
async def test_another_shops_customer_is_refused(world):
    app, t, ravi, other = world
    out = await D.make_document(app, ctx(other), {"kind": "statement", "party_id": ravi})
    assert "error" in out and not app.state.sandbox.runs


@needs_db
async def test_payables_schedule_flags_the_deadline(world):
    app, t, _, _ = world
    out = await D.make_document(app, ctx(t), {"kind": "payables_schedule"})
    assert out["figures"]["past_deadline_bills"] == 1 and out["figures"]["tracked_bills"] == 1      # Big Corp is "other": not tracked
    rows = app.state.sandbox.runs[0]["blocks"][1]["rows"]
    assert rows[0][0] == "Kota Steel" and "past deadline" in rows[0][4]


@needs_db
async def test_owner_report_and_letter_and_custom(world):
    app, t, ravi, _ = world
    rep = await D.make_document(app, ctx(t), {"kind": "owner_report", "days": 9999})
    assert rep["figures"]["outstanding_rupees"] == 228750.5 and rep["figures"]["open_supplier_bills"] == 2
    assert "error" in await D.make_document(app, ctx(t), {"kind": "reminder_letter", "party_id": ravi})            # no body
    letter = await D.make_document(app, ctx(t), {"kind": "reminder_letter", "party_id": ravi, "body": "Please pay.", "format": "docx"})
    assert letter["filename"].endswith(".docx") and letter["figures"]["overdue_invoices"] == 1
    cust = await D.make_document(app, ctx(t), {"kind": "custom", "title": "Workings", "blocks": [{"type": "paragraph", "text": "hi"}]})
    assert cust["figures"] == {"blocks": 1}
    assert "error" in await D.make_document(app, ctx(t), {"kind": "invoice"})
    assert "error" in await D.make_document(app, ctx(t), {"kind": "custom", "title": "x", "blocks": [{"type": "script"}]})


@needs_db
async def test_a_failed_render_is_reported_not_hidden(world):
    app, t, ravi, _ = world
    app.state.sandbox = FakeSandbox(fail=True)
    out = await D.make_document(app, ctx(t), {"kind": "statement", "party_id": ravi})
    assert out["error"] == "the document could not be made" and "boom" in out["detail"] and "file_ref" not in out
