"""Session-scoped adaptive research used by the canonical agent tool loop."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evidence_index import EvidenceIndex
from .research_eval import ClaimCheck, score_claims
from .research_graph import ResearchGraph, ResearchNode
from .research_tools import FetchUrlTool, WebSearchTool


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

    def fetch(self, node_id: str, url: str) -> dict[str, Any]:
        node = self.graph.nodes.get(node_id)
        if node is None:
            raise ResearchStateError("unknown research node")
        deps_satisfied = all(self.graph.nodes.get(dep) and self.graph.nodes[dep].state == "supported" for dep in node.dependencies)
        if not deps_satisfied:
            unmet = [dep for dep in node.dependencies if not (self.graph.nodes.get(dep) and self.graph.nodes[dep].state == "supported")]
            raise ResearchStateError(f"Research node '{node_id}' has unresolved dependencies: {unmet}. Resolve them first with research_resolve, or define nodes without dependencies.")
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
        return {
            "status": "ok",
            "node_id": node_id,
            "evidence": asdict(record),
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

    def inspect(self, evidence_id: str, max_chars: int = 4000) -> dict[str, Any]:
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
        return {
            "status": "ok",
            "evidence": {**asdict(record), "text": record.text[: max(1, min(int(max_chars), 16000))]},
            "provenance": {"valid": provenance[0], "reason": provenance[1]},
        }

    def analyze(
        self,
        rows: Iterable[Mapping[str, Any]],
        numeric_columns: Iterable[str],
        *,
        evidence_ids: Iterable[str],
        group_by: str | None = None,
        time_column: str | None = None,
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
        try:
            result = analyze_tabular(rows, numeric_columns=numeric_columns, group_by=group_by, time_column=time_column, evidence_ids=ids)
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

    def validate(self, claims: Iterable[Mapping[str, Any]], *, require_complete: bool = True) -> dict[str, Any]:
        requested = [dict(item) for item in claims]
        checks = []
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
        unresolved = [node.id for node in self.graph.nodes.values() if node.state in {"unresolved", "blocked"}]
        passed = bool(checks) and score["supported_claims"] == len(checks) and not (require_complete and unresolved)
        self.validation = {"passed": passed, "require_complete": require_complete, "unresolved_nodes": unresolved, "score": score, "claims": requested}
        artifact_id = self._save("research_validated", {"passed": passed, "claim_count": len(checks), "unresolved_nodes": unresolved})
        return {"status": "ok", "passed": passed, "research_state_artifact_id": artifact_id, **self.validation}

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
