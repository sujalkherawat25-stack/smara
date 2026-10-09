"""Real-model, bounded coding acceptance on disposable synthetic projects.

The model chooses its tools and patch. Native code executes them. Assertions
check unchanged tests and actual execution, not merely the model's claims.
No private repository source, user documents or memory are uploaded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid

from smara.cli import _load_local_profiles, _resolve_profile_key
from smara.native_provider import ChatEndpoint, ResponsesAdapter
from smara.native_runtime import launch_options, native_binary


class BoundedCodingClient:
    def __init__(self, client, ceiling=8):
        self.client = client
        self.ceiling = ceiling
        self.requests = 0
        self.tool_calls = []
        self.tool_catalog = set()
        self.history_replays = 0
        self._lock = threading.Lock()

    def stream(self, method, url, **kwargs):
        payload = kwargs["json"]
        with self._lock:
            if self.requests >= self.ceiling:
                raise RuntimeError("Coding acceptance request ceiling reached")
            self.requests += 1
            self.tool_catalog.update(tool["function"]["name"] for tool in payload.get("tools", []))
            for message in payload["messages"]:
                if message["role"] == "tool":
                    self.history_replays += 1
                for call in message.get("tool_calls", []):
                    name = call["function"]["name"]
                    entry = (call["id"], name)
                    if entry not in self.tool_calls:
                        self.tool_calls.append(entry)
        payload["max_tokens"] = min(payload.get("max_tokens", 2048), 2048)
        return self.client.stream(method, url, **kwargs)

    def close(self):
        self.client.close()


def coding_prompt(python: str) -> str:
    return (
        "Fix the bug in calculator.py in this disposable synthetic project. "
        "Inspect the project and reproduce its failing unittest tests before changing code, "
        "then make a minimal implementation fix and rerun the tests. Do not change the tests. "
        f"Python is available at {python}. This is a Windows PowerShell environment. "
        "Only work inside this project. Do not install packages, access other projects, "
        "contact external services, ask for elevated execution or use Git. "
        "Use the provided native tools; report the actual verification result."
    )


def cleanup_scratch(scratch: Path, build: Path):
    target = scratch.resolve()
    if not target.is_relative_to(build.resolve()) or not target.name.startswith("native-coding-"):
        raise ValueError("Refusing cleanup outside the exact coding scratch boundary")
    # Windows can briefly retain handles after a native process exits. Retry
    # only the same owned scratch tree; never kill unrelated processes.
    def remove_readonly(function, failed_path, _error):
        path = Path(failed_path)
        if not path.resolve().is_relative_to(target) or not path.is_file():
            raise OSError("Cleanup refused outside the owned scratch tree")
        # Git object files are intentionally read-only on Windows. Clear only
        # that file attribute; never change ACLs or traverse an outside link.
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
        function(failed_path)
    for attempt in range(4):
        try:
            shutil.rmtree(target, onerror=remove_readonly)
            return {"cleanup": "removed"}
        except OSError as exc:
            if attempt == 3:
                return {"cleanup": "retained", "cleanup_error": str(exc), "synthetic_failure_workspace": str(target)}
            time.sleep(.2 * (attempt + 1))


def parse_cli_output(result):
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    items = [event["item"] for event in events if event.get("type") == "item.completed"]
    return items, any(event.get("type") == "turn.completed" for event in events), [
        event for event in events if event.get("type") in {"error", "turn.failed"}
    ]


def run_cli(adapter, workspace, home, prompt, timeout):
    options, environment = launch_options(adapter, home=home, workspace=workspace)
    result = subprocess.run([str(native_binary()), *options, "--no-daemon", "exec", "--skip-git-repo-check", "--json", prompt],
        cwd=workspace, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    items, completed, failures = parse_cli_output(result)
    return {"exit_code": result.returncode, "completed": completed, "items": items, "failures": failures, "resume": False}


def run_desktop(adapter, workspace, home, prompt, timeout):
    # This is the native app-server protocol used by Desktop;
    # it does not pretend to be visual Tauri acceptance.
    options, environment = launch_options(adapter, home=home, workspace=workspace)
    process = subprocess.Popen([str(native_binary()), *options, "app-server", "--listen", "stdio://"],
        cwd=workspace, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    messages = queue.Queue()
    items, failures, turns = [], [], []
    denied = []
    result = None
    deadline = time.monotonic() + timeout

    def read():
        try:
            for line in process.stdout:
                messages.put(json.loads(line))
        finally:
            messages.put({"transport_exited": True})

    threading.Thread(target=read, daemon=True).start()
    def drain_errors():
        for _line in process.stderr:
            pass
    threading.Thread(target=drain_errors, daemon=True).start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def observe(message):
        params = message.get("params", {})
        if message.get("id") is not None and message.get("method"):
            if message["method"] in {"item/fileChange/requestApproval", "item/commandExecution/requestApproval"}:
                # No approval grant is needed in the normal scratch workspace.
                # Never weaken native permissions to make a benchmark pass.
                denied.append(message["method"])
                send({"id": message["id"], "result": {"decision": "decline"}})
            else:
                raise RuntimeError("Unexpected native client request: " + message["method"])
        elif message.get("method") == "item/completed":
            items.append(params["item"])
        elif message.get("method") == "turn/completed":
            turns.append(params["turn"])
        elif message.get("method") == "error":
            failures.append(params)

    def next_message():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Coding protocol deadline reached")
        message = messages.get(timeout=remaining)
        if message.get("transport_exited"):
            raise RuntimeError("Native protocol exited before completion")
        return message

    def rpc(identifier, method, params):
        send({"id": identifier, "method": method, "params": params})
        while True:
            message = next_message()
            if message.get("id") == identifier and not message.get("method"):
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]
            observe(message)

    try:
        rpc(1, "initialize", {"clientInfo": {"name": "smara_live_coding_acceptance", "version": "0.1.8"}, "capabilities": None})
        send({"method": "initialized"})
        thread = rpc(2, "thread/start", {"cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})
        rpc(3, "turn/start", {"threadId": thread["thread"]["id"], "input": [{"type": "text", "text": prompt, "text_elements": []}]})
        while not turns:
            observe(next_message())
        resumed = rpc(4, "thread/resume", {"threadId": thread["thread"]["id"], "cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})
        replay = resumed["thread"].get("turns", [])
        result = {"completed": turns[-1]["status"] == "completed", "items": items,
            "thread_id": thread["thread"]["id"],
            "failures": failures, "resume": bool(replay), "denied_requests": denied}
        return result
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if result is not None:
            result["exit_code"] = process.returncode


def resume_after_restart(adapter, workspace, home, thread_id, expected_item_ids):
    """Fresh native process, existing rollout, no new turn or inference."""
    options, environment = launch_options(adapter, home=home, workspace=workspace)
    process = subprocess.Popen([str(native_binary()), *options, "app-server", "--listen", "stdio://"],
        cwd=workspace, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    requests = [
        {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "smara_restart_acceptance", "version": "0.1.8"}, "capabilities": None}},
        {"method": "initialized"},
        {"id": 2, "method": "thread/resume", "params": {"threadId": thread_id, "cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"}},
    ]
    messages = queue.Queue()
    def read():
        try:
            for line in process.stdout:
                messages.put(json.loads(line))
        finally:
            messages.put({"transport_exited": True})
    def drain():
        for _line in process.stderr:
            pass
    threading.Thread(target=read, daemon=True).start()
    threading.Thread(target=drain, daemon=True).start()
    replay = None
    try:
        # Handshake must settle before subsequent RPCs are sent.
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            if "id" not in request:
                continue
            deadline = time.monotonic() + 45
            while True:
                message = messages.get(timeout=max(.01, deadline - time.monotonic()))
                if message.get("transport_exited"):
                    raise RuntimeError("Restarted native transport exited")
                if message.get("id") == request["id"] and not message.get("method"):
                    if message.get("error"):
                        raise RuntimeError(str(message["error"]))
                    if request["id"] == 2:
                        replay = message["result"]["thread"]
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Restarted session did not resume")
        turns = replay.get("turns", [])
        actual_ids = {item["id"] for turn in turns for item in turn.get("items", [])}
        return bool(turns) and expected_item_ids.issubset(actual_ids) and replay["id"] == thread_id
    finally:
        if process.poll() is None:
            process.stdin.close()
            try: process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def verify_result(result, workspace, original_tests, bounded):
    command_items = [item for item in result["items"] if item.get("type") in {"command_execution", "commandExecution"}]
    codes = [item.get("exit_code", item.get("exitCode")) for item in command_items]
    outputs = [str(item.get("aggregated_output", item.get("aggregatedOutput", ""))) for item in command_items]
    unchanged = (workspace / "test_calculator.py").read_bytes() == original_tests
    actual = subprocess.run([sys.executable, "-m", "unittest", "-v"], cwd=workspace,
        capture_output=True, text=True, timeout=30)
    independent = subprocess.run([sys.executable, "-c",
        "from calculator import clamp; "
        "cases=[(-9,-5,5),(0,-5,5),(9,-5,5),(2.25,1.5,4.0),(10,10,10)]; "
        "assert all(clamp(v,lo,hi)==max(lo,min(v,hi)) for v,lo,hi in cases)"],
        cwd=workspace, capture_output=True, text=True, timeout=30)
    checks = {
        "native_turn_completed": result["completed"] and result["exit_code"] == 0,
        "tests_unchanged": unchanged,
        "agent_observed_failing_tests": any(code not in (0, None) and "FAILED" in output for code, output in zip(codes, outputs)),
        "agent_reran_passing_tests": any(code == 0 and "Ran 5 tests" in output and "OK" in output for code, output in zip(codes, outputs)),
        "independent_test_run_passed": actual.returncode == 0 and "Ran 5 tests" in actual.stderr,
        "independent_extra_cases_passed": independent.returncode == 0,
        "model_received_actual_tool_history": bounded.history_replays > 0,
        "no_native_errors": not result["failures"],
    }
    if "resume_after_restart" in result:
        checks["session_history_survived_process_restart"] = result["resume_after_restart"]
        checks["resume_did_not_repeat_inference"] = result["restart_no_inference"]
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
        "provider_requests": bounded.requests, "request_ceiling": bounded.ceiling,
        "max_output_tokens_per_request": 2048, "native_commands": len(command_items),
        "command_exit_codes": codes, "model_tool_choices": [name for _identity, name in bounded.tool_calls],
        "available_native_tools": sorted(bounded.tool_catalog),
        "session_resume": result["resume"], "denied_requests": result.get("denied_requests", []),
        "test_sha256": hashlib.sha256(original_tests).hexdigest(), "private_code_sent": False,
        "scripted_provider": False, "visual_desktop_test": False, "failures": result["failures"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("cli", "desktop", "both"), default="both")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    profiles, active, credentials = _load_local_profiles()
    profile = next(item for item in profiles if item.get("id") == active)
    if profile.get("id") != "sarvam_glm":
        raise SystemExit("Select Sarvam GLM explicitly; this test will not substitute a provider")
    key = _resolve_profile_key(profile, credentials)
    if not key:
        raise SystemExit("Configured model credential unavailable")
    endpoint = ChatEndpoint(profile["base_url"], profile["model"], key, profile.get("auth_header", "authorization"))
    results = []
    for mode in ("cli", "desktop") if args.mode == "both" else (args.mode,):
        scratch = (root / "build" / ("native-coding-" + uuid.uuid4().hex)).resolve()
        assert scratch.is_relative_to((root / "build").resolve())
        scratch.mkdir()
        workspace = scratch / "workspace"
        workspace.mkdir()
        for filename in ("calculator.py", "test_calculator.py"):
            fixture_name = filename + ".txt" if filename.startswith("test_") else filename
            shutil.copy2(root / "tests" / "fixtures" / "native_coding" / fixture_name, workspace / filename)
        original_tests = (workspace / "test_calculator.py").read_bytes()
        started = time.monotonic()
        with ResponsesAdapter(endpoint) as adapter:
            bounded = BoundedCodingClient(adapter.client)
            adapter.client = bounded
            try:
                runner = run_cli if mode == "cli" else run_desktop
                result = runner(adapter, workspace, scratch / "home", coding_prompt(sys.executable), args.timeout)
                if mode == "desktop" and result["completed"]:
                    request_count = bounded.requests
                    result["resume_after_restart"] = resume_after_restart(adapter, workspace, scratch / "home", result["thread_id"], {item["id"] for item in result["items"]})
                    result["restart_no_inference"] = bounded.requests == request_count
                report = verify_result(result, workspace, original_tests, bounded)
            except (RuntimeError, TimeoutError, subprocess.TimeoutExpired, queue.Empty) as exc:
                report = {"status": "failed", "failure": type(exc).__name__ + ": " + str(exc), "provider_requests": bounded.requests}
        report.update(mode=mode, provider=profile["id"], model=profile["model"], elapsed_seconds=round(time.monotonic() - started, 2))
        results.append(report)
        print(json.dumps(report), flush=True)
        # Retain failed synthetic workspaces for diagnosis, delete only this
        # exact newly created validated scratch tree on success.
        if report["status"] == "passed":
            report.update(cleanup_scratch(scratch, root / "build"))
            if report["cleanup"] == "retained":
                print(json.dumps({"cleanup": report["cleanup"], "synthetic_failure_workspace": str(scratch)}), flush=True)
        else:
            print(json.dumps({"synthetic_failure_workspace": str(scratch)}), flush=True)
    if args.report:
        report_path = args.report.resolve()
        if not report_path.is_relative_to(root / "build"):
            raise SystemExit("Acceptance reports must stay under repository build/")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"results": results}, indent=2) + "\n", encoding="utf-8")
    return 0 if all(result["status"] == "passed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
