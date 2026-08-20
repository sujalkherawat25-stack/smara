"""Safe, evidence-first research execution for one durable task step."""
from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .store import TaskStore

MAX_SOURCE_BYTES = 1_000_000
MAX_EXCERPT_CHARS = 1_800


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs) -> None:
        self._in_title = tag.lower() == "title"

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title": self._in_title = False

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if cleaned:
            self.parts.append(cleaned)
            if self._in_title: self.title += (" " if self.title else "") + cleaned


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_public_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        return False
    try:
        return not ipaddress.ip_address(host).is_private and not ipaddress.ip_address(host).is_loopback
    except ValueError:
        try:
            addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            return bool(addresses) and all(not ipaddress.ip_address(item[4][0]).is_private and not ipaddress.ip_address(item[4][0]).is_loopback for item in addresses)
        except socket.gaierror:
            return False


@dataclass(frozen=True)
class ResearchStepResult:
    text: str
    report: str | None = None
    verified_evidence_count: int = 0


class ResearchExecutor:
    def __init__(self, store: TaskStore, http_client: httpx.AsyncClient | None = None):
        self._store = store
        self._http = http_client

    async def run_step(self, task: dict) -> ResearchStepResult:
        if task["name"] == "research.fetch_sources":
            return await self._fetch_sources(task)
        if task["name"] == "research.verify_evidence":
            return self._verify_evidence(task)
        if task["name"] == "research.write_report":
            return self._write_report(task)
        raise ValueError(f"Unsupported research step: {task['name']}")

    async def _fetch_sources(self, task: dict) -> ResearchStepResult:
        evidence = self._store.evidence(task["id"], task["account_id"])
        owned_client = self._http is None
        client = self._http or httpx.AsyncClient(timeout=httpx.Timeout(12.0), follow_redirects=False, headers={"User-Agent": "SmaraResearch/0.1 (+evidence-ledger)"})
        fetched = 0
        try:
            for item in evidence:
                if item["status"] != "pending":
                    continue
                if not _is_public_http_url(item["url"]):
                    self._store.update_evidence(item["id"], task["id"], status="blocked", error="Only publicly routable HTTP(S) source URLs are allowed.")
                    continue
                try:
                    response = await self._safe_get(client, item["url"])
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    raw = response.content[:MAX_SOURCE_BYTES]
                    if len(response.content) > MAX_SOURCE_BYTES:
                        raise ValueError("Source exceeded the 1 MB retrieval limit.")
                    if "html" not in content_type and not content_type.startswith("text/plain"):
                        raise ValueError(f"Unsupported source content type: {content_type or 'unknown'}")
                    extracted = _TextExtractor()
                    extracted.feed(raw.decode(response.encoding or "utf-8", errors="replace"))
                    excerpt = re.sub(r"\s+", " ", html.unescape(" ".join(extracted.parts))).strip()[:MAX_EXCERPT_CHARS]
                    if len(excerpt) < 80:
                        raise ValueError("Source did not contain enough readable text to cite.")
                    title = extracted.title.strip()[:500] or urlparse(str(response.url)).hostname or item["url"]
                    self._store.update_evidence(item["id"], task["id"], status="fetched", title=title, retrieved_at=_now(), content_sha256=hashlib.sha256(raw).hexdigest(), excerpt=excerpt)
                    fetched += 1
                except (httpx.HTTPError, ValueError) as exc:
                    self._store.update_evidence(item["id"], task["id"], status="failed", error=str(exc)[:1000])
        finally:
            if owned_client:
                await client.aclose()
        self._store.append_event(task["id"], "research.sources_retrieved", f'{{"fetched":{fetched}}}')
        return ResearchStepResult(f"Retrieved {fetched} source(s); failed or blocked sources remain visible in the evidence ledger.")

    async def _safe_get(self, client: httpx.AsyncClient, initial_url: str) -> httpx.Response:
        current_url = initial_url
        for _ in range(5):
            if not _is_public_http_url(current_url):
                raise ValueError("Redirect target is not a publicly routable HTTP(S) URL.")
            response = await client.get(current_url)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Source returned a redirect without a location.")
                current_url = urljoin(current_url, location)
                continue
            return response
        raise ValueError("Source exceeded the redirect limit.")

    def _verify_evidence(self, task: dict) -> ResearchStepResult:
        evidence = self._store.evidence(task["id"], task["account_id"])
        fetched = [item for item in evidence if item["status"] == "fetched"]
        if not fetched:
            raise ValueError("No retrievable sources were available; Smara will not generate an uncited report.")
        for index, item in enumerate(fetched, start=1):
            confidence = 0.85 if item["url"].startswith("https://") and len(item.get("excerpt") or "") >= 500 else 0.65
            claim = f"Evidence retrieved from {item.get('title') or item['url']}."
            self._store.update_evidence(item["id"], task["id"], status="verified", title=item.get("title"), retrieved_at=item.get("retrieved_at"), content_sha256=item.get("content_sha256"), excerpt=item.get("excerpt"), claim=claim, confidence=confidence, citation_label=f"[{index}]")
        self._store.append_event(task["id"], "research.evidence_verified", f'{{"verified":{len(fetched)}}}')
        return ResearchStepResult(f"Verified {len(fetched)} retrieved source(s).", verified_evidence_count=len(fetched))

    def _write_report(self, task: dict) -> ResearchStepResult:
        evidence = self._store.evidence(task["id"], task["account_id"])
        verified = [item for item in evidence if item["status"] == "verified"]
        if not verified:
            raise ValueError("No verified evidence is available for a report.")
        lines = [f"# {task['title']}", "", "## Research question", task["objective"], "", "## Evidence-backed notes"]
        for item in verified:
            lines += [f"### {item['citation_label']} {item['title']}", item["excerpt"], "", f"Source: {item['url']}", ""]
        failures = [item for item in evidence if item["status"] in {"failed", "blocked"}]
        if failures:
            lines += ["## Limitations", f"{len(failures)} supplied source(s) could not be retrieved or were blocked. They were not used for any report content.", ""]
        lines += ["## Sources"] + [f"{item['citation_label']} [{item['title']}]({item['url']})" for item in verified]
        report = "\n".join(lines).strip() + "\n"
        artifact = self._store.create_artifact(task["id"], task["account_id"], kind="research_report", name="research-report.md", content=report)
        self._store.append_event(task["id"], "research.report_created", f'{{"artifact_id":"{artifact["id"]}"}}')
        return ResearchStepResult("Created a cited research report artifact.", report=report, verified_evidence_count=len(verified))
