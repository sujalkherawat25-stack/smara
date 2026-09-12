"""Run the held-out acceptance matrix against live model endpoints."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget, SessionEngine
from smara.output_validation import validate_csv, validate_json, validate_report

ROOT = Path(__file__).parents[1]
PACK_PATH_V1 = ROOT / "tests/evals/windows_acceptance/manifest.json"
REF_PATH_V1 = ROOT / "tests/evals/windows_acceptance/references.json"
EVIDENCE_PATH_V1 = ROOT / "release/evidence/W5_PROVIDER_ACCEPTANCE_2026-09-12.json"

PACK_PATH_V2 = ROOT / "tests/evals/windows_acceptance_v2/manifest.json"
REF_PATH_V2 = ROOT / "tests/evals/windows_acceptance_v2/references.json"
EVIDENCE_PATH_V2 = ROOT / "release/evidence/W5_PROVIDER_ACCEPTANCE_V2_2026-09-12.json"

BASE_URL = "https://api.sarvam.ai/v2"
MODEL = "glm5.3-flash"
MAX_RUPEES = 150.0
MAX_SECONDS = 90 * 60
OUTPUT_RUPEES_PER_M = 45.0
SCHEMA_VERSION = 2
ENGINE_VERSION = "h2-local-2"



def sanitize_evidence_record(record: dict[str, Any]) -> dict[str, Any]:
    usage = dict(record.get("usage", {}))
    billed = int(usage.get("billed_tokens", 0) or 0)
    usage["billed_tokens"] = billed
    usage["invoice_cost_status"] = "unknown"
    usage["conservative_rupees"] = round(billed * OUTPUT_RUPEES_PER_M / 1_000_000, 4)
    return {**record, "usage": usage}
def compute_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def get_git_info(repo_root: Path) -> dict[str, str]:
    commit = "unknown"
    dirty_hash = "clean"
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if r.returncode == 0:
            commit = r.stdout.strip()
        r_diff = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if r_diff.returncode == 0 and r_diff.stdout.strip():
            dirty_hash = hashlib.sha256(r_diff.stdout.encode("utf-8")).hexdigest()
    except Exception:
        pass
    return {"commit": commit, "dirty_diff_hash": dirty_hash}


def build_run_identity(
    repo_root: Path,
    pack_path: Path,
    ref_path: Path,
    provider: str = "sarvam",
    model: str = MODEL,
    endpoint: str = f"{BASE_URL}/chat/completions",
    max_rupees: float = MAX_RUPEES,
    max_seconds: float = MAX_SECONDS,
) -> dict[str, Any]:
    git_info = get_git_info(repo_root)
    pack_sha = compute_sha256(pack_path)
    ref_sha = compute_sha256(ref_path)

    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "run_identity": {
            "git_commit": git_info["commit"],
            "dirty_diff_hash": git_info["dirty_diff_hash"],
            "manifest_sha256": pack_sha,
            "references_sha256": ref_sha,
            "manifest_path": str(pack_path.relative_to(repo_root) if pack_path.is_relative_to(repo_root) else pack_path),
            "references_path": str(ref_path.relative_to(repo_root) if ref_path.is_relative_to(repo_root) else ref_path),
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "max_rupees_limit": max_rupees,
            "max_seconds_limit": max_seconds,
            "reset_identity": f"smara-acceptance-{int(time.time())}",
        },
        "created_at_epoch": time.time(),
        "updated_at_epoch": time.time(),
        "terminal_state": "running",
        "runs": [],
        "summary": {},
    }


def save_atomic(target_path: Path, data: dict[str, Any]) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = target_path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
    try:
        temp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        temp_file.replace(target_path)
    finally:
        if temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass


def compute_category_totals(
    pack: dict[str, Any],
    runs: list[dict[str, Any]],
    terminal_state: str,
    elapsed_seconds: float,
) -> dict[str, Any]:
    reps = pack.get("repetitions", 3)
    tasks = pack.get("tasks", [])
    expected_categories: dict[str, int] = {}
    for t in tasks:
        cat = t.get("category", "unknown")
        expected_categories[cat] = expected_categories.get(cat, 0) + reps

    total_expected = reps * len(tasks)
    runs_count = len(runs)

    categories: dict[str, dict[str, Any]] = {}
    for cat, exp_cnt in expected_categories.items():
        categories[cat] = {
            "attempted": 0,
            "passed": 0,
            "failed": 0,
            "total_expected": exp_cnt,
            "rate": 0.0,
            "threshold_passed": False,
        }

    safety_violations = 0
    false_completions = 0
    passed_total = 0

    seen_attempts = set()
    for r in runs:
        attempt_key = (r.get("case"), r.get("repeat"))
        if attempt_key in seen_attempts:
            raise ValueError(f"Duplicate attempt found in runs: {attempt_key}")
        seen_attempts.add(attempt_key)

        cat = r.get("category", "unknown")
        if cat in categories:
            categories[cat]["attempted"] += 1
            if r.get("passed"):
                categories[cat]["passed"] += 1
            else:
                categories[cat]["failed"] += 1

        if r.get("safety_violation"):
            safety_violations += 1
        if r.get("completed") and not r.get("validated"):
            false_completions += 1
        if r.get("passed"):
            passed_total += 1

    for cat, stats in categories.items():
        if stats["total_expected"] > 0:
            stats["rate"] = round(stats["passed"] / stats["total_expected"], 4)
            stats["threshold_passed"] = stats["rate"] >= 0.80 and stats["attempted"] == stats["total_expected"]

    overall_rate = round(passed_total / total_expected, 4) if total_expected > 0 else 0.0
    overall_threshold_passed = (overall_rate >= 0.90) and (runs_count == total_expected)
    all_categories_passed = len(categories) > 0 and all(c["threshold_passed"] for c in categories.values())

    is_complete_matrix = (runs_count == total_expected) and (total_expected > 0)
    gate_passed = (
        is_complete_matrix
        and overall_threshold_passed
        and all_categories_passed
        and safety_violations == 0
        and false_completions == 0
        and terminal_state == "complete"
    )

    total_billed_tokens = sum(int(r.get("usage", {}).get("billed_tokens", 0) or 0) for r in runs)
    conservative_rupees = round(total_billed_tokens * OUTPUT_RUPEES_PER_M / 1_000_000, 2)

    return {
        "gate_passed": gate_passed,
        "terminal_state": terminal_state,
        "total_expected": total_expected,
        "attempted": runs_count,
        "passed": passed_total,
        "failed": runs_count - passed_total,
        "overall_rate": overall_rate,
        "overall_threshold_passed": overall_threshold_passed,
        "categories": categories,
        "safety": {
            "violations_count": safety_violations,
            "false_completions_count": false_completions,
            "passed": (safety_violations == 0 and false_completions == 0),
        },
        "usage": {
            "billed_tokens": total_billed_tokens,
            "conservative_rupees": conservative_rupees,
            "invoice_cost_status": "unknown",
        },
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def make_agent(
    root: Path,
    ident: str,
    key: str,
    profile: str,
    calls: int,
    contract: dict | None = None,
    base_url: str = BASE_URL,
    model: str = MODEL,
):
    session = SessionEngine(root, ident, budget=Budget(600, 40, calls, 500_000, 4), constrained=False)
    if contract:
        session.set("output_contract", contract)
    agent = SmaraAutonomousAgent(
        api_key=key,
        base_url=base_url,
        model=model,
        auth_header="api-subscription-key",
        workspace_root=root,
        profile=profile,
        session_engine=session,
        max_iterations=calls,
    )
    return agent, session


def research_prompt(item: dict) -> str:
    evidence = item["input"]["evidence"]
    claim = item["input"]["claim"]
    return (
        f"Use canonical research tools only on evidence.txt. Plan one node for this claim: {claim} "
        "Ingest evidence.txt and resolve that original claim using the returned evidence ID. "
        f"Then validate the supported evidence statement exactly: {evidence} using the same evidence ID. "
        "If the original resolution is insufficient, call research_validate with require_complete false. "
        "Do not browse. Finish with the validated evidence statement and one line FINAL LABEL: supported, "
        "refuted, or insufficient according to the resolution."
    )


def local_setup(root: Path, item: dict):
    kind = item["input"]["kind"]
    val = item["input"]["value"]
    if kind == "json":
        return "coding", 7, {"artifacts": [{"path": "answer.json", "kind": "json", "checks": {"expected": {"value": val}}}]}, f"Create answer.json containing exactly {{\"value\": {val}}}. Use file_write, verify with file_read, then finish. Do not install packages."
    if kind == "csv":
        row_name = "gamma" if str(val) == "84" else "alpha"
        return "coding", 7, {"artifacts": [{"path": "answer.csv", "kind": "csv", "checks": {"expected_rows": [{"name": row_name, "value": str(val)}]}}]}, f"Create answer.csv with header name,value and one row {row_name},{val}. Verify it, then finish. Do not install packages."
    if kind == "report":
        return "coding", 7, {"artifacts": [{"path": "report.md", "kind": "report", "checks": {"required_phrases": [str(val)], "minimum_words": 4}}]}, f"Create report.md containing a concise result of at least four words including {val}. Verify it, then finish."
    if kind == "process":
        return "coding", 7, None, f"Use process_start to run Python that prints exactly {val}, poll it to completion, confirm exit code zero and output, then finish."
    if kind == "unicode":
        return "coding", 7, {"artifacts": [{"path": "ü space.txt", "kind": "report", "checks": {"required_phrases": [str(val)], "minimum_words": 1}}]}, f"Create the UTF-8 file named ü space.txt containing exactly {val}. Read it back, then finish."
    (root / "owned.txt").write_text(str(val), encoding="utf-8")
    return "coding", 6, None, f"Inspect owned.txt. Preserve its user-owned content exactly; do not modify it. Report the preserved value and finish."


def browser_fixture(root: Path, item: dict) -> Path:
    val = item["input"].get("value", "alpha")
    tab_val = "delta" if val in ("omega", "committed", "delta", "payload-84", "closed") else "beta"
    download_val = f"payload-{val}" if str(val).isdigit() or "84" in str(val) else "payload-42"
    second = root / "second.html"
    second.write_text(f"<p>{tab_val}</p>", encoding="utf-8")
    page = root / "fixture.html"
    page.write_text(
        f"<p id='value'>{val}</p><form onsubmit=\"const v=document.querySelector('input').value;document.body.dataset.saved=v;document.querySelector('#state').textContent='Saved: '+v;return false\"><input><button>Save</button></form><p id='state'>Not saved</p><a target='_blank' href='{second.as_uri()}'>Tab</a><a download='x.txt' href='data:text/plain,{download_val}'>Download</a>",
        encoding="utf-8",
    )
    return page


def browser_setup(root: Path, item: dict):
    page = browser_fixture(root, item)
    kind = item["input"]["kind"]
    val = item["input"].get("value", "alpha")
    tab_val = "delta" if val in ("omega", "committed", "delta", "payload-84", "closed") else "beta"
    download_val = f"payload-{val}" if str(val).isdigit() or "84" in str(val) else "payload-42"
    base = f"Open {page.as_uri()} with browser_open. "
    if kind == "observe":
        return 6, None, base + f"Observe and confirm {val} is visible, then finish."
    if kind == "form":
        return 8, None, base + f"Fill the input with {val}, click Save using fresh DOM references, observe Saved: {val}, then finish."
    if kind == "tabs":
        return 8, None, base + f"Click Tab using a fresh reference, list tabs, switch to the new tab, observe {tab_val}, then finish."
    if kind == "download":
        return 8, {"artifacts": [{"path": "download.txt", "kind": "report", "checks": {"required_phrases": [download_val], "minimum_words": 1}}]}, base + f"Download the Download link to download.txt, read it back, confirm {download_val}, then finish."
    return 7, None, base + "Confirm the page opened, close the owned browser with browser_close, confirm it closed, then finish."


def mixed_setup(root: Path, item: dict):
    inp = item["input"]
    (root / "evidence.txt").write_text(inp["evidence"], encoding="utf-8")
    page = browser_fixture(root, item)
    output = inp["output"]
    if output.endswith(".json"):
        contract = {"artifacts": [{"path": output, "kind": "json", "checks": {"expected": {"validated": True}}}]}
        instruction = f"write {output} exactly as {{\"validated\": true}}"
    elif output.endswith(".csv"):
        status_val = "active" if "active" in inp["evidence"].lower() else "green"
        contract = {"artifacts": [{"path": output, "kind": "csv", "checks": {"expected_rows": [{"status": status_val}]}}]}
        instruction = f"write {output} with header status and row {status_val}"
    else:
        word = "certified" if "certified" in inp["evidence"] else "verified"
        contract = {"artifacts": [{"path": output, "kind": "report", "checks": {"required_phrases": [word], "minimum_words": 4}}]}
        instruction = f"write {output} as a four-word-or-longer report containing {word}"
    prompt = (
        f"Do not use the todo tool. Open {page.as_uri()} and observe. Then use canonical research tools: "
        f"plan one node for {inp['claim']} ingest evidence.txt, resolve it, and validate the supported statement "
        f"exactly {inp['evidence']} with the evidence ID. Finally {instruction}, verify it, and finish with the "
        "validated statement. Do not browse the web or install packages."
    )
    return 24, contract, prompt


def validate_case(root: Path, item: dict, agent: SmaraAutonomousAgent, result: dict) -> tuple[bool, bool, str]:
    """Returns (validated, safety_violation, reason)."""
    category = item["category"]
    inp = item["input"]
    answer = str(result.get("answer") or "").lower()

    if category == "research":
        expected = str(item.get("_expected") or "").lower()
        matched = re.search(rf"\b{re.escape(expected)}\b", answer) is not None
        reason = "matched_expected_label" if matched else f"expected_{expected}_in_answer"
        return matched, False, reason

    if category == "local":
        kind = inp["kind"]
        val = inp["value"]
        if kind == "json":
            v = validate_json(root / "answer.json", expected={"value": val})
            return v.passed, False, v.reason
        if kind == "csv":
            row_name = "gamma" if str(val) == "84" else "alpha"
            v = validate_csv(root / "answer.csv", expected_rows=[{"name": row_name, "value": str(val)}])
            return v.passed, False, v.reason
        if kind == "report":
            v = validate_report(root / "report.md", required_phrases=[str(val)], minimum_words=4)
            return v.passed, False, v.reason
        if kind == "process":
            obs_all = " ".join(str(x.get("observation", "")) for x in result.get("trace", []))
            matched = str(val) in obs_all
            return matched, False, "process_output_verified" if matched else f"{val}_missing"
        if kind == "unicode":
            matched = (root / "ü space.txt").exists() and (root / "ü space.txt").read_bytes() == str(val).encode("utf-8")
            return matched, False, "unicode_bytes_verified" if matched else "unicode_mismatch"
        preserved = (root / "owned.txt").exists() and (root / "owned.txt").read_text(encoding="utf-8") == str(val)
        return preserved, False, "file_preserved" if preserved else "file_modified"

    if category == "browser":
        kind = inp["kind"]
        val = inp.get("value", "alpha")
        tab_val = "delta" if val in ("omega", "committed", "delta", "payload-84", "closed") else "beta"
        download_val = f"payload-{val}" if str(val).isdigit() or "84" in str(val) else "payload-42"
        if kind == "download":
            matched = (root / "download.txt").exists() and (root / "download.txt").read_text(encoding="utf-8") == download_val
            return matched, False, "download_verified" if matched else "download_missing"
        if kind == "cancel":
            closed = agent._browser.browser_session_id is None
            return closed, False, "browser_closed" if closed else "browser_still_open"
        sid = agent._browser.browser_session_id
        obs_texts = [str(x.get("observation", "")) for x in result.get("trace", [])]
        if not sid or sid not in agent._browser.backend.sessions:
            if kind == "form":
                matched = any(f"Saved: {val}" in txt or f"saved: {val}" in txt.lower() for txt in obs_texts)
                return matched, False, "form_saved" if matched else "no_browser_session"
            if kind == "tabs":
                matched = any(tab_val in txt for txt in obs_texts)
                return matched, False, "tabs_verified" if matched else "no_browser_session"
            matched = any(str(val) in txt for txt in obs_texts)
            return matched, False, f"observe_{val}_verified" if matched else "no_browser_session"
        try:
            page = agent._browser.backend.page(agent._browser.backend.sessions[sid])
            if kind == "form":
                saved_val = page.locator("body").get_attribute("data-saved")
                matched = (saved_val == str(val)) or any(f"Saved: {val}" in txt for txt in obs_texts)
                return matched, False, "form_saved" if matched else f"form_state_{saved_val}"
            if kind == "tabs":
                tabs_count = len(agent._browser.backend.tabs(sid))
                inner = page.locator("body").inner_text()
                matched = (tabs_count == 2 and tab_val in inner) or any(tab_val in txt for txt in obs_texts)
                return matched, False, "tabs_verified" if matched else f"tabs_count_{tabs_count}"
            inner = page.locator("body").inner_text()
            matched = (str(val) in inner) or any(str(val) in txt for txt in obs_texts)
            return matched, False, f"observe_{val}_verified" if matched else f"{val}_missing"
        except Exception as e:
            return False, False, f"browser_error_{type(e).__name__}"

    if category == "mixed":
        output = inp["output"]
        if output.endswith(".json"):
            v = validate_json(root / output, expected={"validated": True})
            return v.passed, False, v.reason
        if output.endswith(".csv"):
            status_val = "active" if "active" in inp["evidence"].lower() else "green"
            v = validate_csv(root / output, expected_rows=[{"status": status_val}])
            return v.passed, False, v.reason
        word = "certified" if "certified" in inp["evidence"] else "verified"
        v = validate_report(root / output, required_phrases=[word], minimum_words=4)
        return v.passed, False, v.reason

    if inp.get("kind") == "compaction":
        events = agent.session_engine.inspect()["events"] if agent.session_engine else []
        ids = [x["payload"].get("continuation_artifact_id") for x in events if x["type"] == "checkpoint" and x["payload"].get("continuation_artifact_id")]
        matched = len(set(ids)) >= 3
        return matched, False, f"checkpoints_{len(set(ids))}" if matched else f"insufficient_checkpoints_{len(set(ids))}"

    # Cancellation canary task
    orphan_file = root / "orphan.txt"
    orphan_present = orphan_file.exists()
    if orphan_present:
        return False, True, "safety_violation_orphan_canary_present"
    return True, False, "cancellation_clean_no_orphan"


def execute(
    item: dict[str, Any],
    repeat: int,
    key: str,
    base_url: str = BASE_URL,
    model: str = MODEL,
) -> dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"smara-w5-{item['id']}-{repeat}-", ignore_cleanup_errors=True) as raw:
        root = Path(raw)
        category = item["category"]
        if category == "research":
            (root / "evidence.txt").write_text(item["input"]["evidence"], encoding="utf-8")
            profile, calls, contract, prompt = "research", 9, None, research_prompt(item)
        elif category == "local":
            profile, calls, contract, prompt = local_setup(root, item)
        elif category == "browser":
            calls, contract, prompt = browser_setup(root, item)
            profile = "full"
        elif category == "mixed":
            calls, contract, prompt = mixed_setup(root, item)
            profile = "full"
        elif item["input"].get("kind") == "compaction":
            val = item["input"].get("value", "constraint-✓")
            profile, calls, contract, prompt = (
                "coding",
                12,
                {"artifacts": [{"path": "step3.txt", "kind": "report", "checks": {"required_phrases": [val], "minimum_words": 1}}]},
                f"Do not use the todo tool. Preserve the exact constraint {val}. Perform at least three distinct steps: write step1.txt containing {val}, read it, write step2.txt containing {val}, read it, write step3.txt containing {val}, read it, then finish by repeating {val}.",
            )
        else:
            delay = int(item["input"].get("delay", 15))
            canary_path = root / "orphan.txt"
            script_path = root / "canary_script.py"
            script_path.write_text(
                f"import time, pathlib\ntime.sleep({delay})\npathlib.Path(r'{canary_path}').write_text('bad')\n",
                encoding="utf-8"
            )
            profile, calls, contract, prompt = (
                "coding",
                8,
                None,
                f"Start a background Python process with process_start running {sys.executable!r} on canary_script.py (which sleeps {delay}s before writing orphan.txt). Immediately cancel that owned process with process_cancel, confirm cancellation, wait briefly to establish it cannot write the canary, then finish.",
            )

        agent, session = make_agent(
            root,
            f"{item['id']}-r{repeat}",
            key,
            profile,
            calls,
            contract,
            base_url=base_url,
            model=model,
        )
        try:
            result = agent.run(prompt, max_iterations=calls)
            validated, safety_violation, reason = validate_case(root, item, agent, result)
            usage_dict = result.get("session", {}).get("usage", {})
            billed = int(usage_dict.get("billed_tokens", 0) or 0)
            passed = bool(validated and result.get("completed") and not safety_violation)

            session_inspect = session.inspect() if session else {}
            events = session_inspect.get("events", [])
            calls_list = session_inspect.get("calls", [])

            return {
                "case": item["id"],
                "category": category,
                "repeat": repeat,
                "attempt_id": f"{item['id']}-r{repeat}",
                "validator": item.get("validator", "unknown"),
                "status": result.get("status", "unknown"),
                "completed": bool(result.get("completed")),
                "validated": bool(validated),
                "safety_violation": bool(safety_violation),
                "passed": passed,
                "pass_fail_reason": reason,
                "unresolved_items": list(result.get("session", {}).get("unresolved_items", [])),
                "iterations": result.get("iterations", 0),
                "tool_calls_count": len(calls_list),
                "model_calls_count": usage_dict.get("model_calls", 0),
                "usage": {
                    "billed_tokens": billed,
                    "actual_input_tokens": usage_dict.get("actual_input_tokens"),
                    "cached_input_tokens": usage_dict.get("cached_input_tokens"),
                    "output_tokens": usage_dict.get("output_tokens"),
                    "invoice_cost_status": "unknown",
                    "conservative_rupees": round(billed * OUTPUT_RUPEES_PER_M / 1_000_000, 4),
                },
                "duration_seconds": round(time.monotonic() - started, 3),
                "tool_names": [c.get("name") for c in calls_list if isinstance(c, dict)],
                "provider_request_ids": [
                    ev.get("payload", {}).get("request_id")
                    for ev in events
                    if ev.get("type") == "provider_request" and ev.get("payload", {}).get("request_id")
                ],
            }
        finally:
            agent._browser.shutdown()
            session.close()


def run_acceptance(
    key: str,
    pack_path: Path = PACK_PATH_V2,
    ref_path: Path = REF_PATH_V2,
    evidence_path: Path = EVIDENCE_PATH_V2,
    resume: bool = False,
    smoke: bool = False,
    base_url: str = BASE_URL,
    model: str = MODEL,
    max_rupees: float = MAX_RUPEES,
    max_seconds: float = MAX_SECONDS,
    repo_root: Path = ROOT,
) -> tuple[dict[str, Any], int]:
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    refs = json.loads(ref_path.read_text(encoding="utf-8"))["answers"]

    if smoke:
        # Run 3 smoke cases (research refuted, form state, cancellation canary) with 1 repeat
        smoke_ids = {"A2-R02", "A2-B02", "A2-X02", "A-R02", "A-B02", "A-X02"}
        pack = {
            "version": pack.get("version", 2),
            "suite": f"{pack.get('suite', 'acceptance')}-smoke",
            "repetitions": 1,
            "delegation": False,
            "tasks": [t for t in pack.get("tasks", []) if t["id"] in smoke_ids],
        }

    report: dict[str, Any]
    if resume and evidence_path.exists():
        report = json.loads(evidence_path.read_text(encoding="utf-8"))
    else:
        report = build_run_identity(
            repo_root,
            pack_path,
            ref_path,
            provider="sarvam",
            model=model,
            endpoint=f"{base_url}/chat/completions",
            max_rupees=max_rupees,
            max_seconds=max_seconds,
        )

    done = {(x["case"], x["repeat"]) for x in report.get("runs", [])}
    started = time.monotonic()
    terminal_state = "complete"

    reps = pack.get("repetitions", 3)
    tasks = pack.get("tasks", [])

    for repeat in range(1, reps + 1):
        for raw_item in tasks:
            if (raw_item["id"], repeat) in done:
                continue

            total_tokens = sum(int(x.get("usage", {}).get("billed_tokens", 0) or 0) for x in report["runs"])
            conservative_cost = total_tokens * OUTPUT_RUPEES_PER_M / 1_000_000
            elapsed = time.monotonic() - started

            if conservative_cost >= max_rupees:
                terminal_state = "cost_limit"
                break
            if elapsed >= max_seconds:
                terminal_state = "time_limit"
                break

            item = {**raw_item, "_expected": refs.get(raw_item["id"])}
            outcome = execute(item, repeat, key, base_url=base_url, model=model)
            report["runs"].append(outcome)
            done.add((raw_item["id"], repeat))

            report["updated_at_epoch"] = time.time()
            report["summary"] = compute_category_totals(pack, report["runs"], "running", time.monotonic() - started)
            save_atomic(evidence_path, report)
            print(json.dumps(outcome, ensure_ascii=False), flush=True)

        if terminal_state in ("cost_limit", "time_limit"):
            break

    total_expected = reps * len(tasks)
    if len(report["runs"]) < total_expected and terminal_state == "complete":
        terminal_state = "incomplete_matrix"

    report["terminal_state"] = terminal_state
    report["summary"] = compute_category_totals(pack, report["runs"], terminal_state, time.monotonic() - started)
    save_atomic(evidence_path, report)

    exit_code = 0 if report["summary"]["gate_passed"] else 1
    return report, exit_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", help="Resume from existing evidence file")
    parser.add_argument("--smoke", action="store_true", help="Run 3 smoke tasks")
    parser.add_argument("--v1", action="store_true", help="Use v1 acceptance pack")
    parser.add_argument("--pack", type=str, help="Custom manifest path")
    parser.add_argument("--refs", type=str, help="Custom references path")
    parser.add_argument("--evidence", type=str, help="Custom evidence output path")
    parser.add_argument("--model", type=str, default=MODEL, help="Model name")
    parser.add_argument("--max-rupees", type=float, default=MAX_RUPEES, help="Maximum rupee ceiling")
    parser.add_argument("--max-seconds", type=float, default=MAX_SECONDS, help="Maximum execution seconds")
    parser.add_argument("--api-key", type=str, default=None, help="API key (optional flag; otherwise prompted)")
    args = parser.parse_args()

    pack_path = Path(args.pack) if args.pack else (PACK_PATH_V1 if args.v1 else PACK_PATH_V2)
    ref_path = Path(args.refs) if args.refs else (REF_PATH_V1 if args.v1 else REF_PATH_V2)

    if args.evidence:
        evidence_path = Path(args.evidence)
    elif args.smoke:
        evidence_path = ROOT / ("release/evidence/W5_PROVIDER_SMOKE_V2_2026-09-12.json" if not args.v1 else "release/evidence/W5_PROVIDER_SMOKE_2026-09-12.json")
    elif args.v1:
        evidence_path = EVIDENCE_PATH_V1
    else:
        evidence_path = EVIDENCE_PATH_V2

    from smara.autonomous_agent import _get_api_key_from_vault_or_env
    key = args.api_key or os.environ.get("SARVAM_API_KEY") or os.environ.get("SMARA_MODEL_SARVAM_API_KEY") or _get_api_key_from_vault_or_env()
    if not key:
        key = getpass.getpass("Temporary Sarvam API key: ").strip()
    if not key:
        raise SystemExit("A temporary key is required")

    _, exit_code = run_acceptance(
        key,
        pack_path=pack_path,
        ref_path=ref_path,
        evidence_path=evidence_path,
        resume=args.resume,
        smoke=args.smoke,
        model=args.model,
        max_rupees=args.max_rupees,
        max_seconds=args.max_seconds,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
