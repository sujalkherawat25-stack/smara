"""Approval-gated Sarvam OCR/image readers; no legacy execution imports.

Document AI: /doc-ai/v1/job/digitise, status, download-url.
Image understanding: Gemma 4 on /v2/chat/completions with base64 input.
Only explicitly selected workspace files are read; no scanning or fallback
uploads. Native MCP approval must occur before invoking either entry point.
"""
import asyncio
import base64
import hashlib
import io
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import time
import zipfile

import httpx

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
API = "https://api.sarvam.ai"


class DocumentError(ValueError):
    """Secret-free diagnostic; raw provider responses are never exposed."""


def sarvam_key():
    from .native_profiles import load_profiles, resolve_credential, resolve_profile_key
    profiles, active, credentials = load_profiles()
    # A dedicated OCR credential is intentional. Otherwise prefer the active
    # Sarvam profile, not an old shared alias left behind by prior engines.
    key = resolve_credential("SMARA_OCR_API_KEY", credentials, allow_environment=False)
    if not key:
        ordered = sorted(profiles, key=lambda profile: profile.get("id") != active)
        for profile in ordered:
            if str(profile.get("base_url", "")).rstrip("/") in {API, API + "/v2"}:
                key = resolve_profile_key(profile, credentials, allow_environment=False)
                if key:
                    break
    if not key:
        for alias in ("SARVAM_API_KEY", "SMARA_MODEL_SARVAM_API_KEY"):
            key = resolve_credential(alias, credentials, allow_environment=False)
            if key:
                break
    if not key:
        raise DocumentError("No protected Sarvam key is readable. Save it in Settings → Models. No file was uploaded.")
    return key


