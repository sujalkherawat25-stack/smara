"""WhatsApp files of every kind: parsing, big downloads, and reading them as text."""
import io
import zipfile
from types import SimpleNamespace

import httpx
import pytest

from src.agent import reader
from src.ingest.media import MediaError, download_media
from src.ingest.owner_flow import file_name
from src.ingest.payload import InboundMessage, parse_messages


def payload(**message):
    return {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "N1"},
                                              "messages": [{"id": "w1", "from": "919812345678", **message}]}}]}]}


def test_every_media_kind_is_parsed_with_its_mime():
    for kind, body in {"document": {"id": "m1", "filename": "Ledger.pdf", "mime_type": "application/pdf", "caption": "c"},
                       "image": {"id": "m2", "mime_type": "image/jpeg"}, "video": {"id": "m3", "mime_type": "video/mp4"},
                       "audio": {"id": "m4", "mime_type": "audio/ogg; codecs=opus"}, "sticker": {"id": "m5", "mime_type": "image/webp"}}.items():
        [m] = parse_messages(payload(type=kind, **{kind: body}))
        assert m.media_id == body["id"] and m.mime == body["mime_type"]


def test_file_names_always_have_an_extension_and_are_safe():
    mk = lambda **k: InboundMessage(wamid="w", sender="1", kind=k.pop("kind"), **k)
    assert file_name(mk(kind="image", mime="image/jpeg")).endswith((".jpg", ".jpeg"))
    assert file_name(mk(kind="video", mime="video/mp4")) == "video.mp4"
    assert file_name(mk(kind="document", filename="../../etc/passwd")) == "passwd.txt" or "/" not in file_name(mk(kind="document", filename="../../etc/passwd"))
    assert file_name(mk(kind="document", filename="Sales Report 2026.xlsx")) == "Sales_Report_2026.xlsx"


def client(size: int, chunks: int = 1, fail_first: bool = False, declared: int | None = None):
    calls = {"n": 0}

    def handler(request: httpx.Request):
        if request.url.path.endswith("/media1"):
            return httpx.Response(200, json={"url": "https://cdn.test/file", "mime_type": "application/pdf",
                                             "file_size": declared if declared is not None else size})
        calls["n"] += 1
        if fail_first and calls["n"] == 1:
            raise httpx.ReadError("connection dropped")
        return httpx.Response(200, content=b"x" * size)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_large_file_downloads_whole():
    async with client(30_000_000) as c:
        data, mime = await download_media("t", "media1", c)
    assert len(data) == 30_000_000 and mime == "application/pdf"


async def test_a_dropped_connection_is_retried(monkeypatch):
    monkeypatch.setattr("src.ingest.media.asyncio.sleep", lambda s: _noop())
    async with client(1000, fail_first=True) as c:
        data, _ = await download_media("t", "media1", c)
    assert len(data) == 1000


async def _noop():
    return None


async def test_oversize_is_refused_before_and_while_downloading():
    async with client(10, declared=200_000_000) as c:
        with pytest.raises(MediaError, match="limit"):
            await download_media("t", "media1", c)
    async with client(2_000_000, declared=1) as c:           # the size WhatsApp reported was wrong
        with pytest.raises(MediaError, match="larger"):
            await download_media("t", "media1", c, max_bytes=1_000_000)


def test_pdf_text_and_scanned_pdf_note():
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 700, "Invoice 77 total Rs 5000")
    c.showPage()
    c.save()
    out = reader.pdf_text(buf.getvalue())
    assert "Invoice 77" in out["text"] and out["pages"] == 1
    blank = io.BytesIO()
    c = canvas.Canvas(blank)
    c.showPage()
    c.save()
    assert "scan" in reader.pdf_text(blank.getvalue())["note"]


def office(parts: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, x in parts.items():
            z.writestr(n, x)
    return b.getvalue()


async def run_reader(monkeypatch, name, data, mime="application/octet-stream", llm_enabled=False):
    async def fake_fetch(app, ref, ctx=None):
        return data, mime, name
    monkeypatch.setattr(reader, "_fetch", fake_fetch)
    app = SimpleNamespace(state=SimpleNamespace(llm=SimpleNamespace(enabled=llm_enabled), settings=SimpleNamespace(sarvam_api_key="")))
    return await reader.read_attachment(app, SimpleNamespace(tenant_id="t"), {"file_ref": "whatsapp:m:" + name})


async def test_word_slides_text_zip_and_unknown(monkeypatch):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    docx = office({"word/document.xml": f'<d xmlns:w="{w}"><w:p><w:r><w:t>Hello owner</w:t></w:r></w:p></d>'})
    assert "Hello owner" in (await run_reader(monkeypatch, "a.docx", docx))["text"]
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    pptx = office({"ppt/slides/slide1.xml": f'<s xmlns:a="{a}"><a:t>Slide one</a:t></s>'})
    assert "Slide one" in (await run_reader(monkeypatch, "a.pptx", pptx))["text"]
    assert "plain words" in (await run_reader(monkeypatch, "n.txt", b"plain words", "text/plain"))["text"]
    z = await run_reader(monkeypatch, "b.zip", office({"x.csv": "a,b", "y/z.pdf": "pdf"}), "application/zip")
    assert z["file_count"] == 2
    assert (await run_reader(monkeypatch, "v.mp4", b"0", "video/mp4"))["kind"] == "video"
    assert (await run_reader(monkeypatch, "w.bin", b"0"))["kind"] == "other"


async def test_damaged_files_return_an_error_not_a_crash(monkeypatch):
    out = await run_reader(monkeypatch, "bad.pdf", b"%PDF-1.4 garbage", "application/pdf")
    assert "could not read" in out["error"]
    out = await run_reader(monkeypatch, "p.jpg", b"jpg", "image/jpeg", llm_enabled=False)
    assert "not configured" in out["error"]


async def test_large_text_is_capped(monkeypatch):
    out = await run_reader(monkeypatch, "big.txt", b"a" * 100_000, "text/plain")
    assert len(out["text"]) == reader.MAX_TEXT and out["truncated"] and out["chars"] == 100_000
