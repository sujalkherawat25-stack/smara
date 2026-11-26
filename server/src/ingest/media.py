"""WhatsApp media: download, speech-to-text (Sarvam), bill/UTR vision OCR.

External-API shapes here (Graph media fetch, Sarvam /speech-to-text, OpenAI-style
image_url parts) follow the vendors' public docs but are not yet exercised live.
"""
import array
import asyncio
import logging
import base64
import io
import re
import wave

import httpx

from src.loop.llm import LLM

GRAPH = "https://graph.facebook.com/v20.0"
SARVAM_STT = "https://api.sarvam.ai/speech-to-text"
MAX_MEDIA_BYTES = 100 * 1024 * 1024      # WhatsApp itself accepts at most 100 MB per document
ATTEMPTS = 3
STT_PIECE_S = 29        # the speech service refuses a recording over 30 s
STT_CUT_FROM_S = 20     # a longer one is cut at its quietest moment between this and STT_PIECE_S
STT_AT_ONCE = 3
MAX_VOICE_S = 300       # longer than this is refused with a plain message (speech is paid for by the second)


log = logging.getLogger(__name__)


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


async def to_wav(audio: bytes) -> bytes:
    """16 kHz mono WAV. WhatsApp and Telegram send voice notes as Ogg/Opus, which the speech service refuses."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate(audio)
    except OSError:
        log.warning("voice note not converted: ffmpeg is not installed")
        raise MediaError("I could not read that voice note. Please send it again or type it.")
    if proc.returncode != 0 or not out:
        log.warning("voice note not converted (ffmpeg exit %s)", proc.returncode)
        raise MediaError("I could not read that voice note. Please send it again or type it.")
    return out


def _spoken_length(seconds: float) -> str:
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes} min {rest} s"


def split_wav(wav: bytes) -> list[bytes]:
    """A recording the speech service can take, as it is; a longer one as pieces in order, each cut at the quietest moment
    near the end of its piece so that words are not split. No sound is dropped or repeated."""
    try:
        with wave.open(io.BytesIO(wav)) as source:
            channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
            # ffmpeg cannot write the length into a piped header, so it is worked out from the size, before copying anything
            length = len(wav) / (rate * channels * width)
            if length > MAX_VOICE_S + 0.5:
                raise MediaError(f"That voice note is {_spoken_length(length)} long. I can take up to "
                                 f"{MAX_VOICE_S // 60} minutes: please send it in shorter parts, or type it.")
            pcm = source.readframes(source.getnframes())    # likewise: read to the end
    except (wave.Error, EOFError):
        return [wav]                                        # cannot be measured: the speech service decides
    if len(pcm) / (rate * channels * width) <= STT_PIECE_S or width != 2:
        return [wav]
    samples = array.array("h", pcm)                         # 16-bit little-endian, as to_wav makes it
    per_second, window = rate * channels, rate * channels // 5

    def loudness(at: int) -> int:
        return sum(map(abs, samples[at:at + window]))

    cuts, start = [0], 0
    while len(samples) - start > STT_PIECE_S * per_second:
        first = start + STT_CUT_FROM_S * per_second
        last = min(start + STT_PIECE_S * per_second, len(samples) - 2 * per_second)     # the last piece keeps 2 s or more
        quietest = min(range(last - window, first - 1, -window // 2), key=loudness)     # latest first: ties give fewer pieces
        start = (quietest + window // 2) // channels * channels
        cuts.append(start)
    cuts.append(len(samples))
    pieces = []
    for begin, end in zip(cuts, cuts[1:]):
        out = io.BytesIO()
        with wave.open(out, "wb") as piece:
            piece.setnchannels(channels)
            piece.setsampwidth(width)
            piece.setframerate(rate)
            piece.writeframes(samples[begin:end].tobytes())
        pieces.append(out.getvalue())
    return pieces


async def _speech_to_text(http: httpx.AsyncClient, api_key: str, url: str, wav: bytes) -> str:
    for attempt in range(ATTEMPTS):
        resp = await http.post(
            url, headers={"api-subscription-key": api_key},
            files={"file": ("voice.wav", wav, "audio/wav")},
            data={"model": "saarika:v2.5", "language_code": "unknown"},
        )
        busy = resp.status_code == 429 or resp.status_code >= 500
        if not busy or attempt == ATTEMPTS - 1:
            break
        await asyncio.sleep(1 + 2 * attempt)
    if resp.status_code >= 400:
        log.warning("speech service answered %s: %s", resp.status_code, resp.text[:200])
    resp.raise_for_status()
    return resp.json().get("transcript", "")


async def transcribe(api_key: str, audio: bytes, mime: str = "audio/ogg",
                     client: httpx.AsyncClient | None = None, url: str = SARVAM_STT) -> str:
    """Hinglish-capable STT. Returns '' when no key is configured. The key is sent in a header and never logged.
    A long recording is sent in pieces. If any piece fails, all of it fails: half an instruction must never be acted on."""
    if not api_key:
        return ""
    pieces = split_wav(await to_wav(audio))
    http = client or httpx.AsyncClient(timeout=60)
    gate = asyncio.Semaphore(STT_AT_ONCE)

    async def one(piece: bytes) -> str:
        async with gate:
            return await _speech_to_text(http, api_key, url or SARVAM_STT, piece)

    jobs = [asyncio.ensure_future(one(piece)) for piece in pieces]
    try:
        said = await asyncio.gather(*jobs)
    finally:
        for job in jobs:
            job.cancel()                                    # no effect on the finished ones
        await asyncio.gather(*jobs, return_exceptions=True)
        if client is None:
            await http.aclose()
    return said[0] if len(said) == 1 else " ".join(part.strip() for part in said if part.strip())


async def read_image(llm: LLM, image: bytes, mime: str) -> dict:
    """Vision OCR for bills and UTR screenshots. {} when no LLM is configured."""
    if not llm.enabled:
        return {}
    url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    return await llm.json(BILL_PROMPT, [{"type": "image_url", "image_url": {"url": url}}])


READ_PROMPT = ("Read this image for the user. Return JSON with keys: kind ('bill'|'invoice'|'cheque'|'statement'|'screenshot'|'photo'|'other'), "
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