def read_document(workspace, file_path, *, image_only=False):
    root = Path(workspace).resolve(strict=True)
    target = (root / file_path).resolve(strict=True)
    if not target.is_relative_to(root) or any(part in {".git", ".smara"} for part in target.relative_to(root).parts):
        raise DocumentError("Document path must remain inside the selected workspace, outside internal state folders.")
    suffix = target.suffix.lower()
    mimes = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    if image_only:
        mimes.pop(".pdf")
        mimes[".webp"] = "image/webp"
    if suffix not in mimes or not target.is_file():
        raise DocumentError("Select a supported PDF/PNG/JPEG document, or PNG/JPEG/WebP for image understanding.")
    with target.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise DocumentError("Document is empty or exceeds the 10 MB upload safety limit.")
    if image_only and len(raw) > 7 * 1024 * 1024:
        raise DocumentError("Image exceeds the 7 MB raw limit; base64 plus request framing must fit Sarvam's 10 MB request limit.")
    valid = (raw.startswith(b"%PDF-") if suffix == ".pdf" else raw.startswith(b"\x89PNG\r\n\x1a\n") if suffix == ".png"
             else raw.startswith(b"\xff\xd8") if suffix in {".jpg", ".jpeg"}
             else raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")
    if not valid:
        raise DocumentError("Document bytes do not match the selected file type. No upload was performed.")
    return target.name, raw, mimes[suffix]


async def bounded_response(client, method, url, **kwargs):
    async with client.stream(method, url, **kwargs) as response:
        if response.status_code >= 300:
            raise DocumentError(f"Sarvam document/image request failed (HTTP {response.status_code}); no automatic retry or fallback upload.")
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            if len(raw) + len(chunk) > MAX_RESULT_BYTES:
                raise DocumentError("Document result exceeds the 4 MB reader limit; no silent truncation.")
            raw.extend(chunk)
        return httpx.Response(response.status_code, content=bytes(raw), headers=response.headers)


def archive_text(raw):
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        text = raw.decode("utf-8", errors="strict")
        return html_text(text) if text.lstrip().lower().startswith(("<!doctype html", "<html")) else text
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = [entry for entry in archive.infolist() if not entry.is_dir() and entry.filename.lower().endswith((".md", ".txt", ".html", ".json"))]
        if len(files) > 32 or sum(entry.file_size for entry in files) > MAX_RESULT_BYTES:
            raise DocumentError("OCR archive exceeds the expanded result limit.")
        # Prefer markdown when an archive provides several output formats;
        # never extract archive paths to disk.
        files.sort(key=lambda entry: [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", entry.filename)])
        markdown = [entry for entry in files if entry.filename.lower().endswith(".md")]
        if markdown:
            return "\n\n".join(archive.read(entry).decode("utf-8", errors="strict") for entry in markdown)
        chunks = []
        for entry in files:
            if not entry.filename.lower().endswith(".json"):
                continue
            result = json.loads(archive.read(entry))
            blocks = result.get("blocks") if isinstance(result, dict) else None
            if isinstance(blocks, list):
                ordered = sorted((block for block in blocks if isinstance(block, dict)), key=lambda block: block.get("reading_order", 0))
                chunks.append("\n".join(block["text"] for block in ordered if isinstance(block.get("text"), str)))
        if any(chunks):
            return "\n\n".join(chunks)
        html = [entry for entry in files if entry.filename.lower().endswith(".html")]
        if html:
            return "\n\n".join(html_text(archive.read(entry).decode("utf-8", errors="strict")) for entry in html)
        return "\n\n".join(archive.read(entry).decode("utf-8", errors="strict") for entry in files if entry.filename.lower().endswith(".txt"))


def html_text(text):
    class VisibleText(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts, self.hidden = [], 0
        def handle_starttag(self, tag, attrs):
            if tag in {"head", "script", "style"}:
                self.hidden += 1
            elif not self.hidden and tag in {"br", "p", "div", "tr", "h1", "h2", "li"}:
                self.parts.append("\n")
        def handle_endtag(self, tag):
            if tag in {"head", "script", "style"}:
                self.hidden = max(0, self.hidden - 1)
            elif not self.hidden and tag in {"p", "div", "tr", "li"}:
                self.parts.append("\n")
        def handle_data(self, data):
            if not self.hidden:
                self.parts.append(data)
    parser = VisibleText()
    parser.feed(text)
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


async def digitise(workspace, file_path, language="en-IN", *, client=None, key=None, max_wait=30):
    if not re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}", language):
        raise DocumentError("Use a BCP-47 language such as en-IN or hi-IN.")
    filename, raw, mime = read_document(workspace, file_path)
    key = key or sarvam_key()
    owns = client is None
    client = client or httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False)
    headers = {"api-subscription-key": key}
    prefix = API + "/doc-ai/v1/job"
    try:
        # Exactly one submission. Timeout does not resubmit a possibly accepted
        # paid job. Its ID is returned on polling expiry for honest recovery.
        job = (await bounded_response(client, "POST", prefix + "/digitise", headers=headers,
            data={"language": language, "content_type": "mixed"}, files={"file": (filename, raw, mime)})).json()
        job_id = job.get("job_id")
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
            raise DocumentError("Sarvam returned an invalid OCR job identity.")
        deadline = time.monotonic() + max_wait
        status = job.get("status")
        usage = {}
        for _ in range(15):
            if status in {"completed", "partially_completed", "failed", "rejected"}:
                break
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(min(2, max(0, deadline - time.monotonic())))
            state = (await bounded_response(client, "GET", f"{prefix}/{job_id}/status", headers=headers)).json()
            status, usage = state.get("status"), state.get("usage", {})
        if status not in {"completed", "partially_completed"}:
            raise DocumentError(f"OCR job {job_id} is {status if status in {'failed', 'rejected'} else 'not finished within the polling limit'}. Do not resubmit it automatically.")
        download = (await bounded_response(client, "GET", f"{prefix}/{job_id}/download-url", headers=headers)).json()
        from .research import _is_public_http_url
        url = download.get("url")
        download_headers = download.get("headers") or {}
        if (download.get("method", "GET").upper() != "GET" or not isinstance(url, str) or not _is_public_http_url(url)
                or not isinstance(download_headers, dict) or any(str(name).lower() in {"authorization", "api-subscription-key", "host"} for name in download_headers)):
            raise DocumentError("OCR result download is not a safe public GET; credentials were not forwarded.")
        result = await bounded_response(client, "GET", url, headers=download_headers)
        text = archive_text(result.content).strip()
        if not text:
            raise DocumentError("OCR returned no readable text.")
        return {"text": text, "job_id": job_id, "status": status, "usage": usage, "provider": "sarvam-document-ai",
                "content_sha256": hashlib.sha256(raw).hexdigest(), "untrusted_content": True}
    except DocumentError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, OSError) as exc:
        raise DocumentError("Sarvam OCR transport or response failed. No automatic resubmission; raw provider details withheld.") from exc
    finally:
        if owns:
            await client.aclose()


async def describe_image(workspace, file_path, question, *, client=None, key=None):
    if not isinstance(question, str) or not question.strip() or len(question) > 8000:
        raise DocumentError("Provide a nonempty image question up to 8000 characters.")
    _, raw, mime = read_document(workspace, file_path, image_only=True)
    key = key or sarvam_key()
    owns = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)
    try:
        response = await bounded_response(client, "POST", API + "/v2/chat/completions", headers={"api-subscription-key": key}, json={
            "model": "gemma4", "stream": False, "max_tokens": 2048,
            "messages": [{"role": "user", "content": [{"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"}}]}]})
        result = response.json()
        choice = result["choices"][0]
        text = choice["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise DocumentError("Image model returned no readable answer.")
        return {"text": text, "provider": "sarvam", "model": "gemma4", "finish_reason": choice.get("finish_reason"),
                "content_sha256": hashlib.sha256(raw).hexdigest(), "untrusted_content": True}
    except DocumentError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise DocumentError("Sarvam image transport or response failed; raw provider details withheld.") from exc
    finally:
        if owns:
            await client.aclose()
