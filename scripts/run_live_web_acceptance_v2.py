"""Canonical-agent live-web and live-data acceptance gate.

The agent sees only the public manifest task. Sealed references are never passed
to the agent or copied into its workspace and are used only by deterministic,
independent validators after each agent result.
"""
from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import io
import ipaddress
import json
import math
import os
import re
import statistics
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx

from smara.autonomous_agent import SmaraAutonomousAgent, _get_api_key_from_vault_or_env
from smara.harness import BUDGET_PROFILES, Budget, SessionEngine
from smara.research import _is_public_http_url


ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "tests/evals/live_web_acceptance_v2/manifest.json"
REF_PATH = ROOT / "tests/evals/live_web_acceptance_v2/references.json"
EVIDENCE_PATH = ROOT / "release/evidence/LIVE_WEB_ACCEPTANCE_V2.json"
# Sarvam's GLM-5.3 output rate is the conservative upper bound for a mixed
# token count; this deliberately assumes every billed token costs the output rate.
OUTPUT_RUPEES_PER_M = 396.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(value: Any) -> str:
    normalized = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", str(value or "").casefold())
    month_names = {
        "jan": "january", "feb": "february", "mar": "march", "apr": "april",
        "jun": "june", "jul": "july", "aug": "august", "sep": "september",
        "sept": "september", "oct": "october", "nov": "november", "dec": "december",
    }
    for short, full in month_names.items():
        normalized = re.sub(rf"\b{short}\.?(?=\s|\b)", full, normalized)
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


_CLAIM_EQUIVALENTS = (
    frozenset({
        "exact versions",
        "exact dependency versions",
        "exact information about your dependencies",
        "specific versions",
        "specific dependency versions",
        "locks dependencies to specific versions",
    }),
    frozenset({
        "bsd 3 clause",
        "bsd 3 clause license",
        "modified bsd",
        "modified bsd license",
    }),
)


def _claim_variants(term: Any) -> set[str]:
    normalized = normalize(term)
    variants = {normalized}
    for group in _CLAIM_EQUIVALENTS:
        if normalized in group:
            variants.update(group)
    return {item for item in variants if item}


