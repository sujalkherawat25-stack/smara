"""Evidence-first local research report builder.

This module intentionally does not invent market facts when search or page
fetching is unavailable. It turns the evidence actually returned by local
connectors into a traceable report and makes missing evidence explicit.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx


class DeepResearchEngine:
    """Collect configured-search evidence and produce an auditable report."""

    def __init__(self, workspace: Path | str | None = None):
        self.workspace = (Path(workspace) if workspace else Path.cwd()).resolve()
        self.reports_dir = self.workspace / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def fan_out_research_vectors(self, topic: str) -> list[dict[str, str]]:
        """Split a broad question into independent evidence-gathering queries."""
        clean_topic = topic.strip()
        return [
            {"vector": "landscape", "title": "Landscape and primary actors", "query": f"{clean_topic} overview primary sources"},
            {"vector": "technical", "title": "Technical architecture", "query": f"{clean_topic} technical architecture primary source"},
            {"vector": "evidence", "title": "Benchmarks and evaluations", "query": f"{clean_topic} benchmark evaluation paper"},
            {"vector": "practice", "title": "Implementation practice", "query": f"{clean_topic} implementation documentation"},
            {"vector": "risks", "title": "Limitations and risks", "query": f"{clean_topic} limitations safety reliability research"},
        ]

    def retrieve_multi_vector_sources(self, vectors: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Use configured desktop search connectors; never substitute canned sources."""
        collected: list[dict[str, Any]] = []
        try:
            from .desktop_executor import resolve_local_credential
            from .desktop_integrations import execute_local_integration
        except ImportError:
            return collected

        def credential_resolver(keys: list[str]) -> dict[str, str]:
            return {key: resolve_local_credential(key) for key in keys}

        for vector in vectors:
            try:
                raw = execute_local_integration(
                    {"provider": "tavily", "operation": "search", "query": vector["query"], "max_results": 3},
                    credential_resolver,
                )
                response = json.loads(raw)
            except Exception:
                continue
            for result in response.get("results", []):
                url = str(result.get("url") or "").strip()
                if not url.startswith(("https://", "http://")):
                    continue
                collected.append(
                    {
                        "vector": vector["vector"],
                        "title": str(result.get("title") or url),
                        "url": url,
                        "snippet": str(result.get("snippet") or result.get("content") or "").strip(),
                    }
                )
        return collected

    def scrape_primary_evidence(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fetch a bounded excerpt for each source and record fetch outcome."""
        records: list[dict[str, Any]] = []
        for source in sources[:12]:
            record = dict(source)
            record["fetched"] = False
            url = str(record.get("url") or "")
            try:
                with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                    response = client.get(url, headers={"User-Agent": "SmaraResearch/1.0"})
                if response.status_code == 200:
                    text = re.sub(r"<[^>]+>", " ", response.text)
                    record["scraped_content"] = " ".join(text.split())[:3_000]
                    record["fetched"] = True
                else:
                    record["fetch_error"] = f"HTTP {response.status_code}"
            except Exception as exc:
                record["fetch_error"] = type(exc).__name__
            records.append(record)
        return records

    def synthesize_market_analysis(self, topic: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
        """Create source-attributed notes only from supplied evidence."""
        if not sources:
            raise RuntimeError(
                "No research evidence was retrieved. Configure a local Tavily or Exa connector and retry; "
                "Smara will not create a report from invented sources."
            )
        source_notes: list[dict[str, Any]] = []
        vectors: set[str] = set()
        limitations: list[str] = []
        for source in sources:
            vector = str(source.get("vector") or "general")
            vectors.add(vector)
            excerpt = str(source.get("scraped_content") or source.get("snippet") or "").strip()
            if not excerpt:
                limitations.append(f"No extractable excerpt for {source.get('url', 'a source')}")
            if source.get("fetch_error"):
                limitations.append(f"Fetch unavailable for {source.get('url', 'a source')}: {source['fetch_error']}")
            source_notes.append(
                {
                    "vector": vector,
                    "title": str(source.get("title") or source.get("url") or "Untitled source"),
                    "url": str(source.get("url") or ""),
                    "excerpt": excerpt[:1_500],
                    "fetched": bool(source.get("fetched")),
                }
            )
        return {
            "topic": topic,
            "executive_summary": (
                f"Collected {len(source_notes)} source records across {len(vectors)} research areas for {topic}. "
                "The report preserves source excerpts and fetch status; it does not infer facts beyond that evidence."
            ),
            "competitive_matrix": [],
            "market_drivers": [],
            "headwinds": [],
            "source_notes": source_notes,
            "limitations": list(dict.fromkeys(limitations)) or ["Source text is limited to the returned search excerpts."],
            "sources_count": len(source_notes),
        }

    def generate_executive_report(self, topic: str, analysis: dict[str, Any]) -> Path:
        """Write a traceable markdown evidence report without unsupported claims."""
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", topic.lower()).strip("_")[:40] or "research"
        report_file = self.reports_dir / f"research_evidence_{slug}.md"
        lines = [
            f"# Research Evidence Report: {topic}",
            "",
            f"> Generated: {time.strftime('%Y-%m-%d')}",
            "> Status: evidence-bound; no unsupported market assertions included.",
            "",
            "## Summary",
            "",
            str(analysis["executive_summary"]),
            "",
            "## Source ledger",
            "",
        ]
        for index, note in enumerate(analysis.get("source_notes", []), start=1):
            lines.extend(
                [
                    f"### {index}. {note['title']}",
                    f"- Area: `{note['vector']}`",
                    f"- URL: {note['url']}",
                    f"- Fetch status: {'fetched' if note['fetched'] else 'search excerpt only'}",
                    f"- Evidence: {note['excerpt'] or 'No extractable excerpt was available.'}",
                    "",
                ]
            )
        lines.extend(["## Limitations", ""])
        lines.extend(f"- {item}" for item in analysis.get("limitations", []))
        lines.extend(["", "---", "This report is a source ledger, not a substitute for reviewing the cited material.", ""])
        report_file.write_text("\n".join(lines), encoding="utf-8")
        return report_file

    def run_full_pipeline(self, topic: str) -> dict[str, Any]:
        """Run bounded collection, optional fetches, and source-ledger creation."""
        vectors = self.fan_out_research_vectors(topic)
        sources = self.retrieve_multi_vector_sources(vectors)
        scraped = self.scrape_primary_evidence(sources)
        analysis = self.synthesize_market_analysis(topic, scraped)
        report_path = self.generate_executive_report(topic, analysis)
        return {"topic": topic, "analysis": analysis, "report_path": str(report_path), "sources_count": len(scraped)}
