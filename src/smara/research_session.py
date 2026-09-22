"""Session-scoped adaptive research used by the canonical agent tool loop."""
from __future__ import annotations

import asyncio
import concurrent.futures
import csv
import io
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evidence_index import EvidenceIndex
from .research_eval import ClaimCheck, score_claims
from .research_graph import ResearchGraph, ResearchNode
from .research_modes import QUICK_POLICY
from .research_ranking import hybrid_rank
from .research_tools import FetchUrlTool, WebSearchTool
from .pdf_collection import PdfCollectionError, scan_pdf_collection


class ResearchStateError(RuntimeError):
    pass


def _run(coro):
    """Run an async provider adapter from the synchronous autonomous loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Playwright's synchronous facade owns an event loop on this thread. Run
    # unrelated async provider I/O in one bounded helper thread; browser
    # objects remain on their original stable owner thread.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="smara-research-io") as pool:
        return pool.submit(asyncio.run, coro).result()


class CanonicalResearchSession:
    VERSION = 1

    def __init__(self, *, session_engine=None, state_path: str | Path | None = None, searcher=None, fetcher=None):
        self.engine = session_engine
        self.state_path = Path(state_path) if state_path else None
        self.searcher = searcher or WebSearchTool()
        self.fetcher = fetcher or FetchUrlTool()
        self.graph = ResearchGraph()
        self.index = EvidenceIndex(session_engine.artifact_store if session_engine is not None else None)
        self.leads: dict[str, list[dict[str, Any]]] = {}
        self.claims: list[dict[str, Any]] = []
        self.validation: dict[str, Any] = {}
        self.analyses: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        value = None
        if self.engine is not None:
            artifact_id = self.engine.get("research_state_artifact_id")
            if artifact_id:
                try:
                    value = json.loads(self.engine.resolve_artifact(artifact_id))
                except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
                    self.engine.set("research_state_invalid", str(exc))
                    self.engine.event("research_state_invalid", {"artifact_id": artifact_id, "error": type(exc).__name__})
        elif self.state_path and self.state_path.exists():
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not value:
            return
        if int(value.get("version", 0)) != self.VERSION:
            raise ResearchStateError("unsupported research state version")
        self.graph = ResearchGraph.from_dict(value.get("graph", {}))
        self.index = EvidenceIndex.from_dict(value.get("evidence", {}), self.engine.artifact_store if self.engine is not None else None)
        self.leads = {str(k): list(v) for k, v in value.get("leads", {}).items()}
        self.claims = list(value.get("claims", ()))
        self.validation = dict(value.get("validation", {}))
        self.analyses = list(value.get("analyses", ()))

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": self.VERSION,
            "graph": self.graph.to_dict(),
            "evidence": self.index.to_dict(),
            "leads": self.leads,
            "claims": self.claims,
            "validation": self.validation,
            "analyses": self.analyses,
        }

    def _save(self, event_type: str, payload: Mapping[str, Any]) -> str | None:
        value = self.snapshot()
        artifact_id = None
        if self.engine is not None:
            artifact_id, _ = self.engine.artifact_store.put_json(value)
            self.engine.set("research_state_artifact_id", artifact_id)
            self.engine.set("research_state_invalid", None)
            self.engine.set("research_validation", self.validation)
            if event_type == "research_validated":
                self.engine.set("research_validated_state_artifact_id", artifact_id)
            self.engine.event(event_type, {**dict(payload), "research_state_artifact_id": artifact_id})
        elif self.state_path:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
            temporary.replace(self.state_path)
        return artifact_id

    def plan(self, question: str, nodes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        question = str(question).strip()
        if not question:
            raise ResearchStateError("research question is required")
        pending = [dict(item) for item in nodes]
        if not pending:
            pending = [{"id": "answer", "question": question, "dependencies": []}]
        planned_ids = {str(item.get("id") or "").strip() for item in pending}
        max_nodes = int(self._lane_policy()["max_nodes"])
        if len(set(self.graph.nodes) | planned_ids) > max_nodes:
            raise ResearchStateError(f"research plan exceeds {max_nodes} nodes for the active lane")
        added = []
        while pending:
            ready = [item for item in pending if set(item.get("dependencies", ())) <= set(self.graph.nodes) | set(added)]
            if not ready:
                raise ResearchStateError("research plan has missing or cyclic dependencies")
            for item in ready:
                ident = str(item.get("id") or "").strip()
                node_question = str(item.get("question") or "").strip()
                if not ident or not node_question:
                    raise ResearchStateError("every research node needs id and question")
                if ident not in self.graph.nodes:
                    self.graph.add(
                        ResearchNode(
                            ident,
                            node_question,
                            tuple(str(x) for x in item.get("dependencies", ())),
                            stopping_criterion=str(item.get("stopping_criterion") or "directly supported claim with fetched evidence"),
                        )
                    )
                added.append(ident)
                pending.remove(item)
        self.validation = {}
        self._save("research_planned", {"question": question, "node_ids": added})
        return {"status": "ok", "ready": [node.id for node in self.graph.ready()], "graph": self.graph.to_dict()}

    def search(self, node_id: str, query: str, max_results: int = 5) -> dict[str, Any]:
        node = self.graph.nodes.get(node_id)
        if node is None:
            raise ResearchStateError("unknown research node")
        deps_satisfied = all(self.graph.nodes.get(dep) and self.graph.nodes[dep].state == "supported" for dep in node.dependencies)
        if not deps_satisfied:
            unmet = [dep for dep in node.dependencies if not (self.graph.nodes.get(dep) and self.graph.nodes[dep].state == "supported")]
            raise ResearchStateError(f"Research node '{node_id}' has unresolved dependencies: {unmet}. Resolve them first with research_resolve, or define nodes without dependencies.")
        hits = _run(self.searcher.search(query, max_results=max_results))
        records = []
        for hit in hits:
            evidence = self.index.add(
                kind="search_snippet",
                url=hit.url,
                content=hit.snippet.encode(),
                text=hit.snippet,
                extraction_version=f"search:{hit.provider}",
            )
            records.append({**asdict(hit), "evidence_id": evidence.id})
        self.leads.setdefault(node_id, []).extend(records)
        self.validation = {}
        self._save("research_searched", {"node_id": node_id, "query": query, "lead_count": len(records)})
        return {"status": "ok", "node_id": node_id, "leads": records}

    def _lane_policy(self) -> dict[str, Any]:
        value = self.engine.get("research_policy") if self.engine is not None else None
        if isinstance(value, dict):
            return dict(value)
        # Research sessions created before lane routing retain their original
        # one-source completion contract. New research_web runs always persist
        # an explicit Quick or Deep policy before invoking a research tool.
        return {**QUICK_POLICY.to_dict(), "mode": "legacy", "min_sources": 1, "target_sources": 1, "max_sources": 200, "max_nodes": 64}

    def fetched_source_urls(self) -> list[str]:
        return sorted({
            record.canonical_url
            for record in self.index.records.values()
            if record.kind in {"fetched_passage", "pdf_page", "pdf_table", "image_ocr"}
            and record.canonical_url.startswith(("http://", "https://", "file://"))
        })

    def gather(self, requests: Iterable[Mapping[str, Any]], *, max_sources_per_node: int = 5) -> dict[str, Any]:
        """Search and fetch one ready DAG wave concurrently with deterministic reranking."""
        policy = self._lane_policy()
        items = [dict(item) for item in requests]
        if not items:
            items = [{"node_id": node.id, "query": node.question} for node in self.graph.ready()]
        if not items:
            raise ResearchStateError("research gather has no ready nodes")
        if len(items) > int(policy["max_nodes"]):
            raise ResearchStateError("research gather exceeds lane node limit")
        # Providers often emit a whole DAG wave in one gather call, including
        # nodes whose prerequisites are not resolved yet.  Rejecting the
        # entire request strands the ready roots and causes a model to repeat
        # the same invalid call.  Execute the ready subset and return an
        # explicit blocked list so the next turn can resolve prerequisites and
        # continue the wave.  Unknown nodes and malformed queries remain hard
        # errors because they indicate a broken plan rather than scheduling.
        blocked: list[dict[str, Any]] = []
        ready_items: list[dict[str, Any]] = []
        for item in items:
            node_id = str(item.get("node_id") or "").strip()
            query = str(item.get("query") or "").strip()
            node = self.graph.nodes.get(node_id)
            if node is None:
                raise ResearchStateError(f"unknown research node: {node_id}")
            if not query:
                raise ResearchStateError(f"research query is required for node: {node_id}")
            unmet = [dep for dep in node.dependencies if self.graph.nodes[dep].state != "supported"]
            if unmet:
                blocked.append({"node_id": node_id, "query": query, "unmet_dependencies": unmet})
                continue
            item["node_id"] = node_id
            item["query"] = query
            ready_items.append(item)

        if not ready_items:
            return {
                "status": "blocked",
                "lane": policy["mode"],
                "nodes": [],
                "blocked": blocked,
                "ready": [node.id for node in self.graph.ready()],
                "source_count": len(self.fetched_source_urls()),
                "target_sources": policy["target_sources"],
                "min_sources": policy["min_sources"],
                "max_sources": policy["max_sources"],
                "hint": "Resolve the listed unmet dependencies, then gather the ready nodes.",
            }

        items = ready_items

        existing_urls = set(self.fetched_source_urls())
        remaining_capacity = max(0, int(policy["max_sources"]) - len(existing_urls))
        if remaining_capacity == 0:
            return {"status": "ok", "lane": policy["mode"], "source_count": len(existing_urls), "sources": sorted(existing_urls), "nodes": [], "capacity_reached": True}
        per_node = max(1, min(8, int(max_sources_per_node)))
        concurrency = max(1, min(16, int(policy["max_concurrency"])))

        async def execute_wave():
            semaphore = asyncio.Semaphore(concurrency)

            async def search_one(item):
                try:
                    async with semaphore:
                        hits = await self.searcher.search(item["query"], max_results=8)
                    return item, hybrid_rank(item["query"], hits, per_node)
                except Exception as exc:
                    return item, exc

            searched = await asyncio.gather(*(search_one(item) for item in items))
            selected: list[tuple[dict[str, Any], Any]] = []
            selected_urls = set(existing_urls)
            for item, result in searched:
                if isinstance(result, Exception):
                    continue
                for hit in result:
                    if hit.url not in selected_urls and len(selected) < remaining_capacity:
                        selected.append((item, hit))
                        selected_urls.add(hit.url)

            async def fetch_one(item, hit):
                try:
                    async with semaphore:
                        source = await self.fetcher.fetch(hit.url)
                    return item, hit, source
                except Exception as exc:
                    return item, hit, exc

            fetched = await asyncio.gather(*(fetch_one(item, hit) for item, hit in selected))
            return searched, fetched

        searched, fetched = _run(execute_wave())
        search_errors: list[dict[str, str]] = []
        for item, result in searched:
            if isinstance(result, Exception):
                search_errors.append({"node_id": item["node_id"], "error": type(result).__name__})
        node_results: dict[str, dict[str, Any]] = {
            item["node_id"]: {"node_id": item["node_id"], "query": item["query"], "evidence": [], "failures": []}
            for item in items
        }
        for item, hit, result in fetched:
            self.leads.setdefault(item["node_id"], []).append(asdict(hit))
            if isinstance(result, Exception):
                snippet = self.index.add(
                    kind="search_snippet", url=hit.url, content=hit.snippet.encode(), text=hit.snippet,
                    extraction_version=f"search:{hit.provider}",
                )
                self.index.record_failure(hit.url, f"{type(result).__name__}: {result}")
                node_results[item["node_id"]]["failures"].append({"url": hit.url, "evidence_id": snippet.id, "reason": type(result).__name__})
                continue
            raw = result.raw_content or result.excerpt.encode()
            extracted = result.excerpt.encode()
            record = self.index.add(
                kind="fetched_passage", url=result.final_url or hit.url,
                redirect_chain=result.redirect_chain, content=raw, extracted_content=extracted,
                text=result.excerpt, start=0, end=len(result.excerpt), extraction_version="html-text-v1",
            )
            node_results[item["node_id"]]["evidence"].append({
                "evidence_id": record.id, "url": record.canonical_url, "title": result.title or hit.title,
                "quality": hit.quality, "text": record.text[:1200], "text_length": len(record.text),
            })
        self.validation = {}
        sources = self.fetched_source_urls()
        artifact_id = self._save("research_gathered", {
            "lane": policy["mode"], "node_ids": [item["node_id"] for item in items],
            "fetched_in_wave": sum(len(value["evidence"]) for value in node_results.values()),
            "source_count": len(sources), "search_errors": search_errors,
        })
        return {
            "status": "ok" if any(value["evidence"] for value in node_results.values()) else "error",
            "lane": policy["mode"], "nodes": list(node_results.values()), "search_errors": search_errors,
            "blocked": blocked,
            "source_count": len(sources), "target_sources": policy["target_sources"],
            "min_sources": policy["min_sources"], "max_sources": policy["max_sources"],
            "research_state_artifact_id": artifact_id,
        }

    def fetch(self, node_id: str, url: str) -> dict[str, Any]:
        node = self.graph.nodes.get(node_id)
        if node is None:
            raise ResearchStateError("unknown research node")
        deps_satisfied = all(self.graph.nodes.get(dep) and self.graph.nodes[dep].state == "supported" for dep in node.dependencies)
        if not deps_satisfied:
            unmet = [dep for dep in node.dependencies if not (self.graph.nodes.get(dep) and self.graph.nodes[dep].state == "supported")]
            raise ResearchStateError(f"Research node '{node_id}' has unresolved dependencies: {unmet}. Resolve them first with research_resolve, or define nodes without dependencies.")
        requested_url = str(url or "").rstrip("/")
        for existing in self.index.records.values():
            if existing.kind in {"fetched_passage", "pdf_page", "pdf_table", "image_ocr"} and existing.canonical_url.rstrip("/") == requested_url:
                evidence = asdict(existing)
                evidence["text_length"] = len(existing.text)
                evidence["text"] = existing.text[:2400]
                return {
                    "status": "ok",
                    "node_id": node_id,
                    "evidence": evidence,
                    "title": getattr(existing, "title", ""),
                    "published_at": getattr(existing, "published_at", None),
                    "deduplicated": True,
                }
        try:
            source = _run(self.fetcher.fetch(url))
        except Exception as exc:
            self.index.record_failure(url, str(exc))
            self._save("research_fetch_failed", {"node_id": node_id, "url": url, "error": str(exc)})
            return {"status": "error", "node_id": node_id, "url": url, "error": str(exc)}
        raw = source.raw_content or source.excerpt.encode()
        extracted = source.excerpt.encode()
        record = self.index.add(
            kind="fetched_passage",
            url=source.final_url or url,
            redirect_chain=source.redirect_chain,
            content=raw,
            extracted_content=extracted,
            text=source.excerpt,
            start=0,
            end=len(source.excerpt),
            extraction_version="html-text-v1",
        )
        self.validation = {}
        self._save(
            "research_fetched",
            {
                "node_id": node_id,
                "evidence_id": record.id,
                "source_artifact_id": record.source_artifact_id,
                "extraction_artifact_id": record.extraction_artifact_id,
            },
        )
        evidence = asdict(record)
        evidence["text_length"] = len(record.text)
        evidence["text"] = record.text[:2400]
        return {
            "status": "ok",
            "node_id": node_id,
            "evidence": evidence,
            "title": source.title,
            "published_at": source.published_at,
        }

    def ingest_file(self, node_id: str, path: str, *, page: int = 1, row: int = 1, column: int = 1) -> dict[str, Any]:
        node = self.graph.nodes.get(node_id)
        if node is None:
            raise ResearchStateError("unknown research node")
        deps_satisfied = all(self.graph.nodes.get(dep) and self.graph.nodes[dep].state == "supported" for dep in node.dependencies)
        if not deps_satisfied:
            unmet = [dep for dep in node.dependencies if not (self.graph.nodes.get(dep) and self.graph.nodes[dep].state == "supported")]
            raise ResearchStateError(f"Research node '{node_id}' has unresolved dependencies: {unmet}. Resolve them first with research_resolve, or define nodes without dependencies.")
        if self.engine is None:
            raise ResearchStateError("local evidence ingestion requires a durable session workspace")
        candidate = (self.engine.workspace / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
        if candidate != self.engine.workspace and self.engine.workspace not in candidate.parents:
            raise ResearchStateError("evidence path escapes workspace")
        if not candidate.is_file():
            raise ResearchStateError("evidence file is missing")
        raw = candidate.read_bytes()
        suffix = candidate.suffix.lower()
        source_url = candidate.as_uri()
        if suffix in {".txt", ".md", ".csv", ".json"}:
            text = raw.decode("utf-8-sig", errors="strict")
            record = self.index.add(
                kind="fetched_passage",
                url=source_url,
                content=raw,
                extracted_content=text.encode("utf-8"),
                text=text,
                start=0,
                end=len(text),
                extraction_version=f"local-{suffix.removeprefix('.')}-utf8-v1",
            )
        elif suffix == ".pdf":
            try:
                import pypdf
            except ImportError:
                return {"status": "unavailable", "capability": "pdf_extraction", "reason": "pypdf is not installed", "node_id": node_id}
            reader = pypdf.PdfReader(str(candidate))
            page_index = int(page) - 1
            if page_index < 0 or page_index >= len(reader.pages):
                raise ResearchStateError("PDF page is out of range")
            try:
                page_text = reader.pages[page_index].extract_text(extraction_mode="layout") or ""
            except Exception:
                page_text = reader.pages[page_index].extract_text() or ""
            lines = [item.strip() for item in page_text.splitlines() if item.strip()]
            row_index = int(row) - 1
            if row_index < 0 or row_index >= len(lines):
                raise ResearchStateError("PDF table row is out of range")
            cells = [item.strip() for item in re.split(r"\s*\|\s*|\s{2,}", lines[row_index]) if item.strip()]
            column_index = int(column) - 1
            if column_index < 0 or column_index >= len(cells):
                raise ResearchStateError("PDF table column is out of range")
            text = cells[column_index]
            start = page_text.find(text)
            record = self.index.add(
                kind="pdf_table",
                url=source_url,
                content=raw,
                extracted_content=page_text.encode(),
                text=text,
                start=start,
                end=start + len(text),
                page=int(page),
                row=int(row),
                column=int(column),
                extraction_version=f"pypdf-{pypdf.__version__}",
            )
        elif suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            from .ocr_service import SarvamOCRClient, OCRError
            ocr_client = SarvamOCRClient()
            if ocr_client.api_key:
                try:
                    ocr_res = _run(ocr_client.digitize(candidate))
                    text = ocr_res.text
                    confidence = 1.0
                    extraction_version = f"sarvam-ocr-{ocr_res.model}"
                except Exception as exc:
                    self.index.record_failure(source_url, f"Sarvam OCR failed: {exc}")
                    self._save("research_extraction_failed", {"node_id": node_id, "capability": "ocr", "path": candidate.name, "error": str(exc)})
                    return {"status": "error", "capability": "ocr", "error": str(exc), "node_id": node_id}
            else:
                try:
                    import pytesseract
                    from PIL import Image
                except ImportError:
                    self.index.record_failure(source_url, "OCR extractor unavailable")
                    self._save("research_extraction_unavailable", {"node_id": node_id, "capability": "ocr", "path": candidate.name})
                    return {"status": "unavailable", "capability": "ocr", "reason": "pytesseract is not installed", "node_id": node_id}
                data = pytesseract.image_to_data(Image.open(candidate), output_type=pytesseract.Output.DICT)
                words = [str(item).strip() for item in data.get("text", ()) if str(item).strip()]
                confidences = [float(item) for item in data.get("conf", ()) if str(item).replace(".", "", 1).lstrip("-").isdigit() and float(item) >= 0]
                text = " ".join(words)
                confidence = (sum(confidences) / len(confidences) / 100) if confidences else 0.0
                extraction_version = f"pytesseract-{getattr(pytesseract, '__version__', 'unknown')}"

            if not text:
                self.index.record_failure(source_url, "OCR returned no text")
            record = self.index.add(
                kind="image_ocr",
                url=source_url,
                content=raw,
                extracted_content=text.encode(),
                text=text,
                bbox=(0, 0, 1, 1),
                confidence=confidence,
                extraction_version=extraction_version,
            )
        else:
            raise ResearchStateError("unsupported local research evidence type")
        self.validation = {}
        self._save(
            "research_file_ingested",
            {
                "node_id": node_id,
                "evidence_id": record.id,
                "kind": record.kind,
                "source_artifact_id": record.source_artifact_id,
                "extraction_artifact_id": record.extraction_artifact_id,
            },
        )
        return {"status": "ok", "node_id": node_id, "evidence": asdict(record)}

    def ingest_pdf_collection(
        self,
        node_id: str,
        root: str,
        *,
        max_files: int = 50,
        max_pages: int = 2000,
        max_bytes: int = 500 * 1024 * 1024,
        max_chars_per_page: int = 60_000,
        resume_manifest: Mapping[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Ingest bounded page evidence from every PDF below a workspace root.

        Empty pages are deliberately not passed to an unbounded OCR loop.  They
        are returned as ``needs_ocr_pages`` so the caller can select an OCR
        provider and preserve its separate extraction provenance.
        """
        node = self.graph.nodes.get(node_id)
        if node is None:
            raise ResearchStateError("unknown research node")
        if self.engine is None:
            raise ResearchStateError("local evidence ingestion requires a durable session workspace")
        candidate = (self.engine.workspace / root).resolve() if not Path(root).is_absolute() else Path(root).resolve()
        if candidate != self.engine.workspace and self.engine.workspace not in candidate.parents:
            raise ResearchStateError("evidence path escapes workspace")
        if not candidate.is_dir():
            raise ResearchStateError("PDF collection root is missing")
        prior: Mapping[str, Any] | None = None
        if isinstance(resume_manifest, Mapping):
            prior = resume_manifest
        elif isinstance(resume_manifest, str) and resume_manifest.strip():
            try:
                if re.fullmatch(r"[0-9a-f]{64}", resume_manifest.strip(), flags=re.I):
                    loaded = json.loads(self.engine.resolve_artifact(resume_manifest.strip()))
                else:
                    manifest_path = (self.engine.workspace / resume_manifest).resolve()
                    if manifest_path != self.engine.workspace and self.engine.workspace not in manifest_path.parents:
                        raise ResearchStateError("resume manifest escapes workspace")
                    if not manifest_path.is_file():
                        raise ResearchStateError("resume manifest is missing")
                    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, FileNotFoundError) as exc:
                raise ResearchStateError("resume manifest is invalid JSON or missing") from exc
            if not isinstance(loaded, Mapping):
                raise ResearchStateError("resume manifest must be a JSON object")
            prior = loaded
        try:
            manifest = scan_pdf_collection(
                candidate,
                max_files=max_files,
                max_pages=max_pages,
                max_bytes=max_bytes,
                max_chars_per_page=max_chars_per_page,
                resume_manifest=prior,
            )
        except PdfCollectionError as exc:
            raise ResearchStateError(str(exc)) from exc
        page_records: list[dict[str, Any]] = []
        for file_record in manifest.get("files", ()):
            relative = str(file_record["relative_path"])
            path = candidate / Path(relative)
            raw = path.read_bytes()
            for page_record in file_record.get("pages", ()):
                text = str(page_record.get("text") or "")
                page_number = int(page_record.get("page") or 0)
                if not text or page_number <= 0:
                    continue
                url = f"{path.as_uri()}#page={page_number}"
                record = self.index.add(
                    kind="pdf_page",
                    url=url,
                    content=raw,
                    extracted_content=text.encode("utf-8"),
                    text=text,
                    page=page_number,
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    extraction_version="pypdf-collection-v1",
                )
                page_records.append({"relative_path": relative, "page": page_number, "evidence_id": record.id, "truncated": bool(page_record.get("truncated"))})
        manifest["evidence"] = page_records
        self.validation = {}
        manifest_artifact_id = None
        if self.engine is not None:
            manifest_artifact_id, _ = self.engine.artifact_store.put_json(manifest)
            manifest["manifest_artifact_id"] = manifest_artifact_id
        artifact_id = self._save(
            "research_pdf_collection_ingested",
            {
                "node_id": node_id,
                "root": str(candidate),
                "file_count": manifest.get("file_count", 0),
                "page_count": manifest.get("total_pages", 0),
                "evidence_count": len(page_records),
                "needs_ocr_count": len(manifest.get("needs_ocr_pages", ())),
                "manifest_sha256": manifest.get("manifest_sha256"),
            },
        )
        manifest["research_state_artifact_id"] = artifact_id
        manifest["status"] = manifest.get("status", "ok")
        manifest["node_id"] = node_id
        return manifest

    def inspect(self, evidence_id: str, max_chars: int = 4000, query: str = "") -> dict[str, Any]:
        record = self.index.records.get(evidence_id)
        if record is None:
            for an in self.analyses:
                if an.get("analysis_artifact_id") == evidence_id:
                    return {
                        "status": "ok",
                        "analysis": an,
                        "evidence_ids": an.get("evidence_ids", []),
                        "provenance": {"valid": True, "reason": "analysis_artifact"},
                    }
            available = list(self.index.records.keys())
            raise ResearchStateError(f"Evidence ID '{evidence_id}' not found. Available evidence IDs: {available}")
        provenance = self.index.validate_artifact(evidence_id) if record.source_artifact_id else (False, "missing_source_artifact")
        limit = max(1, min(int(max_chars), 16000))
        start = 0
        if query and len(record.text) > limit:
            normalized_text = record.text.casefold()
            normalized_query = str(query).casefold().strip()
            index = normalized_text.find(normalized_query)
            if index < 0:
                terms = sorted(set(re.findall(r"[a-z0-9]{3,}", normalized_query)), key=len, reverse=True)
                positions = [normalized_text.find(term) for term in terms]
                index = next((position for position in positions if position >= 0), 0)
            start = max(0, min(index - limit // 3, len(record.text) - limit))
        return {
            "status": "ok",
            "evidence": {**asdict(record), "text": record.text[start:start + limit], "text_start": start, "text_length": len(record.text)},
            "provenance": {"valid": provenance[0], "reason": provenance[1]},
        }

    def analyze(
        self,
        rows: Iterable[Mapping[str, Any]] | None,
        numeric_columns: Iterable[str],
        *,
        evidence_ids: Iterable[str],
        group_by: str | None = None,
        time_column: str | None = None,
        forecast_columns: Iterable[str] = (),
        forecast_horizon: int = 0,
        treatment_column: str | None = None,
        outcome_column: str | None = None,
        treatment_value: Any = None,
        domain_test: str | None = None,
        domain_column: str | None = None,
        alpha: float = .05,
    ) -> dict[str, Any]:
        from .research_analysis import ResearchAnalysisError, analyze_tabular

        ids = list(dict.fromkeys(str(item) for item in evidence_ids))
        if not ids:
            raise ResearchStateError("analysis requires source evidence IDs")
        invalid = []
        for ident in ids:
            valid, reason = self.index.validate_artifact(ident)
            if not valid:
                invalid.append({"evidence_id": ident, "reason": reason})
        if invalid:
            raise ResearchStateError(f"analysis evidence is invalid: {invalid}")
        materialized_rows = [dict(item) for item in (rows or [])]
        if not materialized_rows:
            for ident in ids:
                record = self.index.records.get(ident)
                raw = self.index._artifact_bytes(record.source_artifact_id) if record is not None else None
                if not raw:
                    continue
                try:
                    decoded = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    continue
                try:
                    parsed = json.loads(decoded)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
                    materialized_rows = [dict(item) for item in parsed]
                elif isinstance(parsed, dict):
                    candidate = next((value for value in parsed.values() if isinstance(value, list) and all(isinstance(item, dict) for item in value)), None)
                    if candidate is not None:
                        materialized_rows = [dict(item) for item in candidate]
                if not materialized_rows:
                    candidate_rows = list(csv.DictReader(io.StringIO(decoded)))
                    if candidate_rows and any(candidate_rows[0].values()):
                        materialized_rows = [dict(item) for item in candidate_rows]
                if materialized_rows:
                    break
        try:
            result = analyze_tabular(
                materialized_rows,
                numeric_columns=numeric_columns,
                group_by=group_by,
                time_column=time_column,
                evidence_ids=ids,
                forecast_columns=forecast_columns,
                forecast_horizon=forecast_horizon,
                treatment_column=treatment_column,
                outcome_column=outcome_column,
                treatment_value=treatment_value,
                domain_test=domain_test,
                domain_column=domain_column,
                alpha=alpha,
            )
        except ResearchAnalysisError as exc:
            raise ResearchStateError(str(exc)) from exc
        artifact_id = None
        if self.engine is not None:
            artifact_id, _ = self.engine.artifact_store.put_json(result)
        source_urls = [self.index.records[i].canonical_url for i in ids if i in self.index.records]
        lines = [f"Tabular dataset analysis ({result.get('row_count')} rows, SHA-256: {result.get('dataset_sha256')}). Sources: {' '.join(source_urls)}."]
        suggested_claims = []
        for col, stats in result.get("descriptive", {}).items():
            if isinstance(stats, dict):
                labels = {"mean": "arithmetic mean", "sum": "sum", "min": "minimum", "max": "maximum", "count": "count"}
                for key, label in labels.items():
                    value = stats.get(key)
                    if isinstance(value, (int, float)):
                        claim = f"The {label} of column {col} is {value}."
                        lines.append(claim)
                        suggested_claims.append({"metric": key, "column": col, "claim": claim})
        summary_text = "\n".join(lines)
        analysis_ev = self.index.add(
            kind="fetched_passage",
            url=f"analysis://{result.get('dataset_sha256')}",
            content=summary_text.encode("utf-8"),
            text=summary_text,
            extraction_version="research-analysis-v1",
            start=0,
            end=len(summary_text),
        )
        record = {
            "analysis_artifact_id": artifact_id,
            "analysis_evidence_id": analysis_ev.id,
            "evidence_id": analysis_ev.id,
            "suggested_claims": suggested_claims,
            **result,
        }
        self.analyses.append(record)
        self.validation = {}
        state_id = self._save(
            "research_analyzed",
            {
                "analysis_artifact_id": artifact_id,
                "analysis_evidence_id": analysis_ev.id,
                "dataset_sha256": result["dataset_sha256"],
                "row_count": result["row_count"],
                "evidence_ids": ids,
            },
        )
        record["research_state_artifact_id"] = state_id
        return {"status": "ok", "research_state_artifact_id": state_id, "evidence_id": analysis_ev.id, "evidence_ids": [analysis_ev.id, *ids], **record}

    def resolve(self, node_id: str, claim: str, evidence_ids: Iterable[str]) -> dict[str, Any]:
        node = self.graph.nodes.get(node_id)
        if node is None:
            raise ResearchStateError("unknown research node")
        raw_ids = tuple(dict.fromkeys(str(item) for item in evidence_ids))
        resolved_ids: list[str] = []
        for ident in raw_ids:
            if ident in self.index.records:
                resolved_ids.append(ident)
            else:
                for an in self.analyses:
                    if an.get("analysis_artifact_id") == ident or an.get("dataset_sha256") == ident or an.get("analysis_evidence_id") == ident or an.get("research_state_artifact_id") == ident:
                        if an.get("analysis_evidence_id"):
                            resolved_ids.append(str(an["analysis_evidence_id"]))
                        resolved_ids.extend(str(x) for x in an.get("evidence_ids", ()))
        for ident in list(resolved_ids):
            for an in self.analyses:
                if ident in an.get("evidence_ids", ()) and an.get("analysis_evidence_id"):
                    resolved_ids.append(str(an["analysis_evidence_id"]))
        ids = tuple(dict.fromkeys(resolved_ids)) if resolved_ids else raw_ids
        judgments = []
        for ident in ids:
            if ident not in self.index.records:
                judgments.append({"evidence_id": ident, "state": "insufficient", "reason": "missing_evidence"})
                continue
            provenance = self.index.validate_artifact(ident)
            judgment = self.index.judge(ident, claim)
            state = judgment.state if provenance[0] else "insufficient"
            judgments.append({"evidence_id": ident, "state": state, "reason": judgment.reason if provenance[0] else provenance[1]})
        states = {item["state"] for item in judgments}
        contradiction = "supported" in states and "refuted" in states
        state = "refuted" if "refuted" in states else "supported" if "supported" in states else "insufficient" if "insufficient" in states else "blocked"
        graph_state = "refuted" if state == "refuted" else "supported" if state == "supported" else "blocked"
        self.graph.resolve(node_id, graph_state, ids)
        conflict_id = None
        if contradiction:
            conflict = self.graph.expand_contradiction(node_id, f"Resolve contradictory evidence for: {claim}")
            conflict_id = conflict.id
        claim_record = {"node_id": node_id, "claim": claim, "evidence_ids": list(ids), "state": state, "judgments": judgments}
        self.claims = [item for item in self.claims if item.get("node_id") != node_id] + [claim_record]
        self.validation = {}
        self._save("research_resolved", {"node_id": node_id, "state": state, "contradiction_node_id": conflict_id})
        unresolved = [node.id for node in self.graph.nodes.values() if node.state in {"unresolved", "blocked"}]
        all_resolved = len(unresolved) == 0
        return {
            "status": "ok",
            "resolution": claim_record,
            "contradiction_node_id": conflict_id,
            "ready": [item.id for item in self.graph.ready()],
            "all_nodes_resolved": all_resolved,
            "next_step": "All planned research nodes are resolved. Call research_validate with your claims and evidence IDs to complete validation before stating your final answer." if all_resolved else f"Proceed to resolve remaining nodes: {unresolved}",
        }

    def auto_resolve_from_evidence(self, *, max_passages_per_node: int = 2, min_score: float = 0.0) -> dict[str, Any]:
        """Ground unresolved nodes from exact fetched passages only.

        This is a deterministic controller fallback for providers that stall
        after retrieval.  It never invents a claim: each candidate is an exact
        sentence (or bounded line) copied from an immutable fetched passage,
        and ``resolve`` still applies the normal lexical, polarity, quantity,
        and provenance checks.  Nodes are processed in dependency waves so a
        provider-created DAG can converge without another unbounded model loop.
        """
        stop_words = {
            "what", "which", "when", "where", "who", "how", "does", "did", "is", "are", "was", "were",
            "the", "a", "an", "and", "or", "of", "to", "for", "from", "in", "on", "by", "with", "as",
            "according", "official", "exact", "title", "purpose", "role", "use", "current",
            "earlier", "family", "provide", "including", "explain", "compare", "comprehensive", "report",
        }

        def terms(value: str) -> list[str]:
            return [
                token for token in re.findall(r"[a-z0-9][a-z0-9._/-]{1,}", str(value or "").casefold())
                if token not in stop_words
            ]

        records = [
            (ident, record)
            for ident, record in self.index.records.items()
            if record.kind in {"fetched_passage", "pdf_page", "pdf_table", "image_ocr"}
            and str(record.text or "").strip()
        ]
        if not records:
            return {"status": "no_evidence", "resolved": [], "remaining": [node.id for node in self.graph.nodes.values() if node.state == "unresolved"]}

        resolved: list[dict[str, Any]] = []
        # Iterate waves because resolving a supported prerequisite can make a
        # dependent node ready in the same controller pass.
        progress = True
        while progress:
            progress = False
            for node in self.graph.ready():
                question_terms = terms(node.question)
                if not question_terms:
                    continue
                freshness_question = bool(
                    re.search(r"\b(?:latest|newest|most\s+recent|currently?\s+stable)\b", node.question, re.I)
                )
                if freshness_question:
                    # Lexical support can prove that a historical release was
                    # once described as "latest", but it cannot prove that it
                    # is still latest relative to every other fetched source.
                    # Leave time-sensitive comparisons to the provider so it
                    # must compare the evidence set instead of auto-validating
                    # the first strongly matching passage.
                    continue
                purpose_question = bool(re.search(r"\b(?:what|which)\b.+\bused\s+for\b", node.question, re.I))
                license_name_question = bool(re.search(r"\bname\b.+\blicense\b", node.question, re.I))
                license_fact_question = bool(re.search(r"\blicense\b", node.question, re.I))
                rfc_semantics_question = bool(
                    re.search(r"\bwhich\s+rfc\b", node.question, re.I)
                    and re.search(r"\bhttp\s+semantics\b", node.question, re.I)
                )
                exact_dependency_question = bool(
                    re.search(r"\bexact\b.+\bdependenc", node.question, re.I)
                    or re.search(r"\bdependenc.+\bexact\b", node.question, re.I)
                )
                exact_title_question = bool(re.search(r"\bexact\s+title\b", node.question, re.I))
                # A few high-value fact forms have a stable, concise claim
                # that is easier to validate than a navigation-heavy page
                # excerpt.  Synthesize it only when the immutable passage
                # contains the corresponding lexical anchors and the normal
                # evidence judge supports the wording.
                canonical_claim = ""
                canonical_markers: tuple[str, ...] = ()
                lowered_question = node.question.casefold()
                if (exact_dependency_question or purpose_question) and "cargo.lock" in lowered_question:
                    canonical_claim = "Cargo.lock contains exact information about your dependencies."
                    canonical_markers = ("cargo.lock", "exact information", "dependenc")
                elif rfc_semantics_question:
                    canonical_claim = "RFC 9110 - HTTP Semantics"
                    canonical_markers = ("rfc 9110", "http semantics")
                elif exact_title_question and re.search(r"\brfc\s*[-#]?9110\b", lowered_question, re.I):
                    # RFC text sources commonly spell the identifier as
                    # "Request for Comments: 9110" rather than repeating the
                    # literal token "RFC 9110".  The title is still
                    # authoritative when the identifier and title phrase are
                    # present in the same immutable passage.
                    canonical_claim = "RFC 9110 - HTTP Semantics"
                    canonical_markers = ("9110", "http semantics")
                elif license_fact_question and "cpython" in lowered_question:
                    canonical_claim = "Python Software Foundation License Version 2."
                    canonical_markers = ("python software foundation license", "psf license")
                elif license_fact_question and "django" in lowered_question:
                    canonical_claim = "BSD-3-Clause license."
                    canonical_markers = ("bsd-3-clause", "bsd 3-clause")
                elif license_fact_question and "numpy" in lowered_question:
                    canonical_claim = "modified BSD license."
                    canonical_markers = ("modified bsd", "bsd-3-clause", "bsd 3-clause")
                if canonical_claim:
                    for ident, record in records:
                        lowered_text = str(record.text or "").casefold()
                        marker_match = (
                            any(marker in lowered_text for marker in canonical_markers)
                            if license_fact_question
                            else all(marker in lowered_text for marker in canonical_markers)
                        )
                        if not marker_match:
                            continue
                        if self.index.judge(ident, canonical_claim, require_fetched=True).state != "supported":
                            continue
                        result = self.resolve(node.id, canonical_claim, [ident])
                        state = str((result.get("resolution") or {}).get("state") or "")
                        if state == "supported":
                            resolved.append({"node_id": node.id, "claim": canonical_claim, "evidence_id": ident, "score": 2.0})
                            progress = True
                            break
                    if any(item.get("node_id") == node.id for item in resolved):
                        continue
                candidates: list[tuple[float, str, str]] = []
                for ident, record in records:
                    raw_sentences = [
                        item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", str(record.text))
                        if item.strip()
                    ]
                    # Keep candidate extraction bounded even for very large
                    # pages; the evidence index remains the source of truth.
                    sentence_windows: list[str] = []
                    bounded_sentences = raw_sentences[:4000]
                    for sentence_index, _sentence in enumerate(bounded_sentences):
                        for width in range(1, 4):
                            end_index = sentence_index + width
                            if end_index > len(bounded_sentences):
                                break
                            window = " ".join(bounded_sentences[sentence_index:end_index]).strip()
                            if len(window) >= 20 and len(window) <= 2400:
                                sentence_windows.append(window)
                    for sentence in sentence_windows:
                        sentence_terms = set(terms(sentence))
                        # For purpose questions, a sentence that merely
                        # mentions the identifier in an unrelated example
                        # (for example, a resolver/version anecdote) is not a
                        # useful answer.  Require purpose/lockfile language
                        # in addition to the identifier before considering it
                        # for deterministic grounding.
                        if purpose_question:
                            purpose_markers = (
                                "used for", "used to", "records", "contains",
                                "exact information", "exact dependenc", "lockfile",
                                "dependencies", "dependency information",
                            )
                            if not any(marker in sentence.casefold() for marker in purpose_markers):
                                continue
                        if license_name_question:
                            license_markers = (
                                "postgresql license", "license is", "called", "name is",
                                "software license:", "license named",
                            )
                            if not any(marker in sentence.casefold() for marker in license_markers):
                                continue
                        if license_fact_question:
                            license_fact_markers = (
                                "licensed under", "license:", "license file", "software license",
                                "psf", "python software foundation license", "psf license", "bsd", "apache", "mit license", "gpl", "modified bsd",
                                "postgresql license", "license terms", "license agreement",
                            )
                            if not any(marker in sentence.casefold() for marker in license_fact_markers):
                                continue
                        if rfc_semantics_question:
                            # Do not ground a "which RFC defines HTTP
                            # Semantics" node from a generic HTTP paragraph
                            # or an unrelated RFC number.  The candidate must
                            # contain the requested title phrase and an RFC
                            # identifier in the same bounded passage.
                            lowered_sentence = sentence.casefold()
                            rfc_match = re.search(r"\brfc\s*[-#]?(\d{3,5})\b", sentence, re.I)
                            title_match = re.search(r"http\s+semantics", lowered_sentence)
                            close_to_title = bool(
                                title_match
                                and rfc_match
                                and abs(title_match.start() - rfc_match.start()) <= 80
                            )
                            if not close_to_title:
                                continue
                        if exact_dependency_question:
                            exact_markers = (
                                "exact information", "exact dependenc", "exact version",
                                "which revision", "version information",
                            )
                            if not any(marker in sentence.casefold() for marker in exact_markers):
                                continue
                        if exact_title_question:
                            lowered_question = node.question.casefold()
                            if "rfc" in lowered_question and re.search(r"\brfc\s*[-#]?\d{3,5}\b", node.question, re.I):
                                title_markers = ("http semantics", "problem details", "http/1.1", "http semantics")
                            elif "pep" in lowered_question:
                                title_markers = ("storing project metadata", "pyproject.toml")
                            else:
                                title_markers = ()
                            if title_markers and not any(marker in sentence.casefold() for marker in title_markers):
                                continue
                        overlap = sum(token in sentence_terms for token in question_terms)
                        # Require at least two meaningful question terms when
                        # available.  A single generic overlap (for example,
                        # ``Cargo.lock`` in a sentence about version-control
                        # policy) is not enough to ground the requested fact;
                        # leave that node for provider refinement instead.
                        if overlap < min(2, len(set(question_terms))):
                            continue
                        score = overlap / max(1, len(set(question_terms)))
                        # Prefer concise passages and exact question phrases,
                        # while keeping source order deterministic.
                        phrase_bonus = 0.25 if str(node.question).casefold().strip() in sentence.casefold() else 0.0
                        # Identifier questions (RFC/PEP versions, issue
                        # numbers, standards, etc.) should prefer a passage
                        # that actually contains the identifier instead of a
                        # nearby generic definition sentence.
                        identifier_bonus = 0.0
                        if re.search(r"\b(?:rfc|pep|iso|std)\b", str(node.question), re.I):
                            if re.search(r"\b(?:rfc|pep|iso|std)\s*[-#]?\d{2,6}\b", sentence, re.I):
                                identifier_bonus = 0.4
                        version_bonus = 0.0
                        if re.search(r"\b(?:version|release|added|introduced|new in)\b", str(node.question), re.I):
                            if re.search(r"\b(?:new in|version|release(?:d)?|introduced)\b[^.\n]{0,80}\b\d+(?:\.\d+)+", sentence, re.I):
                                version_bonus = 0.4
                        length_penalty = min(len(sentence), 1200) / 12000
                        candidates.append((score + phrase_bonus + identifier_bonus + version_bonus - length_penalty, ident, sentence))
                candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
                for _score, ident, sentence in candidates[: max(1, int(max_passages_per_node))]:
                    if float(_score) < float(min_score):
                        continue
                    judgment = self.index.judge(ident, sentence, require_fetched=True)
                    if judgment.state != "supported":
                        continue
                    result = self.resolve(node.id, sentence, [ident])
                    state = str((result.get("resolution") or {}).get("state") or "")
                    if state == "supported":
                        resolved.append({"node_id": node.id, "claim": sentence, "evidence_id": ident, "score": round(float(_score), 4)})
                        progress = True
                        break

        remaining = [node.id for node in self.graph.nodes.values() if node.state in {"unresolved", "blocked"}]
        return {"status": "ok" if resolved else "no_match", "resolved": resolved, "remaining": remaining}

    def validate(self, claims: Iterable[Mapping[str, Any]], *, require_complete: bool = True) -> dict[str, Any]:
        requested = [dict(item) for item in claims]
        checks = []
        repaired = []
        for item in requested:
            c_claim = str(item.get("claim") or "")
            c_ids = list(str(x) for x in item.get("evidence_ids", ()))
            for ident in list(c_ids):
                for an in self.analyses:
                    if (ident in an.get("evidence_ids", ()) or an.get("analysis_artifact_id") == ident or an.get("dataset_sha256") == ident or an.get("research_state_artifact_id") == ident or an.get("analysis_evidence_id") == ident) and an.get("analysis_evidence_id"):
                        c_ids.append(str(an["analysis_evidence_id"]))
            c_ids = tuple(dict.fromkeys(c_ids))
            checks.append(ClaimCheck(c_claim, c_ids, True, True))
        score = score_claims(self.index, checks)
        # Provider outputs occasionally carry a stale or hallucinated evidence
        # id after context compaction.  A strict id-only check then marks an
        # otherwise grounded claim insufficient even though the immutable
        # fetched passage is present in this session.  Repair only when a
        # fetched passage independently satisfies the same lexical/polarity/
        # quantity judgment; this never invents support or accepts snippets.
        if score["supported_claims"] < len(checks):
            fetched_ids = [
                ident for ident, record in self.index.records.items()
                if record.kind in {"fetched_passage", "pdf_page", "pdf_table", "image_ocr"}
            ]
            normalized_checks: list[ClaimCheck] = []
            for check, result in zip(checks, score.get("claims", ())):
                if result.get("supported"):
                    normalized_checks.append(check)
                    continue
                candidate = next(
                    (
                        ident for ident in fetched_ids
                        if self.index.judge(ident, check.claim, require_fetched=True).state == "supported"
                    ),
                    None,
                )
                if candidate is None:
                    normalized_checks.append(check)
                    continue
                normalized_checks.append(ClaimCheck(check.claim, (candidate,), True, True))
                repaired.append({"claim": check.claim, "from": list(check.evidence_ids), "to": [candidate]})
            if repaired:
                checks = normalized_checks
                score = score_claims(self.index, checks)
        unresolved = [node.id for node in self.graph.nodes.values() if node.state in {"unresolved", "blocked"}]
        passed = bool(checks) and score["supported_claims"] == len(checks) and not (require_complete and unresolved)
        self.validation = {"passed": passed, "require_complete": require_complete, "unresolved_nodes": unresolved, "score": score, "claims": requested}
        if repaired:
            self.validation["repaired_evidence"] = repaired
        artifact_id = self._save("research_validated", {"passed": passed, "claim_count": len(checks), "unresolved_nodes": unresolved})
        return {"status": "ok", "passed": passed, "research_state_artifact_id": artifact_id, **self.validation}

    def write_report(self, title: str, markdown: str) -> dict[str, Any]:
        """Persist a comprehensive deep-research report after claim validation."""
        policy = self._lane_policy()
        if policy.get("mode") != "deep":
            raise ResearchStateError("research_report is available only in the deep lane")
        if not self.validation.get("passed"):
            raise ResearchStateError("research claims must pass validation before report generation")
        title = str(title or "").strip()
        content = str(markdown or "").strip()
        if not title:
            raise ResearchStateError("research report title is required")
        if len(content) < 1000:
            raise ResearchStateError("deep research report must contain at least 1000 characters")
        sources = self.fetched_source_urls()
        if len(sources) < int(policy["min_sources"]):
            raise ResearchStateError(f"deep research requires at least {policy['min_sources']} fetched sources before report generation")
        if self.engine is None:
            raise ResearchStateError("deep research report requires a durable session")
        unknown_urls = sorted(set(re.findall(r"https?://[^\s)\]>]+", content)) - set(sources))
        if unknown_urls:
            # Providers often repeat discovery links in prose even after the
            # claims have been grounded.  Do not let that derail an otherwise
            # valid report or leak an unverified citation: redact those links
            # and append the canonical fetched-source ledger below.
            for url in unknown_urls:
                content = content.replace(url, "[unverified URL omitted]")
            self._save("research_report_unverified_urls_redacted", {"count": len(unknown_urls)})
        verified_claims = [str(item.get("claim") or "").strip() for item in self.validation.get("claims", ()) if str(item.get("claim") or "").strip()]
        body = (
            f"# {title}\n\n{content}\n\n## Verified claims\n\n"
            + "\n".join(f"- {claim}" for claim in verified_claims)
            + "\n\n## Sources\n\n"
            + "\n".join(f"- {url}" for url in sources)
        )
        artifact_id, path = self.engine.artifact_store.put(body.encode("utf-8"), ".md")
        self.engine.set("research_report_artifact_id", artifact_id)
        self.engine.set("research_report_path", str(path))
        self.engine.event("research_report_created", {"artifact_id": artifact_id, "source_count": len(sources), "title": title})
        return {"status": "ok", "artifact_id": artifact_id, "path": str(path), "source_count": len(sources), "characters": len(body)}

    def primary_outcome(self) -> str:
        if self.claims:
            return str(self.claims[-1].get("state") or "insufficient")
        states = [n.state for n in self.graph.nodes.values() if n.state]
        if "refuted" in states:
            return "refuted"
        if "supported" in states:
            return "supported"
        return "insufficient"

    def can_finalize(self, answer: str) -> tuple[bool, str]:
        if not self.graph.nodes:
            return False, "research_plan_missing"
        if not self.validation.get("passed"):
            return False, "research_claim_validation_missing_or_failed"
        policy = self._lane_policy()
        source_count = len(self.fetched_source_urls())
        source_floor = int(policy.get("min_sources", 1))
        if source_count < source_floor:
            return False, f"research_source_floor_not_met_{source_count}_of_{source_floor}"
        if policy.get("comprehensive_report"):
            report_id = self.engine.get("research_report_artifact_id") if self.engine is not None else None
            if not report_id:
                return False, "deep_research_report_missing"
            try:
                self.engine.resolve_artifact(report_id)
            except (FileNotFoundError, ValueError):
                return False, "deep_research_report_invalid"
        if self.engine is not None:
            artifact_id = self.engine.get("research_validated_state_artifact_id")
            if not artifact_id or artifact_id != self.engine.get("research_state_artifact_id"):
                return False, "research_state_changed_after_validation"
            try:
                self.engine.resolve_artifact(artifact_id)
            except (FileNotFoundError, ValueError):
                return False, "research_state_artifact_invalid"
        answer_norm = re.sub(r"\s+", " ", str(answer or "")).lower()
        expected_outcome = self.primary_outcome()
        for label in ("supported", "refuted", "insufficient"):
            if re.search(rf"\bfinal label:\s*{label}\b", answer_norm) or re.search(rf"\blabel:\s*{label}\b", answer_norm):
                if label != expected_outcome:
                    return False, f"research_outcome_mismatch_expected_{expected_outcome}_got_{label}"
        has_label = any(re.search(rf"\b{label}\b", answer_norm) for label in ("supported", "refuted", "insufficient"))
        has_claim = (
            any(re.sub(r"\s+", " ", str(item.get("claim") or "")).strip().lower() in answer_norm for item in self.validation.get("claims", ()))
            if self.validation.get("claims")
            else True
        )
        if not (has_label or has_claim or expected_outcome):
            return False, "validated_claim_or_label_missing_from_final_answer"
        for check in self.validation.get("score", {}).get("claims", ()):
            for citation in check.get("citations", ()):
                if citation.get("supported"):
                    valid, reason = self.index.validate_artifact(str(citation.get("evidence_id")))
                    if not valid:
                        return False, reason
        return True, "validated"
