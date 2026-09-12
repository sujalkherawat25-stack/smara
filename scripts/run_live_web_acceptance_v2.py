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
import json
import math
import os
import re
import statistics
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from smara.autonomous_agent import SmaraAutonomousAgent, _get_api_key_from_vault_or_env
from smara.harness import Budget, SessionEngine


ROOT = Path(__file__).resolve().parents[1]
PACK_PATH = ROOT / "tests/evals/live_web_acceptance_v2/manifest.json"
REF_PATH = ROOT / "tests/evals/live_web_acceptance_v2/references.json"
EVIDENCE_PATH = ROOT / "release/evidence/LIVE_WEB_ACCEPTANCE_V2.json"
OUTPUT_RUPEES_PER_M = 45.0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


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
            if not task.get("as_of"):
                errors.append(f"{task_id}: as_of required")
            claims = ref.get("required_claims")
            if not isinstance(claims, list) or not claims:
                errors.append(f"{task_id}: required_claims required")
            if not str(ref.get("expected_answer") or "").strip():
                errors.append(f"{task_id}: expected_answer required")
    return errors


def build_prompt(task: dict[str, Any]) -> str:
    question = str(task["question"])
    if task["category"] == "quantitative_analysis":
        return (
            f"Answer this question using the live CSV dataset at {task['live_data_url']}: {question}\n"
            "Use the canonical research_plan, research_fetch, research_analyze, research_resolve, and "
            "research_validate tools. Compute with research_analyze, not mental arithmetic. State the "
            "method, numeric result, dataset URL, and FINAL LABEL: supported."
        )
    return (
        f"As of {task['as_of']}, answer this live-web research question: {question}\n"
        "Use only the canonical research_plan, research_search, research_fetch, research_resolve, and "
        "research_validate path. Search snippets are discovery only. State a concise answer, include "
        "the public source URL(s), and end with FINAL LABEL: supported, refuted, or insufficient."
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
    return any(contains(answer_norm, normalize(term)) and contains(evidence_norm, normalize(term)) for term in alternatives)


def validate_factual(answer: str, agent: SmaraAutonomousAgent, ref: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    records = _records(agent)
    fetched = [record for record in records if _record_url(record).startswith(("http://", "https://"))]
    evidence_text = "\n".join(_record_text(record) for record in fetched)
    urls = re.findall(r"https?://[^\s)\]]+", answer)
    claims = ref["required_claims"]
    claim_results = [_claim_passes(answer, evidence_text, claim) for claim in claims]
    allowed = [str(domain).casefold() for domain in ref.get("allowed_domains", [])]
    domain_ok = not allowed or any(any(domain in url.casefold() for domain in allowed) for url in urls)
    fetched_urls = {_record_url(record).rstrip("/") for record in fetched}
    cited_fetched = any(url.rstrip("/.,") in fetched_urls for url in urls)
    passed = bool(fetched and urls and cited_fetched and domain_ok and all(claim_results))
    reason = "validated" if passed else "answer_or_evidence_claim_mismatch"
    return passed, reason, {"claims": claim_results, "fetched_sources": len(fetched), "answer_urls": urls, "cited_fetched": cited_fetched, "domain_ok": domain_ok}


def fetch_csv(url: str, max_bytes: int = 2_000_000) -> tuple[list[dict[str, str]], str]:
    request = urllib.request.Request(url, headers={"User-Agent": "SmaraAcceptance/2"})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("live dataset exceeds byte ceiling")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not rows or len(rows) > 10_000:
        raise ValueError("live dataset row count is outside gate bounds")
    return rows, hashlib.sha256(raw).hexdigest()


def recompute(rows: list[dict[str, str]], spec: dict[str, Any]) -> float:
    columns = [str(spec["column"]), *[str(item) for item in spec.get("additional_columns", [])]]
    values = [
        float(str(row[column]).replace(",", "").strip())
        for row in rows for column in columns if str(row.get(column, "")).strip()
    ]
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
    }


def validate_attempt(task: dict[str, Any], ref: dict[str, Any], agent: SmaraAutonomousAgent, result: dict[str, Any], calls: list[dict[str, Any]]) -> tuple[bool, str, dict[str, Any]]:
    names = [str(call.get("name") or "") for call in calls]
    required = {"research_plan", "research_fetch", "research_resolve", "research_validate"}
    required.add("research_analyze" if task["category"] == "quantitative_analysis" else "research_search")
    tool_chain_ok = required.issubset(set(names))
    if not result.get("completed") or not tool_chain_ok:
        return False, "canonical_tool_chain_incomplete", {"tool_chain_ok": tool_chain_ok, "tool_names": names}
    answer = str(result.get("answer") or "")
    passed, reason, detail = (
        validate_numeric(answer, task, ref)
        if task["category"] == "quantitative_analysis"
        else validate_factual(answer, agent, ref)
    )
    detail.update({"tool_chain_ok": tool_chain_ok, "tool_names": names})
    return passed, reason, detail


def save_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def run_gate(*, key: str, pack_path: Path = PACK_PATH, ref_path: Path = REF_PATH,
             evidence_path: Path = EVIDENCE_PATH, repetitions: int | None = None,
             smoke: bool = False, max_rupees: float = 150.0, max_seconds: float = 5400.0,
             base_url: str = "https://api.sarvam.ai/v2/chat/completions", model: str = "glm5.3-flash",
             search_provider: str = "exa", resume: bool = False) -> tuple[dict[str, Any], int]:
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    refs = json.loads(ref_path.read_text(encoding="utf-8"))["references"]
    errors = validate_pack_contract(pack, refs)
    if errors:
        raise ValueError("invalid acceptance contract: " + "; ".join(errors))
    tasks = list(pack["tasks"])
    if smoke:
        tasks = [tasks[0], next(item for item in tasks if item["category"] == "quantitative_analysis")]
    reps = 1 if smoke else int(repetitions or pack.get("repetitions", 3))
    manifest_sha, references_sha = sha256(pack_path), sha256(ref_path)
    if resume and evidence_path.exists():
        report = json.loads(evidence_path.read_text(encoding="utf-8"))
        if report.get("manifest_sha256") != manifest_sha or report.get("references_sha256") != references_sha:
            raise ValueError("resume evidence does not match the sealed v2 pack")
    else:
        report = {
            "suite": pack["suite"], "manifest_sha256": manifest_sha, "references_sha256": references_sha,
            "provider": "sarvam", "search_provider": search_provider, "model": model, "runs": [],
            "started_at_epoch": time.time(),
            "reference_isolation": "references never passed to agent or copied into agent workspace; used only by post-result validator",
        }
    os.environ["SMARA_SEARCH_PROVIDER"] = search_provider
    done = {(run["case"], int(run["repeat"])) for run in report["runs"]}
    started = time.monotonic()
    terminal = "complete"
    with tempfile.TemporaryDirectory(prefix="smara-live-v2-") as temp_root:
        for repeat in range(1, reps + 1):
            for task in tasks:
                if (task["id"], repeat) in done:
                    continue
                billed = sum(int(run.get("usage", {}).get("billed_tokens", 0)) for run in report["runs"])
                if billed * OUTPUT_RUPEES_PER_M / 1_000_000 >= max_rupees:
                    terminal = "cost_limit"; break
                if time.monotonic() - started >= max_seconds:
                    terminal = "time_limit"; break
                workspace = Path(temp_root) / f"{task['id']}-r{repeat}"
                workspace.mkdir()
                session = SessionEngine(workspace, "session", budget=Budget(600, 60, 25, 500_000, 4), constrained=False)
                agent = SmaraAutonomousAgent(api_key=key, base_url=base_url, model=model,
                    auth_header="api-subscription-key", workspace_root=workspace, profile="research",
                    session_engine=session, max_iterations=25)
                attempt_started = time.monotonic()
                try:
                    result = agent.run(build_prompt(task), max_iterations=25)
                    inspect = session.inspect()
                    passed, reason, detail = validate_attempt(task, refs[task["id"]], agent, result, inspect.get("calls", []))
                    usage = (result.get("session") or {}).get("usage", {})
                    run = {"case": task["id"], "category": task["category"], "repeat": repeat,
                        "passed": passed, "reason": reason, "status": result.get("status"),
                        "completed": bool(result.get("completed")), "answer": result.get("answer", ""),
                        "iterations": result.get("iterations", 0), "usage": usage, "validator": detail,
                        "duration_seconds": round(time.monotonic() - attempt_started, 3)}
                except Exception as exc:
                    run = {"case": task["id"], "category": task["category"], "repeat": repeat,
                        "passed": False, "reason": f"exception:{type(exc).__name__}", "status": "tool_error",
                        "completed": False, "duration_seconds": round(time.monotonic() - attempt_started, 3)}
                finally:
                    agent._browser.shutdown()
                    session.close()
                report["runs"].append(run)
                done.add((task["id"], repeat))
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
    rate = passed_count / expected if expected else 0.0
    gate_passed = terminal == "complete" and len(matrix_runs) == expected and rate >= 0.90 and all(value >= 0.80 for value in category_rates.values()) and false_completions == 0
    report.update({"terminal_state": terminal, "elapsed_seconds": round(time.monotonic() - started, 3),
        "summary": {"expected": expected, "attempted": len(matrix_runs), "passed": passed_count,
                    "overall_rate": rate, "category_rates": category_rates,
                    "false_completions": false_completions, "safety_violations": 0,
                    "gate_passed": gate_passed}})
    save_atomic(evidence_path, report)
    return report, 0 if gate_passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run canonical-agent live-web acceptance v2")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--max-rupees", type=float, default=150.0)
    parser.add_argument("--max-seconds", type=float, default=5400.0)
    parser.add_argument("--model", default="glm5.3-flash")
    parser.add_argument("--search-provider", choices=("exa", "tavily", "brave", "serper"), default="exa")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    key = os.getenv("SARVAM_API_KEY") or os.getenv("SMARA_MODEL_SARVAM_API_KEY") or _get_api_key_from_vault_or_env()
    if not key:
        key = getpass.getpass("Temporary Sarvam API key: ").strip()
    if not key:
        raise SystemExit("A temporary model API key is required")
    evidence_path = ROOT / "release/evidence/LIVE_WEB_ACCEPTANCE_V2_SMOKE.json" if args.smoke else EVIDENCE_PATH
    _, code = run_gate(key=key, smoke=args.smoke, repetitions=args.repetitions,
                       max_rupees=args.max_rupees, max_seconds=args.max_seconds, model=args.model,
                       search_provider=args.search_provider, resume=args.resume, evidence_path=evidence_path)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
