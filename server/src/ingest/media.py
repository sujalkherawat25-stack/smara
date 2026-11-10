"""WhatsApp media: download, speech-to-text (Sarvam), bill/UTR vision OCR.

External-API shapes here (Graph media fetch, Sarvam /speech-to-text, OpenAI-style
image_url parts) follow the vendors' public docs but are not yet exercised live.
"""
import asyncio
import base64
import re

import httpx

from src.loop.llm import LLM

GRAPH = "https://graph.facebook.com/v20.0"
SARVAM_STT = "https://api.sarvam.ai/speech-to-text"
MAX_MEDIA_BYTES = 100 * 1024 * 1024      # WhatsApp itself accepts at most 100 MB per document
ATTEMPTS = 3


class MediaError(Exception):
    """A file could not be fetched; the message is safe to show the owner."""

BILL_PROMPT = (
    "Extract a payment document from the image. Return JSON with keys: "
    "kind ('bill'|'utr_receipt'|'other'), party (string|null), phone (digits|null), "
    "invoice_ref (string|null), amount_rupees (number|null), due_date (YYYY-MM-DD|null), "
    "utr (string|null), paid_amount_rupees (number|null). Use null when not visible; never guess."
)


async def media_token(app, number_id: str) -> str:
    """The token that can read media sent to this number: the shop's own for its own number, else Smara's."""
    from src.ingest.connect import token_for_number
    if number_id and number_id != app.state.settings.wa_phone_number_id:
        if tok := await token_for_number(app.state.pool, number_id):
            return tok
    return app.state.settings.wa_access_token


async def download_media(access_token: str, media_id: str, client: httpx.AsyncClient | None = None,
                         max_bytes: int = MAX_MEDIA_BYTES) -> tuple[bytes, str]:
    """Fetch a WhatsApp file of any type. Large files are streamed and capped, and a dropped connection is retried."""
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30, read=180), headers={"Authorization": f"Bearer {access_token}"})
    try:
        meta = (await http.get(f"{GRAPH}/{media_id}")).raise_for_status().json()
        mime = meta.get("mime_type", "application/octet-stream")
        if int(meta.get("file_size") or 0) > max_bytes:
            raise MediaError(f"That file is {int(meta['file_size']) // 1_000_000} MB; the limit is {max_bytes // 1_000_000} MB.")
        last: Exception | None = None
        for attempt in range(ATTEMPTS):
            try:
                buf = bytearray()
                async with http.stream("GET", meta["url"]) as r:
                    r.raise_for_status()
                    async for chunk in r.aiter_bytes(1 << 20):
                        buf += chunk
                        if len(buf) > max_bytes:
                            raise MediaError(f"That file is larger than {max_bytes // 1_000_000} MB, which is the limit.")
                return bytes(buf), mime
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last = e
                await asyncio.sleep(1 + attempt)
        raise MediaError("The file could not be downloaded from WhatsApp. Please send it again.") from last
    finally:
        if client is None:
            await http.aclose()


async def transcribe(api_key: str, audio: bytes, mime: str = "audio/ogg",
                     client: httpx.AsyncClient | None = None, url: str = SARVAM_STT) -> str:
    """Hinglish-capable STT. Returns '' when no key is configured. The key is sent in a header and never logged."""
    if not api_key:
        return ""
    http = client or httpx.AsyncClient(timeout=60)
    try:
        resp = await http.post(
            url or SARVAM_STT, headers={"api-subscription-key": api_key},
            files={"file": ("voice.ogg", audio, mime)},
            data={"model": "saarika:v2.5", "language_code": "unknown"},
        )
        resp.raise_for_status()
        return resp.json().get("transcript", "")
    finally:
        if client is None:
            await http.aclose()


async def read_image(llm: LLM, image: bytes, mime: str) -> dict:
    """Vision OCR for bills and UTR screenshots. {} when no LLM is configured."""
    if not llm.enabled:
        return {}
    url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    return await llm.json(BILL_PROMPT, [{"type": "image_url", "image_url": {"url": url}}])


READ_PROMPT = ("Read this image for a shop owner. Return JSON with keys: kind ('bill'|'invoice'|'cheque'|'statement'|'screenshot'|'photo'|'other'), "
               "text (all legible text, in reading order, any language), summary (one sentence). Never guess unreadable text.")


async def read_image_text(llm: LLM, image: bytes, mime: str) -> dict:
    """General vision read of any photo or scan. {} when no LLM is configured."""
    if not llm.enabled:
        return {}
    url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    return await llm.json(READ_PROMPT, [{"type": "image_url", "image_url": {"url": url}}])


def ocr_to_evidence(ocr: dict) -> str:
    """Flatten OCR fields into a line the claim extractor can read."""
    bits = []
    if ocr.get("utr"):
        bits.append(f"UTR {ocr['utr']}")
    if ocr.get("paid_amount_rupees"):
        bits.append(f"paid Rs {ocr['paid_amount_rupees']}")
    if ocr.get("invoice_ref"):
        bits.append(f"invoice {ocr['invoice_ref']}")
    return "Payment screenshot: " + ", ".join(bits) if bits else ""


def clean_phone_caption(caption: str) -> str:
    m = re.search(r"\+?\d[\d\s-]{9,16}\d", caption or "")
    return re.sub(r"\D", "", m.group()) if m else ""
