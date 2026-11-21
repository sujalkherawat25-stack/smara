"""A scanned PDF (only pictures of pages, no text layer) is read through the same vision step as a phone photo; it used to come back as 'ask the owner
for a clearer copy'. The pictures are pulled out in the sandbox (which has Pillow); the app image has none."""
import asyncio
from datetime import date
from types import SimpleNamespace

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("PIL")

from evals import bill_data as bd  # noqa: E402
from src.agent import reader  # noqa: E402
from src.agent.policy import SessionCtx  # noqa: E402
from src.box.sandbox import Sandbox  # noqa: E402


class Vision:
    enabled = True

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    async def json(self, system, user, **kw):
        url = user[0]["image_url"]["url"]
        self.calls.append(url[:23])
        return {"kind": "bill", "text": self.answers.pop(0)}


def a_bill(units=455, style=0):
    return bd.electricity(date(2026, 11, 21), name="Khanna Hardware", consumer="4022 0136 7", meter="SP-1", prev_read=100, units=units,
                          history=[430, 420, 470, 445, 462, 439], arrears="0.00", style=style)


def scanned_pdf() -> bytes:
    return bd.scanned_pdf(bd.photo(bd.to_image(a_bill()), angle=0.3, width=900, seed=7))


def run(monkeypatch, tmp_path, data, answers, name="scan.pdf", llm_enabled=True):
    async def fetch(app, ref, ctx=None):
        return data, "", name
    monkeypatch.setattr(reader, "_fetch", fetch)
    llm = Vision(answers)
    llm.enabled = llm_enabled
    app = SimpleNamespace(state=SimpleNamespace(llm=llm, sandbox=Sandbox(str(tmp_path / "jobs"), "local"), settings=SimpleNamespace(sarvam_api_key="", sarvam_stt_url="")))
    out = asyncio.run(reader.read_attachment(app, SessionCtx("s", "t", "TRUSTED", files=("f",)), {"file_ref": "f"}))
    return out, llm


def test_a_scanned_pdf_is_read_from_its_page_pictures(monkeypatch, tmp_path):
    out, llm = run(monkeypatch, tmp_path, scanned_pdf(), ["Net Payable by Due Date Rs. 7,349.00\nDue Date 05/12/2026"])
    assert out["kind"] == "pdf" and out["read_from_scan"] is True and "7,349.00" in out["text"] and "--- page 1 ---" in out["text"]
    assert "scan" in out["note"] and llm.calls == ["data:image/jpeg;base64,"]


def test_an_unreadable_scan_still_asks_for_a_clearer_copy(monkeypatch, tmp_path):
    out, _ = run(monkeypatch, tmp_path, scanned_pdf(), [""])
    assert out["kind"] == "pdf" and not out.get("read_from_scan") and "no text layer" in out["note"]


def test_without_a_vision_model_the_old_answer_stays(monkeypatch, tmp_path):
    out, llm = run(monkeypatch, tmp_path, scanned_pdf(), [], llm_enabled=False)
    assert "no text layer" in out["note"] and llm.calls == []


def test_a_pdf_with_text_never_goes_through_the_scan_path(monkeypatch, tmp_path):
    out, llm = run(monkeypatch, tmp_path, bd.to_pdf(a_bill()), [], name="bill.pdf")
    assert "Net Payable" in out["text"] and not out.get("read_from_scan") and llm.calls == []


def test_a_photo_whose_type_came_back_generic_is_still_read_as_a_photo(monkeypatch, tmp_path):
    jpg = bd.jpeg(bd.photo(bd.to_image(a_bill(units=1042, style=1))))
    out, llm = run(monkeypatch, tmp_path, jpg, ["Net Payable by Due Date Rs. 11,917.00"], name="photo.jpg")
    assert out["kind"] == "image" and "11,917.00" in out["read"]["text"] and len(llm.calls) == 1


def test_the_bill_files_also_render_without_the_windows_fonts(monkeypatch, tmp_path):
    """On the CI machine there is no Arial: the bundled Vera font is used and the rupee sign is printed as 'Rs.'."""
    import os
    import reportlab
    vera = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
    monkeypatch.setattr(bd, "fonts", lambda: (os.path.join(vera, "Vera.ttf"), os.path.join(vera, "VeraBd.ttf"), False))
    keys = bd.build(tmp_path, date(2026, 11, 21))
    from pypdf import PdfReader
    text = "".join(p.extract_text() or "" for p in PdfReader(str(tmp_path / keys["E"]["file"])).pages)
    assert "₹" not in text and "Rs. 5,310.00" in text
    assert (tmp_path / keys["B"]["file"]).read_bytes()[:3] == b"\xff\xd8\xff"