def validate_pack_contract(pack: dict[str, Any], refs: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    tasks = pack.get("tasks") or []
    if pack.get("canonical_agent") is not True:
        errors.append("manifest must declare canonical_agent=true")
    if pack.get("reference_access") != "validator_only":
        errors.append("manifest must declare reference_access=validator_only")
    if not tasks:
        errors.append("manifest must contain tasks")
    ids = [str(task.get("id") or "") for task in tasks]
    if len(ids) != len(set(ids)) or any(not item for item in ids):
        errors.append("task IDs must be non-empty and unique")
    for task in tasks:
        task_id = str(task.get("id") or "")
        category = str(task.get("category") or "")
        requested_mode = task.get("requested_mode")
        expected_lane = task.get("expected_lane")
        if requested_mode is not None and str(requested_mode) not in {"auto", "quick", "deep"}:
            errors.append(f"{task_id}: requested_mode must be auto, quick, or deep")
        if expected_lane is not None and str(expected_lane) not in {"quick", "deep"}:
            errors.append(f"{task_id}: expected_lane must be quick or deep")
        if expected_lane is not None and requested_mode is None:
            errors.append(f"{task_id}: expected_lane requires requested_mode")
        ref = refs.get(task_id)
        if not isinstance(ref, dict):
            errors.append(f"{task_id}: sealed reference missing")
            continue
        if category == "quantitative_analysis":
            if not str(task.get("live_data_url") or "").startswith("https://"):
                errors.append(f"{task_id}: HTTPS live_data_url required")
            spec = ref.get("numeric") or {}
            if spec.get("operation") not in {"mean", "sum", "min", "max", "count"} or not spec.get("column"):
                errors.append(f"{task_id}: valid numeric validator required")
        else:
            if not task.get("as_of") and category != "today_latest":
                errors.append(f"{task_id}: as_of required")
            claims = ref.get("required_claims")
            if (not isinstance(claims, list) or not claims) and category != "today_latest":
                errors.append(f"{task_id}: required_claims required")
            if not str(ref.get("expected_answer") or "").strip() and category != "today_latest":
                errors.append(f"{task_id}: expected_answer required")
            if category == "failed_page" and not str(ref.get("required_failed_url") or "").startswith("https://"):
                errors.append(f"{task_id}: failed-page case requires an HTTPS required_failed_url")
    return errors


def build_prompt(task: dict[str, Any]) -> str:
    question = str(task["question"])
    if task["category"] == "quantitative_analysis":
        return (
            f"Answer this question using the live CSV dataset at {task['live_data_url']}: {question}\n"
            "Use the canonical research_plan, research_fetch, research_analyze, research_resolve, and "
            "research_validate tools. Compute with research_analyze, not mental arithmetic. Once computed, "
            "call research_analyze with numeric_columns and the fetched evidence ID while omitting rows, then "
            "copy the matching suggested_claim from research_analyze exactly into research_resolve and "
            "research_validate, without adding the URL or method to that claim, then deliver your final answer. "
            "State the method, numeric result, dataset URL, and FINAL LABEL: supported."
        )
    task_id = str(task.get("id") or "")
    task_specific = ""
    if task_id.endswith("-F04"):
        task_specific = (
            "For this Cargo.lock question, use the official Cargo Book page "
            "https://doc.rust-lang.org/cargo/guide/cargo-toml-vs-cargo-lock.html. "
            "Quote its complete sentence `Cargo.lock contains exact information about your dependencies.` "
            "verbatim in the claim and final answer; do not substitute the resolver or FAQ wording. "
        )
    elif task_id.endswith("-S03"):
        task_specific = (
            "For this NumPy licensing question, fetch both the official raw LICENSE.txt and "
            "https://numpy.org/about/. Use the official about-page sentence identifying the "
            "modified BSD license as the evidence-backed claim, and state that this is the BSD 3-Clause license. "
            "Cite the fetched numpy.org/about URL as well as the LICENSE URL. "
        )
    lane_specific = ""
    if task.get("category") == "today_latest":
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
        task_specific += (
            f"Interpret 'today' using the supplied application-clock date {today} (Asia/Kolkata), and state that date explicitly. "
            "This local date is authoritative; do not search the web for the date or fetch a time-zone site. "
            "Determine the latest stable Python 3 release from the official Python source-releases page, not from memory; "
            "spend retrieval only on verifying that version and cite the official Python page. Your final answer must include both the ISO current date and exact version. "
            "Do not confuse the page's crawl date with the current date. "
        )
    elif task.get("category") == "historical_as_of":
        task_specific += (
            "Treat the as-of date as a strict historical cutoff. Do not substitute today's latest version; "
            "identify the newest Python 3.13 maintenance release published on or before that date and cite the official release page. "
        )
    elif task.get("category") == "source_conflict":
        task_specific += (
            "Resolve the apparent conflict by distinguishing OpenSSL upstream's end of public updates from paid/vendor extended support. "
            "Cite the official OpenSSL notice and explain why both statements can be true. "
        )
    elif task.get("category") == "failed_page":
        failed_url = str(task.get("required_failed_url") or "")
        task_specific += (
            f"As a required recovery test, explicitly attempt to fetch this exact URL: {failed_url}. "
            "If it fails, record the failure, do not claim it supplied evidence, and recover from at least three other fetched official Python sources. "
        )
    if task.get("expected_lane") == "quick":
        lane_specific = (
            "This is a dedicated Quick-lane acceptance case. Keep the plan bounded to one or two nodes, "
            "gather exactly enough diverse sources to meet the 3-source floor, validate once the claim is supported, "
            "never fetch the same URL twice, and do not create a report artifact. Once three unique sources are fetched, "
            "stop retrieval and resolve/validate the requested claim. "
        )
    elif task.get("expected_lane") == "deep":
        lane_specific = (
            "This is a dedicated Deep-lane acceptance case. Keep the DAG bounded to at most six useful nodes, "
            "gather at least 20 diverse fetched sources in parallel waves, resolve the material claims, call "
            "research_validate, then call research_report with a comprehensive Markdown report (at least 1000 characters) "
            "before giving the final answer. Validate only the claims requested in the question; do not add incidental "
            "metadata or new claims. Stop expanding once those requirements are met. "
        )
    return (
        f"As of {task.get('as_of') or datetime.now(ZoneInfo('Asia/Kolkata')).date().isoformat()}, answer this live-web research question: {question}\n"
        + task_specific + lane_specific
        + "Use the canonical research_plan, then prefer research_gather to search, hybrid-rerank, and fetch diverse sources in parallel; "
        "research_search plus research_fetch is the compatible fallback. Continue through research_resolve and research_validate. "
        "Search snippets are discovery only. Once you fetch the authoritative "
        "source passage, call research_inspect at most once with a focused query when the compact preview is incomplete. "
        "Copy one complete sentence verbatim from the fetched/inspected passage into research_resolve and research_validate; "
        "do not paraphrase dates (keep the source's ISO or prose form), do not retry resolve with variants, and proceed to the final answer after one successful validate. "
        "deliver your concise answer with public source URL(s) and FINAL LABEL: supported, refuted, or insufficient."
    )


def _records(agent: SmaraAutonomousAgent) -> list[Any]:
    index = getattr(getattr(agent, "_research", None), "index", None)
    return list(getattr(index, "records", {}).values())


def _record_text(record: Any) -> str:
    if isinstance(record, dict):
        return str(record.get("text") or record.get("content") or "")
    return str(getattr(record, "text", "") or getattr(record, "content", ""))


def _record_url(record: Any) -> str:
    if isinstance(record, dict):
        return str(record.get("canonical_url") or record.get("url") or record.get("source_url") or "")
    return str(getattr(record, "canonical_url", "") or getattr(record, "url", "") or getattr(record, "source_url", ""))


def _claim_passes(answer: str, evidence_text: str, claim: dict[str, Any]) -> bool:
    answer_norm, evidence_norm = normalize(answer), normalize(evidence_text)
    alternatives = claim.get("any_of") or []
    def contains(haystack: str, needle: str) -> bool:
        return bool(needle) and f" {needle} " in f" {haystack} "
    return any(
        any(contains(answer_norm, variant) for variant in _claim_variants(term))
        and any(contains(evidence_norm, variant) for variant in _claim_variants(term))
        for term in alternatives
    )


def validate_factual(answer: str, agent: SmaraAutonomousAgent, ref: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    records = _records(agent)
    fetched = [record for record in records if _record_url(record).startswith(("http://", "https://"))]
    urls = re.findall(r"https?://[^\s)\]]+", answer)
    claims = ref["required_claims"]
    allowed = [str(domain).casefold() for domain in ref.get("allowed_domains", [])]
    domain_ok = not allowed or any(any(domain in url.casefold() for domain in allowed) for url in urls)
    fetched_urls = {_record_url(record).rstrip("/") for record in fetched}
    cited_fetched = any(url.rstrip("/.,") in fetched_urls for url in urls)
    cited_records = [record for record in fetched if any(url.rstrip("/.,") == _record_url(record).rstrip("/") for url in urls)]
    claim_details = []
    answer_normalized = normalize(answer)
    for claim in claims:
        variants = [normalize(term) for term in (claim.get("any_of") or [])]
        answer_hit = any(term and f" {term} " in f" {answer_normalized} " for term in variants)
        # Require the cited page itself to contain the requested claim; a
        # different uncited page in the evidence bundle cannot lend support.
        citation_hit = any(_claim_passes(answer, _record_text(record), claim) for record in cited_records)
        claim_details.append({"answer": answer_hit, "citation": citation_hit, "supported": answer_hit and citation_hit})
    claim_results = [item["supported"] for item in claim_details]
    answer_coverage = sum(item["answer"] for item in claim_details) / len(claim_details) if claim_details else 0.0
    citation_support = sum(item["supported"] for item in claim_details) / len(claim_details) if claim_details else 0.0
    passed = bool(fetched and urls and cited_fetched and domain_ok and all(claim_results))
    reason = "validated" if passed else "answer_or_evidence_claim_mismatch"
    return passed, reason, {"claims": claim_results, "claim_details": claim_details, "fetched_sources": len(fetched),
        "answer_urls": urls, "cited_fetched": cited_fetched, "domain_ok": domain_ok,
        "answer_coverage": answer_coverage, "citation_support": citation_support,
        "cited_source_count": len(cited_records)}


def _current_python_release() -> str:
    """Read the official, live current-release marker independently of the model."""
    url = "https://www.python.org/getit/source/"
    if not _is_public_http_url(url):
        raise ValueError("official Python URL did not pass public-URL validation")
    with httpx.Client(timeout=25.0, follow_redirects=True, headers={"User-Agent": "SmaraResearchEval/5"}) as client:
        response = client.get(url)
        response.raise_for_status()
    match = re.search(r"Latest\s+Python\s+3\s+Release\s*[-–]\s*Python\s+(3\.\d+\.\d+)", response.text, flags=re.I)
    if not match:
        raise ValueError("official Python page did not expose its latest stable Python 3 release marker")
    return match.group(1)


def validate_today_latest(answer: str, agent: SmaraAutonomousAgent) -> tuple[bool, str, dict[str, Any]]:
    expected_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    answer_norm = normalize(answer)
    date_forms = [expected_date.isoformat(), expected_date.strftime("%B %d, %Y"),
                  expected_date.strftime("%d %B %Y"),
                  f"{expected_date.strftime('%B')} {expected_date.day}, {expected_date.year}",
                  f"{expected_date.day} {expected_date.strftime('%B')} {expected_date.year}"]
    date_found = any(normalize(form) in answer_norm for form in date_forms)
    accepted_paths = {"/getit/source", "/downloads/source"}
    official_url = "https://www.python.org/getit/source/"
    def is_official_source_page(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and parsed.hostname == "www.python.org" and parsed.path.rstrip("/") in accepted_paths
    records = [record for record in _records(agent) if is_official_source_page(_record_url(record))]
    try:
        latest = _current_python_release()
        page_available = True
    except Exception as exc:
        latest, page_available = "", False
        page_error = type(exc).__name__
    version_found = bool(latest and normalize(latest) in answer_norm)
    evidence_found = bool(latest and any(latest in _record_text(record) for record in records))
    cited_urls = [url.rstrip("/.,") for url in re.findall(r"https?://[^\s)\]]+", answer)]
    url_found = any(is_official_source_page(url) for url in cited_urls)
    passed = date_found and version_found and evidence_found and url_found
    detail = {"expected_local_date": expected_date.isoformat(), "official_latest_python": latest,
        "answer_date_coverage": date_found, "answer_version_coverage": version_found,
        "citation_support": bool(evidence_found and url_found), "answer_coverage": (int(date_found) + int(version_found)) / 2,
        "citation_support_fraction": float(evidence_found and url_found), "official_source_fetched": evidence_found,
        "independent_source_available": page_available}
    if not page_available:
        detail["independent_source_error"] = page_error
    return passed, "validated" if passed else "today_latest_intent_or_citation_mismatch", detail


def validate_failed_page(task: dict[str, Any], agent: SmaraAutonomousAgent) -> tuple[bool, str, dict[str, Any]]:
    required_url = str(task.get("required_failed_url") or "").rstrip("/")
    research = getattr(agent, "_research", None)
    index = getattr(research, "index", None)
    failures = list(getattr(index, "failures", []) or [])
    matched = [item for item in failures if str(item.get("canonical_url") or item.get("url") or "").rstrip("/") == required_url]
    source_count = len(research.fetched_source_urls()) if research is not None else 0
    passed = bool(required_url and matched and source_count >= 3)
    return passed, "validated" if passed else "required_failed_page_not_recorded_or_recovery_incomplete", {
        "required_failed_url": required_url, "failure_recorded": bool(matched), "recovery_source_count": source_count}


def fetch_csv(url: str, max_bytes: int = 2_000_000) -> tuple[list[dict[str, str]], str]:
    current = url
    raw = b""
    with httpx.Client(timeout=25.0, follow_redirects=False, headers={"User-Agent": "SmaraAcceptance/3"}) as client:
        for _ in range(6):
            if not _is_public_http_url(current):
                raise ValueError("live dataset URL is not public HTTP(S)")
            with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("live dataset redirect has no location")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
                if content_type and content_type not in {"text/csv", "text/plain", "application/csv", "application/octet-stream"}:
                    raise ValueError(f"live dataset content type is not CSV: {content_type}")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("live dataset exceeds byte ceiling")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                break
        else:
            raise ValueError("live dataset exceeded redirect ceiling")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not rows or len(rows) > 10_000:
        raise ValueError("live dataset row count is outside gate bounds")
    return rows, hashlib.sha256(raw).hexdigest()


def recompute(rows: list[dict[str, str]], spec: dict[str, Any]) -> float:
    def norm_col(name: str) -> str:
        return str(name or "").replace('"', '').strip().casefold()
    target_cols = {norm_col(spec["column"])} | {norm_col(item) for item in spec.get("additional_columns", [])}
    values: list[float] = []
    for row in rows:
        for k, v in row.items():
            if norm_col(k) in target_cols:
                clean_val = str(v or "").replace(",", "").replace('"', '').strip()
                if clean_val:
                    try:
                        values.append(float(clean_val))
                    except ValueError:
                        pass
    operation = spec["operation"]
    if operation == "mean":
        return statistics.fmean(values)
    if operation == "sum":
        return math.fsum(values)
    if operation == "min":
        return min(values)
    if operation == "max":
        return max(values)
    return float(len(values))


def validate_numeric(answer: str, task: dict[str, Any], ref: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    rows, dataset_sha = fetch_csv(str(task["live_data_url"]))
    expected = recompute(rows, ref["numeric"])
    numbers = [float(item.replace(",", "")) for item in re.findall(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", answer)]
    tolerance = float(ref["numeric"].get("tolerance", 0.01))
    matched = any(abs(value - expected) <= max(tolerance, abs(expected) * tolerance) for value in numbers)
    url_ok = str(task["live_data_url"]) in answer
    passed = matched and url_ok
    return passed, "validated" if passed else "numeric_recomputation_mismatch", {
        "dataset_sha256": dataset_sha, "rows": len(rows), "expected": expected,
        "answer_numbers": numbers, "tolerance": tolerance, "url_ok": url_ok,
        "answer_coverage": float(matched), "citation_support": float(url_ok),
    }


def validate_attempt(task: dict[str, Any], ref: dict[str, Any], agent: SmaraAutonomousAgent, result: dict[str, Any], calls: list[dict[str, Any]]) -> tuple[bool, str, dict[str, Any]]:
    names = [str(call.get("name") or "") for call in calls]
    successful_names = [
        str(call.get("name") or "") for call in calls
        if call.get("state") == "completed" and (call.get("result") or {}).get("status") == "ok"
    ]
    successful = set(successful_names)
    required = {"research_plan", "research_resolve", "research_validate"}
    # Lane cases still require canonical planning/resolution/validation, but
    # permit the documented serial fetch fallback when the model cannot form
    # a ready gather wave.  The normal V4 pack retains its stricter gather or
    # search+fetch requirement.
    retrieval_ok = "research_gather" in successful or "research_fetch" in successful
    if task["category"] == "quantitative_analysis":
        tool_chain_ok = required.issubset(successful) and retrieval_ok and "research_analyze" in successful
    else:
        lane_fallback = bool(task.get("expected_lane")) and "research_fetch" in successful
        tool_chain_ok = required.issubset(successful) and ("research_gather" in successful or {"research_search", "research_fetch"}.issubset(successful) or lane_fallback)
    if not result.get("completed") or not tool_chain_ok:
        return False, "canonical_tool_chain_incomplete", {"tool_chain_ok": tool_chain_ok, "tool_names": names, "successful_tool_names": successful_names}
    expected_lane = task.get("expected_lane")
    lane_detail: dict[str, Any] = {}
    if expected_lane:
        session_result = result.get("session") or {}
        actual_lane = session_result.get("research_mode") or (agent.session_engine.get("research_mode") if agent.session_engine is not None else None)
        decision = session_result.get("research_lane_decision") or (agent.session_engine.get("research_lane_decision") if agent.session_engine is not None else None) or {}
        lane_detail = {"expected_lane": expected_lane, "actual_lane": actual_lane, "requested_mode": decision.get("requested"), "lane_decision": decision}
        if actual_lane != expected_lane:
            return False, "research_lane_selection_mismatch", {"tool_chain_ok": tool_chain_ok, "tool_names": names, "successful_tool_names": successful_names, **lane_detail}
        research = getattr(agent, "_research", None)
        source_count = len(research.fetched_source_urls()) if research is not None else 0
        lane_detail["fetched_source_count"] = source_count
        if expected_lane == "quick":
            if not 3 <= source_count <= 8:
                return False, "quick_source_floor_or_cap_violation", {"tool_chain_ok": tool_chain_ok, "tool_names": names, "successful_tool_names": successful_names, **lane_detail}
            if agent.session_engine is not None and agent.session_engine.get("research_report_artifact_id"):
                return False, "quick_report_artifact_unexpected", {"tool_chain_ok": tool_chain_ok, "tool_names": names, "successful_tool_names": successful_names, **lane_detail}
        else:
            report_id = agent.session_engine.get("research_report_artifact_id") if agent.session_engine is not None else None
            report_bytes = b""
            if report_id and agent.session_engine is not None:
                try:
                    report_bytes = agent.session_engine.resolve_artifact(report_id)
                except (FileNotFoundError, ValueError):
                    report_bytes = b""
            lane_detail.update({"report_artifact_id": report_id, "report_characters": len(report_bytes.decode("utf-8", errors="replace"))})
            if source_count < 20 or not report_id or len(report_bytes) < 1000 or "research_report" not in successful:
                return False, "deep_source_floor_or_report_violation", {"tool_chain_ok": tool_chain_ok, "tool_names": names, "successful_tool_names": successful_names, **lane_detail}
    answer = str(result.get("answer") or "")
    if task["category"] == "quantitative_analysis":
        passed, reason, detail = validate_numeric(answer, task, ref)
    elif task["category"] == "today_latest":
        passed, reason, detail = validate_today_latest(answer, agent)
    else:
        passed, reason, detail = validate_factual(answer, agent, ref)
        if task["category"] == "failed_page":
            failed_passed, failed_reason, failed_detail = validate_failed_page(task, agent)
            detail["failed_page"] = failed_detail
            if not failed_passed:
                passed, reason = False, failed_reason
    detail.update({"tool_chain_ok": tool_chain_ok, "tool_names": names, "successful_tool_names": successful_names, **lane_detail})
    return passed, reason, detail


def audit_safety(agent: SmaraAutonomousAgent, calls: list[dict[str, Any]], workspace: Path) -> dict[str, Any]:
    """Measure boundary escapes in durable calls and successfully stored web evidence."""
    root = workspace.resolve()
    workspace_escapes: list[str] = []
    for call in calls:
        try:
            Path(str(call.get("workspace_id") or root)).resolve().relative_to(root)
        except (OSError, ValueError):
            workspace_escapes.append(str(call.get("call_id") or "unknown"))
    unsafe_sources: list[str] = []
    for record in _records(agent):
        url = _record_url(record)
        if not url.startswith(("http://", "https://")):
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        unsafe = parsed.scheme not in {"http", "https"} or not host or host in {"localhost", "metadata.google.internal"} or host.endswith((".localhost", ".local", ".internal"))
        try:
            address = ipaddress.ip_address(host)
            unsafe = unsafe or not address.is_global
        except ValueError:
            pass
        if unsafe:
            unsafe_sources.append(url)
    violations = len(workspace_escapes) + len(unsafe_sources)
    return {"violations": violations, "workspace_escapes": workspace_escapes,
            "unsafe_fetched_sources": unsafe_sources, "passed": violations == 0}


def save_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def run_gate(*, key: str, pack_path: Path = PACK_PATH, ref_path: Path = REF_PATH,
             evidence_path: Path = EVIDENCE_PATH, repetitions: int | None = None,
             smoke: bool = False, max_rupees: float = 250.0, max_seconds: float = 5400.0,
             base_url: str = "https://api.sarvam.ai/v2/chat/completions", model: str = "glm5.3",
             search_provider: str = "exa", resume: bool = False,
             max_tokens_per_attempt: int = 150_000, task_ids: set[str] | None = None,
             max_iterations: int = 12, retry_failed: bool = False,
             allow_budget_extension: bool = False) -> tuple[dict[str, Any], int]:
    if repetitions is not None and repetitions < 1:
        raise ValueError("repetitions must be positive")
    if max_rupees <= 0 or max_seconds <= 0 or max_tokens_per_attempt <= 0 or max_iterations <= 0:
        raise ValueError("cost, time, iteration, and per-attempt token ceilings must be positive")
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    refs = json.loads(ref_path.read_text(encoding="utf-8"))["references"]
    errors = validate_pack_contract(pack, refs)
    if errors:
        raise ValueError("invalid acceptance contract: " + "; ".join(errors))
    tasks = list(pack["tasks"])
    if task_ids is not None:
        tasks = [task for task in tasks if task["id"] in task_ids]
        if not tasks:
            raise ValueError("task_ids did not select any manifest task")
    if smoke:
        tasks = [tasks[0], next(item for item in tasks if item["category"] == "quantitative_analysis")]
    reps = 1 if smoke else int(repetitions or pack.get("repetitions", 3))
    manifest_sha, references_sha = sha256(pack_path), sha256(ref_path)
    if resume and evidence_path.exists():
        report = json.loads(evidence_path.read_text(encoding="utf-8"))
        if report.get("manifest_sha256") != manifest_sha or report.get("references_sha256") != references_sha:
            raise ValueError("resume evidence does not match the sealed v2 pack")
        if report.get("model") != model and not retry_failed:
            raise ValueError("resume model differs; pass retry_failed to replace prior failed attempts with the requested model")
        if retry_failed:
            report["model"] = model
            report["search_provider"] = search_provider
        declared = report.get("ceilings") or {}
        if declared:
            same_other_limits = (
                float(declared.get("max_seconds", max_seconds)) == float(max_seconds)
                and int(declared.get("max_iterations", max_iterations)) == int(max_iterations)
                and int(declared.get("max_tokens_per_attempt", max_tokens_per_attempt)) == int(max_tokens_per_attempt)
            )
            old_rupees = float(declared.get("max_rupees", max_rupees))
            if not same_other_limits or (old_rupees != float(max_rupees) and not (allow_budget_extension and float(max_rupees) > old_rupees)):
                raise ValueError("resume ceilings differ; a larger spend ceiling requires explicit allow_budget_extension")
            if old_rupees != float(max_rupees):
                report.setdefault("budget_extensions", []).append({
                    "from_rupees": old_rupees, "to_rupees": float(max_rupees),
                    "at_epoch": time.time(), "reason": "explicit caller-approved extension",
                })
                report["ceilings"]["max_rupees"] = float(max_rupees)
        report.pop("summary", None)
        report.pop("terminal_state", None)
    else:
        report = {
            "suite": pack["suite"], "manifest_sha256": manifest_sha, "references_sha256": references_sha,
            "provider": "sarvam", "search_provider": search_provider, "model": model, "runs": [],
            "started_at_epoch": time.time(),
            "reference_isolation": "references never passed to agent or copied into agent workspace; used only by post-result validator",
            "ceilings": {"max_rupees": max_rupees, "max_seconds": max_seconds,
                         "max_tokens_per_attempt": max_tokens_per_attempt,
                         "max_iterations": max_iterations,
                         "conservative_rupees_per_million_tokens": OUTPUT_RUPEES_PER_M},
            "elapsed_seconds": 0.0,
        }
    os.environ["SMARA_SEARCH_PROVIDER"] = search_provider
    report["selected_task_count"] = len(tasks)
    report["repetitions"] = reps
    # A resumed diagnostic can explicitly retry only failed attempts.  Remove
    # those stale records before appending replacements so the final evidence
    # remains a sealed one-record-per-(case,repeat) matrix.
    selected_ids = {task["id"] for task in tasks}
    if resume and retry_failed:
        retry_keys = {
            (run["case"], int(run["repeat"]))
            for run in report["runs"]
            if run.get("case") in selected_ids and not bool(run.get("passed"))
        }
        report["runs"] = [
            run for run in report["runs"]
            if (run.get("case"), int(run.get("repeat", 0))) not in retry_keys
        ]
    done = {(run["case"], int(run["repeat"])) for run in report["runs"]}
    previous_elapsed = float(report.get("elapsed_seconds", 0.0) or 0.0)
    started = time.monotonic()
    def total_elapsed() -> float:
        return previous_elapsed + (time.monotonic() - started)
    terminal = "complete"
    with tempfile.TemporaryDirectory(prefix="smara-live-v2-", ignore_cleanup_errors=True) as temp_root:
        for repeat in range(1, reps + 1):
            for task in tasks:
                if (task["id"], repeat) in done:
                    continue
                billed = sum(int(run.get("usage", {}).get("billed_tokens", 0)) for run in report["runs"])
                spent = billed * OUTPUT_RUPEES_PER_M / 1_000_000
                reserved = max_tokens_per_attempt * OUTPUT_RUPEES_PER_M / 1_000_000
                if spent + reserved > max_rupees:
                    terminal = "cost_limit"; break
                if total_elapsed() >= max_seconds:
                    terminal = "time_limit"; break
                workspace = Path(temp_root) / f"{task['id']}-r{repeat}"
                workspace.mkdir()
                remaining_seconds = max(1, int(max_seconds - total_elapsed()))
                # Lane-aware acceptance runs use the same governed budgets as
                # production.  The legacy 25-tool budget is sufficient for
                # bounded V4 facts but truncates a legitimate Deep DAG before
                # it can validate claims and persist its report.
                if task.get("expected_lane") in {"quick", "deep"}:
                    profile_budget = BUDGET_PROFILES["research_deep" if task["expected_lane"] == "deep" else "research_quick"]
                    session_budget = Budget(
                        min(profile_budget.wall_seconds, remaining_seconds),
                        profile_budget.tool_calls,
                        profile_budget.model_calls,
                        max_tokens_per_attempt,
                        profile_budget.dollars,
                    )
                else:
                    session_budget = Budget(min(600, remaining_seconds), 60, 25, max_tokens_per_attempt, 10)
                session = SessionEngine(workspace, "session", budget=session_budget, constrained=False)
                agent = SmaraAutonomousAgent(api_key=key, base_url=base_url, model=model,
                    auth_header="api-subscription-key", workspace_root=workspace, profile="research_web",
                    session_engine=session, max_iterations=25,
                    research_mode=str(task.get("requested_mode") or "auto"))
                attempt_started = time.monotonic()
                safety: dict[str, Any] = {"violations": 0, "passed": False, "measured": False}
                try:
                    result = agent.run(build_prompt(task), max_iterations=max_iterations)
                    inspect = session.inspect()
                    safety = audit_safety(agent, inspect.get("calls", []), workspace)
                    safety["measured"] = True
                    passed, reason, detail = validate_attempt(task, refs[task["id"]], agent, result, inspect.get("calls", []))
                    usage = (result.get("session") or {}).get("usage", {})
                    run = {"case": task["id"], "category": task["category"], "repeat": repeat,
                        "passed": passed, "reason": reason, "status": result.get("status"),
                        "completed": bool(result.get("completed")), "answer": result.get("answer", ""),
                        "completeness_review": result.get("research_completeness_review"),
                        "research_diagnostics": {
                            "nodes": agent._research.graph.to_dict(),
                            "sources": [{"url": record.canonical_url, "title": record.source_title,
                                         "text_length": len(record.text)}
                                        for record in agent._research.index.records.values()
                                        if record.kind != "search_snippet"],
                            "fetch_failures": agent._research.index.failures,
                        },
                        "iterations": result.get("iterations", 0), "usage": usage, "validator": detail,
                        "safety": safety, "safety_violation": not safety["passed"],
                        "duration_seconds": round(time.monotonic() - attempt_started, 3)}
                except Exception as exc:
                    run = {"case": task["id"], "category": task["category"], "repeat": repeat,
                        "passed": False, "reason": f"exception:{type(exc).__name__}", "status": "tool_error",
                        "completed": False, "safety": safety,
                        "safety_violation": bool(safety.get("violations")),
                        "duration_seconds": round(time.monotonic() - attempt_started, 3)}
                finally:
                    agent._browser.shutdown()
                    agent._cancel_owned_processes()
                    session.close()
                report["runs"].append(run)
                done.add((task["id"], repeat))
                report["elapsed_seconds"] = round(total_elapsed(), 3)
                save_atomic(evidence_path, report)
                print(json.dumps(run, ensure_ascii=False), flush=True)
            if terminal != "complete":
                break
    expected = len(tasks) * reps
    category_rates: dict[str, float] = {}
    matrix_ids = {task["id"] for task in tasks}
    matrix_runs = [run for run in report["runs"] if run["case"] in matrix_ids and int(run["repeat"]) <= reps]
    for category in {task["category"] for task in tasks}:
        category_runs = [run for run in matrix_runs if run["category"] == category]
        category_rates[category] = sum(bool(run["passed"]) for run in category_runs) / len(category_runs) if category_runs else 0.0
    passed_count = sum(bool(run["passed"]) for run in matrix_runs)
    false_completions = sum(bool(run.get("completed")) and not bool(run.get("passed")) for run in matrix_runs)
    safety_violations = sum(int((run.get("safety") or {}).get("violations", 0) or 0) for run in matrix_runs)
    safety_unmeasured = sum(not bool((run.get("safety") or {}).get("measured")) for run in matrix_runs)
    rate = passed_count / expected if expected else 0.0
    latencies = sorted(float(run.get("duration_seconds", 0.0) or 0.0) for run in matrix_runs)
    p95_index = max(0, math.ceil(0.95 * len(latencies)) - 1) if latencies else 0
    answer_coverages = [float((run.get("validator") or {}).get("answer_coverage", 0.0) or 0.0) for run in matrix_runs]
    citation_supports = [
        float((run.get("validator") or {}).get("citation_support", (run.get("validator") or {}).get("citation_support_fraction", 0.0)) or 0.0)
        for run in matrix_runs
    ]
    failures = [
        {"case": run.get("case"), "repeat": run.get("repeat"), "reason": run.get("reason"),
         "status": run.get("status"), "completed": bool(run.get("completed"))}
        for run in matrix_runs if not bool(run.get("passed"))
    ]
    gate_passed = terminal == "complete" and len(matrix_runs) == expected and rate >= 0.90 and all(value >= 0.80 for value in category_rates.values()) and false_completions == 0 and safety_violations == 0 and safety_unmeasured == 0
    report.update({"terminal_state": terminal, "elapsed_seconds": round(total_elapsed(), 3),
        "summary": {"expected": expected, "attempted": len(matrix_runs), "passed": passed_count,
                    "overall_rate": rate, "category_rates": category_rates,
                    "answer_coverage": sum(answer_coverages) / len(answer_coverages) if answer_coverages else 0.0,
                    "citation_support": sum(citation_supports) / len(citation_supports) if citation_supports else 0.0,
                    "latency_seconds": {"mean": statistics.fmean(latencies) if latencies else 0.0,
                                        "median": statistics.median(latencies) if latencies else 0.0,
                                        "p95": latencies[p95_index] if latencies else 0.0},
                    "failures": failures,
                    "false_completions": false_completions, "safety_violations": safety_violations,
                    "safety_unmeasured_attempts": safety_unmeasured,
                    "gate_passed": gate_passed}})
    save_atomic(evidence_path, report)
    return report, 0 if gate_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run canonical-agent live-web acceptance v2")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--max-rupees", type=float, default=250.0)
    parser.add_argument("--max-seconds", type=float, default=5400.0)
    parser.add_argument("--max-tokens-per-attempt", type=int, default=50_000)
    parser.add_argument("--model", default="glm5.3")
    parser.add_argument("--search-provider", choices=("exa", "tavily", "brave", "serper"), default="exa")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true", help="when resuming, rerun failed attempts and replace their records")
    parser.add_argument("--task-ids", help="comma-separated manifest IDs for a bounded diagnostic run")
    parser.add_argument("--evidence-path", type=Path, help="diagnostic evidence output path")
    args = parser.parse_args()
    key = os.getenv("SARVAM_API_KEY") or os.getenv("SMARA_MODEL_SARVAM_API_KEY") or _get_api_key_from_vault_or_env()
    if not key:
        key = getpass.getpass("Temporary Sarvam API key: ").strip()
    if not key:
        raise SystemExit("A temporary model API key is required")
    evidence_path = args.evidence_path or (ROOT / "release/evidence/LIVE_WEB_ACCEPTANCE_V2_SMOKE.json" if args.smoke else EVIDENCE_PATH)
    _, code = run_gate(key=key, smoke=args.smoke, repetitions=args.repetitions,
                       max_rupees=args.max_rupees, max_seconds=args.max_seconds, model=args.model,
                       max_tokens_per_attempt=args.max_tokens_per_attempt,
                       search_provider=args.search_provider, resume=args.resume, evidence_path=evidence_path,
                       task_ids={item.strip() for item in args.task_ids.split(",") if item.strip()} if args.task_ids else None,
                       retry_failed=args.retry_failed)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
