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


def pdf_text(data: bytes) -> dict:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        return {"error": "this PDF is password protected; ask the owner for an unprotected copy"}
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


async def read_attachment(app, ctx: SessionCtx, args: dict) -> dict:
    try:
        data, mime, filename = await _fetch(app, args["file_ref"], ctx)
    except MediaError as e:
        return {"error": str(e)}
    name, mime = filename.lower(), (mime or "").split(";")[0]
    base = {"filename": filename, "size_bytes": len(data), "mime": mime}
    try:
        if name.endswith(".pdf") or mime == "application/pdf":
            return {**base, "kind": "pdf", **pdf_text(data)}
        if name.endswith(".docx"):
            return {**base, "kind": "word", **_office_text(data, r"word/document\.xml", "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")}
        if name.endswith(".pptx"):
            return {**base, "kind": "slides", **_office_text(data, r"ppt/slides/slide\d+\.xml", "{http://schemas.openxmlformats.org/drawingml/2006/main}t")}
        if name.endswith(".zip") or mime == "application/zip":
            return {**base, "kind": "zip", **zip_listing(data)}
        if name.endswith((".xlsx", ".xlsm", ".xls")):
            return {**base, "kind": "spreadsheet", "note": "use inspect_and_import_file to read receivables, or run_python for other analysis"}
        if mime.startswith("image/"):
            if not app.state.llm.enabled:
                return {**base, "kind": "image", "error": "photo reading is not configured on this server"}
            return {**base, "kind": "image", "read": await read_image_text(app.state.llm, data, mime)}
        if mime.startswith("audio/") or name.endswith((".ogg", ".mp3", ".m4a", ".wav", ".opus")):
            spoken = await transcribe(app.state.settings.sarvam_api_key, data, mime or "audio/ogg",
                                      url=app.state.settings.sarvam_stt_url)
            return {**base, "kind": "audio", **_cap(spoken), **({} if spoken else {"note": "could not transcribe this audio"})}
        if mime.startswith("video/") or name.endswith((".mp4", ".mov", ".3gp", ".mkv")):
            return {**base, "kind": "video", "note": "videos cannot be watched; ask the owner what it shows or for a photo or the document itself"}
        if name.endswith(TEXT_EXT) or mime.startswith("text/") or mime in ("application/json", "application/xml"):
            return {**base, "kind": "text", **_cap(decode_text(data))}
    except Exception as e:                           # damaged, protected or strange files must not crash the turn
        return {**base, "error": f"could not read this file ({type(e).__name__}); it may be damaged or password protected"}
    return {**base, "kind": "other", "note": "this file type cannot be read directly. If it is an archive or data file, try run_python."}
