"""read_attachment: turn any file the owner sends into text the agent can use."""
import io
import re
import zipfile
import xml.etree.ElementTree as ET

from src.agent.actions import _fetch
from src.agent.policy import SessionCtx
from src.ingest.media import MediaError, read_image_text, transcribe
from src.ingest.sheet_mapper import decode_text

MAX_TEXT = 20_000
MAX_PDF_PAGES = 80
TEXT_EXT = (".txt", ".csv", ".tsv", ".json", ".xml", ".html", ".htm", ".md", ".log", ".tsv")


def _cap(text: str) -> dict:
    text = re.sub(r"[ \t]+\n", "\n", text).strip()
    return {"text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT, "chars": len(text)}


def _bank_statement(data: bytes, password: str = "") -> dict | None:
    """If this PDF is a bank statement, read it with code (rows, totals, tie-out against the closing balance) so the agent
    quotes figures a program computed instead of adding up numbers it read off pages. None when it is not a statement."""
    import tempfile
    from pathlib import Path
    from sandbox import smara_helpers as mh
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "statement.pdf"
        path.write_bytes(data)
        try:
            res = mh.bank_statement_pdf(path, password)
        except Exception:
            return None
    if not res.get("ok"):
        return {"error": res["error"], "need_password": True} if res.get("need_password") else None
    keep = ("row_count", "credits", "debits", "opening", "closing", "computed_closing", "ties", "rows_consistent", "period",
            "by_month", "top_credits", "top_debits", "failed", "notes", "chain_breaks")
    return {"statement": {k: res[k] for k in keep},
            "rows_preview": res["rows"][:5],
            "next": "These figures were computed by a program from every row of the PDF: quote them, do not re-add numbers by eye. "
                    "If statement.ties is false, tell the owner plainly what statement.notes says and do not present the totals as final. "
                    "For anything more (by party or category, matching to the ledger, an Excel of the rows) call run_python with "
                    "`import smara_helpers as mh; r = mh.bank_statement_pdf(path)`; r['rows'] has every row."}


def pdf_text(data: bytes, password: str = "") -> dict:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(password):
        return {"error": "this PDF is password protected: ask the owner for the password (then call read_attachment again with password=) "
                         "or for an unprotected copy", "need_password": True}
    pages = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        pages.append(page.extract_text() or "")
    out = {"pages": len(reader.pages), **_cap("\n\n".join(pages))}
    if not out["text"]:
        out["note"] = "no text layer: this looks like a scan. Ask the owner for the original file or a clear photo of each page."
    return out


def _office_text(data: bytes, parts: str, tag: str) -> dict:
    """docx / pptx are zip files of XML; the words are in the <w:t> / <a:t> elements."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = sorted((n for n in z.namelist() if re.fullmatch(parts, n)), key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)])
        chunks = []
        for n in names[:200]:
            if z.getinfo(n).file_size > 20_000_000:
                continue
            root = ET.fromstring(z.read(n))
            chunks.append(" ".join(e.text for e in root.iter(tag) if e.text))
    return _cap("\n\n".join(chunks))


def zip_listing(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = [{"name": i.filename, "bytes": i.file_size} for i in z.infolist() if not i.is_dir()]
    return {"files": files[:100], "file_count": len(files),
            "note": "Use run_python with this file to open the ones you need (the sandbox has no network)."}


MAX_SCAN_PAGES = 4
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


async def _read_scanned_pdf(app, data: bytes, password: str = "") -> dict | None:
    """A PDF that is only pictures of pages (an office scanner, a scanning app): the sandbox pulls the page pictures out and the same vision read that
    handles a phone photo reads them. None when nothing could be read, so the caller can still ask for a clearer copy."""
    sb = getattr(app.state, "sandbox", None)
    if not (sb and sb.enabled and app.state.llm.enabled):
        return None
    res = await sb.run(f"import smara_helpers as mh\nRESULT = mh.pdf_scan_pages('in/scan.pdf', max_pages={MAX_SCAN_PAGES}, password={password!r})\n", {"scan.pdf": data}, timeout_s=90)
    got = res.get("result") or {}
    chunks, unread = [], []
    for name in got.get("files") or []:
        n = re.search(r"(\d+)", name).group(1)
        text = ((await read_image_text(app.state.llm, sb.read_output(res["job_id"], name), "image/jpeg")).get("text") or "").strip()
        (chunks.append(f"--- page {n} ---\n{text}") if text else unread.append(n))
    if not chunks:
        return None
    note = ("read from the pictures of the pages, not from a text layer: quote the figures as read, say they come from a scan, and for a payment or a "
            "total the owner will act on, ask them to glance at it")
    if unread:
        note += f"; page(s) {', '.join(unread)} could not be read"
    if (got.get("pages") or 0) > MAX_SCAN_PAGES:
        note += f"; only the first {MAX_SCAN_PAGES} of {got['pages']} pages were read"
    return {"pages": got.get("pages"), **_cap("\n\n".join(chunks)), "read_from_scan": True, "note": note}


async def read_attachment(app, ctx: SessionCtx, args: dict) -> dict:
    try:
        data, mime, filename = await _fetch(app, args["file_ref"], ctx)
    except MediaError as e:
        return {"error": str(e)}
    name, mime = filename.lower(), (mime or "").split(";")[0]
    base = {"filename": filename, "size_bytes": len(data), "mime": mime}
    try:
        if name.endswith(".pdf") or mime == "application/pdf":
            pw = str(args.get("password") or "")
            text = pdf_text(data, pw)
            if "text" in text and re.search(r"opening balance|closing balance|(debit|withdrawal).{0,80}(credit|deposit).{0,80}balance", text["text"], re.I | re.S):
                bank = _bank_statement(data, pw)
                if bank and "statement" in bank:
                    return {**base, "kind": "bank_statement_pdf", "pages": text.get("pages"), **bank}
            if "text" in text and not text["text"]:
                if scanned := await _read_scanned_pdf(app, data, pw):
                    return {**base, "kind": "pdf", **scanned}
            return {**base, "kind": "pdf", **text}
        if name.endswith(".docx"):
            return {**base, "kind": "word", **_office_text(data, r"word/document\.xml", "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")}
        if name.endswith(".pptx"):
            return {**base, "kind": "slides", **_office_text(data, r"ppt/slides/slide\d+\.xml", "{http://schemas.openxmlformats.org/drawingml/2006/main}t")}
        if name.endswith(".zip") or mime == "application/zip":
            return {**base, "kind": "zip", **zip_listing(data)}
        if name.endswith((".xlsx", ".xlsm", ".xls")):
            return {**base, "kind": "spreadsheet", "note": "use inspect_and_import_file to read receivables, or run_python for other analysis"}
        image_type = mime if mime.startswith("image/") else IMAGE_TYPES.get(name[name.rfind("."):] if "." in name else "")       # a photo whose type came back generic
        if image_type:
            if not app.state.llm.enabled:
                return {**base, "kind": "image", "error": "photo reading is not configured on this server"}
            return {**base, "kind": "image", "read": await read_image_text(app.state.llm, data, image_type)}
        if mime.startswith("audio/") or name.endswith((".ogg", ".mp3", ".m4a", ".wav", ".opus")):
            spoken = await transcribe(app.state.settings.sarvam_api_key, data, mime or "audio/ogg",
                                      url=app.state.settings.sarvam_stt_url)
            return {**base, "kind": "audio", **_cap(spoken), **({} if spoken else {"note": "could not transcribe this audio"})}
        if mime.startswith("video/") or name.endswith((".mp4", ".mov", ".3gp", ".mkv")):
            return {**base, "kind": "video", "note": "videos cannot be watched; ask the owner what it shows or for a photo or the document itself"}
        if name.endswith(TEXT_EXT) or mime.startswith("text/") or mime in ("application/json", "application/xml"):
            return {**base, "kind": "text", **_cap(decode_text(data))}
    except MediaError as e:
        return {**base, "error": str(e)}
    except Exception as e:                           # damaged, protected or strange files must not crash the turn
        return {**base, "error": f"could not read this file ({type(e).__name__}); it may be damaged or password protected"}
    return {**base, "kind": "other", "note": "this file type cannot be read directly. If it is an archive or data file, try run_python."}
