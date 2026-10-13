"""Maintained native outcomes gate. Never sends email or hosted memory.

Use --live only after explicit approval. Inputs are synthetic fixtures and public
Python documentation. Existing reports are never overwritten. Failed/bounded
runs remain failed; no repeated run or approval is silently substituted.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from contextlib import contextmanager

from smara.native_budget import output_payload, reservation_estimate
from smara.native_goals import GoalStore
from smara.native_goal_runtime import execute_cycle
from smara.native_provider import ChatEndpoint
from smara.native_profiles import load_profiles, resolve_profile_key
from smara.native_runtime import active_profile

PUBLIC = "Read https://docs.python.org/3/library/json.html and https://docs.python.org/3/tutorial/inputoutput.html. Explain json.load versus json.loads using actual fetched source excerpts."
CASES = {
    "coding": {"kind": "coding", "prompt": "Fix calculator.py without changing test_calculator.py. Run the tests and include calculator.py in outputs.", "requirements": [{"id": "tests", "description": "The unchanged synthetic addition tests pass"}]},
    "research": {"kind": "research", "prompt": PUBLIC, "requirements": [{"id": "json", "description": "Explain both JSON functions with supporting source excerpts"}]},
    "mixed": {"kind": "mixed", "prompt": "Fix calculator.py, do not change test_calculator.py, run its tests. " + PUBLIC, "requirements": [{"id": "tests", "description": "The unchanged synthetic tests pass"}, {"id": "json", "description": "Explain both JSON functions with observed source evidence"}]},
    "latest": {"kind": "research", "prompt": "Read the clock, then explain the current Python JSON documentation. Distinguish documentation version from today's date. " + PUBLIC, "time_sensitive": True},
    "historical": {"kind": "research", "prompt": "As of Python 3.10, explain json.load versus json.loads. Read https://docs.python.org/3.10/library/json.html and https://docs.python.org/3.10/tutorial/inputoutput.html. Do not replace historical evidence with the current release."},
    "failed-page": {"kind": "research", "prompt": "Try https://docs.python.org/3/this-smara-synthetic-page-does-not-exist.html once. Explain the failed read and then use actual sources. " + PUBLIC},
}


class LiveBudget:
    def __init__(self, path, requests=12, rupees=300, carry=None):
        self.path, self.lock = path, threading.Lock()
        self.state = {"requests": 0, "request_ceiling": requests, "reserved_inr_estimate": 0.,
                      "ceiling_inr": rupees, "actual_billing_known": False}
        if carry is not None:
            previous = json.loads(Path(carry).read_text(encoding="utf-8"))
            if (previous.get("request_ceiling", requests + 1) > requests or previous.get("ceiling_inr", rupees + 1) > rupees
                    or type(previous.get("requests")) is not int or not 0 <= previous["requests"] <= requests
                    or type(previous.get("reserved_inr_estimate")) not in {int, float}
                    or not math.isfinite(previous["reserved_inr_estimate"])
                    or not 0 <= previous["reserved_inr_estimate"] <= rupees):
                raise ValueError("Invalid previous cumulative live budget")
            self.state.update(requests=previous["requests"], reserved_inr_estimate=previous["reserved_inr_estimate"], previous_ledger=str(Path(carry).resolve()))
        self.save()

    def save(self):
        from smara.native_profiles import write_object
        write_object(self.path, self.state)

    def wrap(self, client):
        guard = self
        class Client:
            @contextmanager
            def stream(self, *args, **kwargs):
                payload = output_payload(kwargs["json"])
                # Safety estimate including output, not an assertion of pricing.
                cost = (reservation_estimate(payload) + 1024) * 396 / 1_000_000
                with guard.lock:
                    if (guard.state["requests"] >= guard.state["request_ceiling"] or
                            guard.state["reserved_inr_estimate"] + cost > guard.state["ceiling_inr"]):
                        raise RuntimeError("Approved live evaluation ceiling reached; no request sent")
                    guard.state["requests"] += 1
                    guard.state["reserved_inr_estimate"] += cost
                    guard.save()
                with client.stream(*args, **{**kwargs, "json": payload}) as response:
                    yield response
            def close(self): client.close()
        return Client()


def independent_coding_check(workspace, original_tests):
    process = subprocess.run([os.sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(workspace / "test_calculator.py")],
                             cwd=workspace, capture_output=True, text=True, timeout=30)
    return {"independent_tests_passed": process.returncode == 0,
            "tests_unchanged": (workspace / "test_calculator.py").read_bytes() == original_tests}


def saved_continuation(root, cases, carry):
    """Bind continuation to a retained failed report, not arbitrary user code."""
    root = root.resolve()
    if carry is None:
        raise ValueError("Saved continuation requires that run's budget ledger")
    previous = json.loads((root / "report.json").read_text(encoding="utf-8"))
    if previous.get("mode") != "live":
        raise ValueError("Only a retained live fixture can be continued")
    rows = {item["case"]: item for item in previous.get("cases", [])}
    if carry.resolve().parent != root:
        if not carry.is_file() or not (carry.resolve().parent / "report.json").is_file():
            raise ValueError("Saved cumulative continuation is unavailable")
        continuation = json.loads((carry.resolve().parent / "report.json").read_text(encoding="utf-8"))
        ledger = json.loads(carry.read_text(encoding="utf-8"))
        if continuation.get("mode") != "live" or continuation.get("budget") != ledger:
            raise ValueError("Continuation report and cumulative ledger disagree")
        cursor, seen = carry.resolve(), set()
        for _ in range(20):
            if cursor in seen: raise ValueError("Cyclic cumulative ledger")
            seen.add(cursor)
            record = json.loads(cursor.read_text(encoding="utf-8"))
            previous_path = record.get("previous_ledger")
            if not previous_path: break
            cursor = Path(previous_path).resolve()
        if cursor != (root / "reservations.json").resolve():
            raise ValueError("Cumulative ledger does not belong to the saved fixture")
        rows.update({item["case"]: item for item in continuation.get("cases", [])})
    if not cases or len(set(cases)) != len(cases) or any(name not in rows or rows[name].get("passed") for name in cases):
        raise ValueError("Continuation must select only previously interrupted cases")
    for name in cases:
        workspace = root / name
        if workspace.is_symlink() or not workspace.is_dir() or not (workspace / ".git").is_dir():
            raise ValueError("Saved isolated workspace is unavailable")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Requires separate explicit user authorization")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=["coding", "research", "mixed"])
    parser.add_argument("--carry-budget", type=Path, help="Carry the same approval's prior reservations; never resets the ceiling")
    parser.add_argument("--finish-interrupted", type=Path, help="Separately approved cumulative 20 requests / INR500: resume existing coding/mixed only")
    parser.add_argument("--finalize-mixed", action="store_true", help="Separately approved INR600 total / same 20 requests: remaining mixed session only")
    parser.add_argument("--resume-saved", type=Path, help="Resume only interrupted cases in an existing live report; requires separate approval")
    parser.add_argument("--request-ceiling", type=int, default=12, help="Explicit cumulative approval, not additional requests")
    parser.add_argument("--inr-ceiling", type=float, default=300, help="Explicit cumulative conservative estimate, not actual billing")
    parser.add_argument("--checkpoint-only", action="store_true", help="For a saved continuation only: one final tool-free checkpoint per selected case")
    args = parser.parse_args(argv)
    if args.resume_saved and (args.finish_interrupted or args.finalize_mixed or not args.live):
        raise ValueError("Saved continuation cannot be combined with another run mode")
    if args.checkpoint_only and not args.resume_saved:
        raise ValueError("Checkpoint-only requires saved sessions")
    if not 1 <= args.request_ceiling <= 100 or not math.isfinite(args.inr_ceiling) or not 0 < args.inr_ceiling <= 10000:
        raise ValueError("Invalid approved live ceilings")
    previous_cases = saved_continuation(args.resume_saved, args.cases, args.carry_budget) if args.resume_saved else {}
    continuing = args.resume_saved or args.finish_interrupted
    root = args.out.resolve(); root.mkdir(parents=True, exist_ok=False)
    report = {"mode": "live" if args.live else "catalog_only", "cases": [], "passed": False,
              "limitations": ["Quote presence is not semantic entailment", "Evidence coverage is not answer correctness", "No hosted memory or emails"]}
    if not args.live:
        report["catalog"] = {key: CASES[key] for key in args.cases}
        report["detail"] = "Not executed. Catalog preview is not a successful evaluation."
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 0
    profiles, selected, credentials = load_profiles()
    profile = active_profile(profiles, selected)
    endpoint = ChatEndpoint(profile["base_url"], profile["model"], resolve_profile_key(profile, credentials, allow_environment=False),
                            profile.get("auth_header", "authorization"), profile.get("context_window"))
    if endpoint.base_url.rstrip("/") != "https://api.sarvam.ai/v2":
        raise ValueError("This live authorization is limited to the configured Sarvam V2 endpoint")
    guard = LiveBudget(root / "reservations.json", requests=22 if args.finalize_mixed else 20 if args.finish_interrupted else args.request_ceiling,
                       rupees=800 if args.finalize_mixed else 500 if args.finish_interrupted else args.inr_ceiling, carry=args.carry_budget)
    if args.finalize_mixed and (not args.finish_interrupted or args.cases != ["mixed"]):
        raise ValueError("Final continuation is only for the already authorized saved mixed fixture")
    if args.finish_interrupted and (args.cases != (["mixed"] if args.finalize_mixed else ["coding", "mixed"]) or args.carry_budget is None):
        raise ValueError("Continuation is for interrupted coding/mixed fixtures and requires the cumulative budget ledger")
    original = {name: os.environ.get(name) for name in ("SMARA_NATIVE_HOME", "SMARA_DESKTOP_STATE", "SMARA_DESKTOP_CREDENTIALS")}
    try:
        run_root = continuing.resolve() if continuing else root
        os.environ.update(SMARA_NATIVE_HOME=str(run_root / "runtime"), SMARA_DESKTOP_STATE=str(root / "isolated/desktop.json"),
                          SMARA_DESKTOP_CREDENTIALS=str(root / "isolated/credentials.json"))
        store = GoalStore()
        for name in args.cases:
            case = CASES[name]
            workspace = run_root / name
            if not continuing: workspace.mkdir()
            original_tests = b""
            if case["kind"] in {"coding", "mixed"}:
                if not continuing: (workspace / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
                original_tests = b"from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n    assert add(-2, 3) == 1\n"
                if not continuing: (workspace / "test_calculator.py").write_bytes(original_tests)
            if not continuing:
                # Nested fixtures otherwise inherit the user's parent .git and
                # a normal git status can expose unrelated repository filenames.
                subprocess.run(["git", "init", "--quiet", str(workspace)], check=True, capture_output=True)
                (workspace / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n", encoding="utf-8")
            instruction = (f' Use "{os.sys.executable}" -m pytest for synthetic tests.' if original_tests else "")
            spec = dict(action="create", workspace=str(workspace), profile_id=profile["id"],
                objective=case["prompt"] + instruction + ' Perform the work with the native tools; do not merely describe a plan. Do not inspect credentials or other folders.',
                success_criteria="Address every requested requirement with observed evidence; report failures honestly.",
                task_kind=case["kind"], requirements=case.get("requirements", []), time_sensitive=case.get("time_sensitive", False),
                sandbox="workspace-write" if original_tests else "read-only", max_requests=4, max_cycles=1,
                max_output_tokens=8192, max_token_estimate=1_000_000, cycle_seconds=180, interval_seconds=0)
            if continuing:
                matches = store.list(workspace)
                if len(matches) != 1 or not matches[0]["session_id"] or matches[0]["state"] not in {"paused", "blocked", "queued"}:
                    raise ValueError("No uniquely identified interrupted synthetic session")
                identity = matches[0]["id"]
                limits = ({"max_cycles": matches[0]["cycles"] + 1,
                           "max_requests": max(matches[0]["spec"]["max_requests"] + 1, matches[0]["requests"] + (1 if args.checkpoint_only else max(1, guard.state["request_ceiling"] - guard.state["requests"]))),
                           "max_token_estimate": max(matches[0]["spec"]["max_token_estimate"], matches[0]["token_estimate"] + int((guard.state["ceiling_inr"] - guard.state["reserved_inr_estimate"]) * 1_000_000 / 396))}
                          if args.resume_saved else {"max_cycles": 5 if args.finalize_mixed else 2, "max_requests": 15 if args.finalize_mixed else 9})
                store.action({"action": "extend", "id": identity, **limits, **({"max_token_estimate": 2_000_000} if args.finalize_mixed else {})})
                store.action({"action": "feedback", "id": identity, "text": "Inspect the saved tool results and current files before continuing. Retain all completed work. Finish missing work, then serialize the checkpoint; do not repeat completed tests or reads if their evidence is current."})
                store.action({"action": "resume", "id": identity})
            else:
                identity = store.action(spec)
            timer = threading.Timer(180, lambda identity=identity: store.action({"action": "pause", "id": identity}))
            timer.daemon = True; timer.start()
            started = time.monotonic()
            try: goal = execute_cycle(store, store.claim(identity), endpoint=endpoint, provider_guard=guard.wrap, checkpoint_only=args.checkpoint_only)
            finally: timer.cancel()
            result = {"case": name, "state": goal["state"], "requests": goal["requests"],
                      "elapsed_seconds": round(time.monotonic() - started, 1), "outcome": goal["checkpoint"].get("outcome"),
                      "summary": goal["checkpoint"].get("summary"), "detail": goal["detail"]}
            if original_tests:
                previous = previous_cases.get(name, {})
                recorded = next((item for item in (previous.get("outcome") or {}).get("outputs", []) if item.get("path") == "calculator.py"), {})
                unchanged = ((workspace / "test_calculator.py").read_bytes() == original_tests
                             and hashlib.sha256((workspace / "calculator.py").read_bytes()).hexdigest() == recorded.get("sha256"))
                if previous.get("independent_tests_passed") and previous.get("tests_unchanged") and unchanged:
                    result.update(independent_tests_passed=True, tests_unchanged=True, independent_check="Retained prior passing tests; verified unchanged file hashes; no tests rerun")
                else:
                    result.update(independent_coding_check(workspace, original_tests))
            result["passed"] = (result["state"] == "ready_for_review" and result.get("independent_tests_passed", True)
                                and result.get("tests_unchanged", True) and bool((result["outcome"] or {}).get("passed")))
            report["cases"].append(result)
            report["budget"] = guard.state
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({"case": name, "passed": result["passed"], "requests": result["requests"]}), flush=True)
    except Exception as error:
        report["failure_type"] = type(error).__name__
    finally:
        for name, value in original.items():
            if value is None: os.environ.pop(name, None)
            else: os.environ[name] = value
        report["budget"] = guard.state
        report["passed"] = len(report["cases"]) == len(args.cases) and all(c["passed"] for c in report["cases"])
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__": raise SystemExit(main())
