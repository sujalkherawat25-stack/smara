"""Tally/Busy export guide and sample sheet (Fix 2), and the PDF claim matching what the agent does (Fix 3). No DB, no network."""
import io
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from src.agent import prompt, reader
from src.agent.reader import pdf_text
from src.ingest import legal, owner_flow
from src.ingest.guides import EXPORT_GUIDE, SAMPLE_NAME, XLSX_MIME, sample_sheet_bytes, sample_sheet_path
from src.ingest.onboarding import welcome_done
from src.ingest.payload import InboundMessage
from src.ingest.sheet_mapper import map_sheet

OWNER = "919000000001"


def owner_text(text: str) -> InboundMessage:
    return InboundMessage(wamid="w1", sender=OWNER, kind="text", text=text)


# ---------- Fix 2: export guide, sample file, owner commands, welcome line ----------

def test_export_guide_is_short_and_covers_tally_and_busy():
    assert len(EXPORT_GUIDE) <= 900
    for needle in ("Tally Prime", "Gateway of Tally", "Outstandings > Receivables", "Alt+E", "Tally ERP 9",
                   "Busy", "Bill-wise Receivable", "Customer name", "Phone", "Invoice no", "Amount"):
        assert needle in EXPORT_GUIDE, needle


async def test_export_commands_reply_with_the_guide():
    app = SimpleNamespace(state=SimpleNamespace())          # the export commands never touch the database
    for cmd in ("export", "Tally Export", "kaise bheju", "export guide"):
        assert await owner_flow._route(app, "t1", owner_text(cmd)) == EXPORT_GUIDE, cmd


async def test_sample_command_sends_the_sample_sheet_as_a_document(monkeypatch):
    from src import tenants
    from src.loop import owner_channel
    sent = []

    async def fake_by_id(pool, tid):
        return SimpleNamespace(id=tid, owner_phone=OWNER)

    async def fake_send(app, tenant, data, filename, mime, caption, reply_channel=None):
        sent.append({"data": data, "filename": filename, "mime": mime, "caption": caption, "channel": reply_channel})
        return "whatsapp"

    monkeypatch.setattr(tenants, "by_id", fake_by_id)
    monkeypatch.setattr(owner_channel, "send_document_to_owner", fake_send)
    app = SimpleNamespace(state=SimpleNamespace(pool=None))
    assert await owner_flow._route(app, "t1", owner_text("sample")) is None      # file sent, no text reply
    assert await owner_flow._route(app, "t1", owner_text("sample file")) is None
    assert [s["filename"] for s in sent] == [SAMPLE_NAME, SAMPLE_NAME] == ["sample_outstanding.xlsx"] * 2
    assert sent[0]["mime"] == XLSX_MIME and sent[0]["data"] == sample_sheet_bytes()
    assert sent[0]["data"][:2] == b"PK"                                           # a real .xlsx (zip) file


def test_help_points_to_export_and_sample():
    assert "'export'" in owner_flow.HELP and "'sample'" in owner_flow.HELP


def test_sample_sheet_is_in_assets_and_parses_with_the_owner_import_path():
    assert sample_sheet_path() is not None
    rows, skipped = map_sheet(SAMPLE_NAME, sample_sheet_bytes())   # same mapper the owner's file upload uses
    assert skipped == []
    assert len(rows) == 8
    assert sum(r.amount_paise for r in rows) == 43_720_125          # Rs 4,37,201.25
    assert all(r.phone.startswith("9198000000") and len(r.phone) == 12 for r in rows)   # clearly fake numbers
    assert {r.party for r in rows} >= {"Sharma Traders", "Agarwal Agencies"}


def test_welcome_message_has_the_export_and_sample_line():
    text = welcome_done("Ramesh Hardware")
    assert "Tally/Busy se list kaise nikalein? 'export' likhiye. Sample file chahiye? 'sample' likhiye." in text
    assert len(text) < 1200


# ---------- Fix 3: PDFs ----------

def _text_pdf(*lines: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in lines:
        c.drawString(72, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


def test_agent_can_read_text_from_an_owner_pdf():
    data = _text_pdf("Sharma Traders statement", "Invoice INV-2601  Rs 45,000 due 02 Nov 2026")
    out = pdf_text(data)
    assert out["pages"] == 1
    assert "Sharma Traders" in out["text"] and "INV-2601" in out["text"]
    assert "note" not in out                                       # has a text layer, so not treated as a scan


def test_landing_page_claim_matches_what_the_agent_does():
    app = FastAPI()
    app.include_router(legal.router)
    html = TestClient(app).get("/").text
    assert "Smara can also read PDFs such as bills and statements" in html
    assert "Excel works best" in html
    assert "Send a Tally export, an Excel or CSV sheet, a PDF" not in html   # the old promise is gone


def test_agent_prompt_says_it_reads_pdfs_and_wants_excel_only_for_bulk_import():
    assert "you CAN read a PDF" in prompt.BASE and "read_attachment" in prompt.BASE
    assert "Excel or CSV export" in prompt.BASE
    assert reader.MAX_PDF_PAGES == 80
