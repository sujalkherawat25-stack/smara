"""Safe, evidence-first research execution for one durable task step."""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import httpx

from .config import settings
from .store import TaskStore

MAX_SOURCE_BYTES = 1_000_000
MAX_EXCERPT_CHARS = 1_800
_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "msclkid", "ref", "ref_src", "source",
    "utm_campaign", "utm_content", "utm_medium", "utm_source", "utm_term",
}
_DISCOVERY_ONLY_HOSTS = {
    "aiagentsdirectory.com", "aiagentstore.ai", "youtube.com", "www.youtube.com",
    "youtu.be", "reddit.com", "www.reddit.com", "quora.com", "medium.com",
    "substack.com", "news.google.com",
}
_PRIMARY_ROOTS = {
    "anthropic.com", "bnbchain.org", "cloud.google.com", "developers.google.com",
    "github.com", "microsoft.com", "nvidia.com", "okta.com", "openai.com",
    "perplexity.ai", "snowflake.com", "x.ai",
}
_REPUTABLE_REPORTING_ROOTS = {
    "apnews.com", "bloomberg.com", "reuters.com", "techcrunch.com",
    "theverge.com", "wired.com", "wsj.com", "nytimes.com", "ft.com",
}


def canonical_source_url(url: str) -> str:
    """Normalize a public URL for deduplication without changing its target."""
    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    try:
        port = parsed.port
    except ValueError:
        # Leave malformed ports untouched; the normal public-URL validator
        # will reject the source before retrieval.
        port = None
    netloc = host
    if port and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
         if key.lower() not in _TRACKING_QUERY_KEYS and not key.lower().startswith("utm_")],
        doseq=True,
    )
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def source_quality(url: str, title: str = "", snippet: str = "") -> tuple[str, list[str]]:
    """Classify a result so weak discovery leads cannot look like proof.

    This is deliberately advisory rather than a hard allowlist: the agent can
    still follow an unusual source, but the evidence ledger and final prompt
    make its lower confidence explicit.
    """
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    root = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
    lower_text = f"{title} {snippet}".lower()
    if host in _DISCOVERY_ONLY_HOSTS or root in _DISCOVERY_ONLY_HOSTS:
        return "discovery_only", ["discovery_only_source"]
    if host.endswith(".gov") or host.endswith(".edu") or host.endswith(".ac.uk") or root in _PRIMARY_ROOTS:
        return "primary", ["primary_source"]
    if host.startswith(("docs.", "developer.", "developers.", "blog.", "press.", "newsroom.", "research.")):
        return "primary", ["primary_source"]
    if root in _REPUTABLE_REPORTING_ROOTS:
        return "secondary", ["independent_reporting"]
    if "official announcement" in lower_text or "press release" in lower_text:
        return "secondary", ["reported_official_claim"]
    return "unclassified", ["unclassified_source"]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.parts: list[str] = []
        self.published_at: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        self._in_title = tag == "title"
        values = {str(key).lower(): str(value).strip() for key, value in attrs if value}
        if tag == "time" and values.get("datetime") and not self.published_at:
            self.published_at = values["datetime"][:100]
        if tag == "meta" and not self.published_at:
            key = (values.get("property") or values.get("name") or values.get("itemprop") or "").lower()
            if key in {"article:published_time", "datepublished", "date", "pubdate", "dc.date"} and values.get("content"):
                self.published_at = values["content"][:100]

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


class ResearchSynthesisError(RuntimeError):
    """A bounded research synthesis could not produce a safe cited answer."""


