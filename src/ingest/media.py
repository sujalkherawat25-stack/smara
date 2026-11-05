"""WhatsApp media: download, speech-to-text (Sarvam), bill/UTR vision OCR.

External-API shapes here (Graph media fetch, Sarvam /speech-to-text, OpenAI-style
image_url parts) follow the vendors' public docs but are not yet exercised live.
"""
import base64
import re

import httpx

from src.loop.llm import LLM

GRAPH = "https://graph.facebook.com/v20.0"
SARVAM_STT = "https://api.sarvam.ai/speech-to-text"

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


async def download_media(access_token: str, media_id: str, client: httpx.AsyncClient | None = None) -> tuple[bytes, str]:
    http = client or httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {access_token}"})
    try:
        meta = (await http.get(f"{GRAPH}/{media_id}")).raise_for_status().json()
        blob = await http.get(meta["url"])
        blob.raise_for_status()
        return blob.content, meta.get("mime_type", "application/octet-stream")
    finally:
        if client is None:
            await http.aclose()


async def transcribe(api_key: str, audio: bytes, mime: str = "audio/ogg",
                     client: httpx.AsyncClient | None = None) -> str:
    """Hinglish-capable STT. Returns '' when no key is configured."""
    if not api_key:
        return ""
    http = client or httpx.AsyncClient(timeout=60)
    try:
        resp = await http.post(
            SARVAM_STT, headers={"api-subscription-key": api_key},
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