class OpenAIResearchSynthesizer:
    """Optional citation-constrained synthesis over verified evidence only."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 45.0):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def synthesize(self, *, question: str, evidence: list[dict]) -> str:
        if not self._base_url or not self._api_key or not self._model:
            raise ResearchSynthesisError("research synthesis provider is not configured")
        context = "\n\n".join(
            f"{item['citation_label']} {item.get('title') or item['url']}\n{item.get('excerpt') or ''}"
            for item in evidence[:8]
        )[:12_000]
        system = (
            "You write a concise research synthesis from verified evidence. "
            "Use only the supplied evidence; do not add outside facts, URLs, or citations. "
            "Every factual sentence must end with one or more supplied citation labels such as [1]. "
            "If the evidence is insufficient, say so clearly. Return plain Markdown, no preamble."
        )
        payload = {
            "model": self._model,
            "temperature": 0,
            "max_tokens": 1000,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Question: {question[:4_000]}\n\nVerified evidence:\n{context}"},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ResearchSynthesisError("research synthesis provider is unavailable") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ResearchSynthesisError("research synthesis provider returned an invalid response") from exc
        if not isinstance(content, str):
            raise ResearchSynthesisError("research synthesis provider returned invalid text")
        return content.strip()


@dataclass(frozen=True)
class RetrievedSource:
    title: str
    excerpt: str
    content_sha256: str
    retrieved_at: str
    published_at: str | None = None


def _domain_policy(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    allowed = [item.strip().lower().lstrip(".") for item in settings.research_allowed_domains.split(",") if item.strip()]
    blocked = [item.strip().lower().lstrip(".") for item in settings.research_blocked_domains.split(",") if item.strip()]
    matches = lambda domain: host == domain or host.endswith(f".{domain}")
    if any(matches(domain) for domain in blocked):
        return "blocked"
    if allowed and not any(matches(domain) for domain in allowed):
        return "unclassified"
    return "allowed" if allowed else "unclassified"


def _agreement_counts(items: list[dict]) -> dict[str, int]:
    token_sets = {
        item["id"]: set(re.findall(r"[a-z0-9]{4,}", (item.get("excerpt") or "").lower()))
        for item in items
    }
    counts: dict[str, int] = {}
    for item in items:
        own = token_sets[item["id"]]
        count = 0
        for other in items:
            if other["id"] == item["id"]:
                continue
            shared = own & token_sets[other["id"]]
            if len(shared) >= 3 and len(shared) / max(1, min(len(own), len(token_sets[other["id"]]))) >= 0.08:
                count += 1
        counts[item["id"]] = count
    return counts


async def fetch_public_source(client: httpx.AsyncClient, initial_url: str) -> RetrievedSource:
    """Read one public HTML/text source with redirect and size limits."""
    current_url = initial_url
    for _ in range(5):
        if not _is_public_http_url(current_url):
            raise ValueError("Source URL or redirect target is not publicly routable HTTP(S).")
        response = await client.get(current_url)
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise ValueError("Source returned a redirect without a location.")
            current_url = urljoin(current_url, location)
            continue
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
        title = extracted.title.strip()[:500] or urlparse(str(response.url)).hostname or initial_url
        return RetrievedSource(title, excerpt, hashlib.sha256(raw).hexdigest(), _now(), extracted.published_at)
    raise ValueError("Source exceeded the redirect limit.")


class ResearchExecutor:
    def __init__(self, store: TaskStore, http_client: httpx.AsyncClient | None = None, search_tool=None, synthesizer=None):
        self._store = store
        self._http = http_client
        self._search_tool = search_tool
        self._synthesizer = synthesizer

    async def run_step(self, task: dict) -> ResearchStepResult:
        if task["name"] == "research.discover_sources":
            return await self._discover_sources(task)
        if task["name"] == "research.fetch_sources":
            return await self._fetch_sources(task)
        if task["name"] == "research.verify_evidence":
            return self._verify_evidence(task)
        if task["name"] == "research.write_report":
            return await self._write_report(task)
        raise ValueError(f"Unsupported research step: {task['name']}")

    async def _discover_sources(self, task: dict) -> ResearchStepResult:
        from .research_tools import ResearchToolError, WebSearchTool
        try:
            hits = await (self._search_tool or WebSearchTool()).search(task["objective"], max_results=5)
        except ResearchToolError as exc:
            raise ValueError(str(exc)) from exc
        # Keep all usable leads available, but put first-party and reputable
        # reporting ahead of directories, videos, and other discovery-only
        # pages. This improves the default evidence set without hard-blocking
        # a niche source when it is the only lead returned.
        quality_order = {"primary": 0, "secondary": 1, "unclassified": 2, "discovery_only": 3}
        hits = sorted(hits, key=lambda hit: quality_order.get(getattr(hit, "quality", "unclassified"), 2))
        added = sum(self._store.add_evidence(task["id"], task["account_id"], hit.url, title=hit.title) for hit in hits)
        self._store.append_event(task["id"], "research.sources_discovered", f'{{"found":{len(hits)},"added":{added}}}')
        if not added:
            raise ValueError("The configured web-search provider returned no usable public sources.")
        return ResearchStepResult(f"Discovered {added} public source(s) for the evidence ledger.")

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
                policy = _domain_policy(item["url"])
                if policy == "blocked":
                    self._store.update_evidence(item["id"], task["id"], status="blocked", domain_policy=policy, error="Source domain is blocked by Smara policy.")
                    continue
                try:
                    source = await fetch_public_source(client, item["url"])
                    self._store.update_evidence(item["id"], task["id"], status="fetched", title=source.title, retrieved_at=source.retrieved_at, published_at=source.published_at, content_sha256=source.content_sha256, excerpt=source.excerpt, domain_policy=policy)
                    fetched += 1
                except (httpx.HTTPError, ValueError) as exc:
                    self._store.update_evidence(item["id"], task["id"], status="failed", error=str(exc)[:1000])
        finally:
            if owned_client:
                await client.aclose()
        self._store.append_event(task["id"], "research.sources_retrieved", f'{{"fetched":{fetched}}}')
        return ResearchStepResult(f"Retrieved {fetched} source(s); failed or blocked sources remain visible in the evidence ledger.")

    def _verify_evidence(self, task: dict) -> ResearchStepResult:
        evidence = self._store.evidence(task["id"], task["account_id"])
        fetched = [item for item in evidence if item["status"] == "fetched"]
        if not fetched:
            raise ValueError("No retrievable sources were available; Smara will not generate an uncited report.")
        agreement = _agreement_counts(fetched)
        verified_count = 0
        for index, item in enumerate(fetched, start=1):
            flags: list[str] = []
            tier, source_flags = source_quality(item["url"], item.get("title") or "", item.get("excerpt") or "")
            flags.extend(source_flags)
            if not item["url"].startswith("https://"):
                flags.append("http_source")
            if not item.get("published_at"):
                flags.append("missing_publication_date")
            if item.get("domain_policy", "unclassified") == "unclassified":
                flags.append("domain_unclassified")
            if agreement.get(item["id"], 0):
                flags.append("cross_source_agreement")
            elif len(fetched) > 1:
                flags.append("no_cross_source_agreement")
            confidence = min(0.95, 0.55 + (0.1 if item["url"].startswith("https://") else 0) + (0.1 if len(item.get("excerpt") or "") >= 500 else 0) + (0.05 if item.get("published_at") else 0) + (0.15 if agreement.get(item["id"], 0) else 0) + (0.1 if tier == "primary" else 0) - (0.2 if tier == "discovery_only" else 0))
            claim = f"Evidence retrieved from {item.get('title') or item['url']} (source tier: {tier})."
            notes = "Quality checks: " + (", ".join(dict.fromkeys(flags)) if flags else "passed")
            self._store.update_evidence(item["id"], task["id"], status="verified", title=item.get("title"), retrieved_at=item.get("retrieved_at"), published_at=item.get("published_at"), content_sha256=item.get("content_sha256"), excerpt=item.get("excerpt"), claim=claim, confidence=confidence, citation_label=f"[{index}]", domain_policy=item.get("domain_policy", "unclassified"), quality_flags=flags, agreement_count=agreement.get(item["id"], 0), verification_notes=notes)
            verified_count += 1
        self._store.append_event(task["id"], "research.evidence_verified", f'{{"verified":{verified_count},"agreement_sources":{sum(1 for count in agreement.values() if count)}}}')
        return ResearchStepResult(f"Verified {verified_count} retrieved source(s); quality flags remain visible in the ledger.", verified_evidence_count=verified_count)

    @staticmethod
    def _validate_synthesis(content: str, evidence: list[dict]) -> str:
        content = content.strip()
        if not content or len(content) > 8_000:
            raise ResearchSynthesisError("research synthesis was empty or exceeded its output limit")
        labels = {str(item.get("citation_label") or "").strip("[]") for item in evidence}
        citations = set(re.findall(r"\[(\d+)\]", content))
        if not citations or not citations.issubset(labels):
            raise ResearchSynthesisError("research synthesis contained an invalid citation")
        return content

    async def _write_report(self, task: dict) -> ResearchStepResult:
        evidence = self._store.evidence(task["id"], task["account_id"])
        verified = [item for item in evidence if item["status"] == "verified"]
        if not verified:
            raise ValueError("No verified evidence is available for a report.")
        synthesis = None
        if self._synthesizer is not None:
            try:
                synthesis = self._validate_synthesis(
                    await self._synthesizer.synthesize(question=task["objective"], evidence=verified), verified
                )
                self._store.append_event(task["id"], "research.report_synthesized", json.dumps({"citations": len(set(re.findall(r"\[(\d+)\]", synthesis)))}))
            except ResearchSynthesisError as exc:
                self._store.append_event(task["id"], "research.report_synthesis_fallback", json.dumps({"reason": str(exc)}))
        else:
            self._store.append_event(task["id"], "research.report_synthesis_skipped", '{"reason":"not_configured"}')
        lines = [f"# {task['title']}", "", "## Research question", task["objective"], ""]
        if synthesis:
            lines += ["## Synthesized findings", synthesis, "", "## Evidence ledger"]
        else:
            lines += ["## Evidence-backed notes"]
        for item in verified:
            quality = ", ".join(json.loads(item.get("quality_flags") or "[]")) or "passed"
            published = item.get("published_at") or "not provided"
            lines += [f"### {item['citation_label']} {item['title']}", item["excerpt"], f"Published: {published}", f"Quality checks: {quality}", "", f"Source: {item['url']}", ""]
        failures = [item for item in evidence if item["status"] in {"failed", "blocked"}]
        if failures:
            lines += ["## Limitations", f"{len(failures)} supplied source(s) could not be retrieved or were blocked. They were not used for any report content.", ""]
        lines += ["## Sources"] + [f"{item['citation_label']} [{item['title']}]({item['url']})" for item in verified]
        report = "\n".join(lines).strip() + "\n"
        artifact = self._store.create_artifact(task["id"], task["account_id"], kind="research_report", name="research-report.md", content=report)
        self._store.append_event(task["id"], "research.report_created", f'{{"artifact_id":"{artifact["id"]}"}}')
        return ResearchStepResult("Created a cited research report artifact.", report=report, verified_evidence_count=len(verified))
