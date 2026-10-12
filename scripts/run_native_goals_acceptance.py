"""Exercise real native goal sessions against a scripted local provider.

This verifies native tool execution, cold continuation and ledger controls.
It does not assess paid model quality or live Telegram delivery.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from smara.native_goals import GoalStore
from smara.native_goal_runtime import execute_cycle
from smara.native_provider import ChatEndpoint


def service_lifecycle(store):
    from smara.native_goal_runtime import service_control
    # All fixture goals are idle; check supervisor ownership without inference.
    service_control(store, {"action": "start_service"})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not store.service().get("running"):
        time.sleep(.1)
    started = store.service().get("running")
    service_control(store, {"action": "stop_service"})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and store.service().get("running"):
        time.sleep(.1)
    return {"background_supervisor_started": bool(started), "background_supervisor_stopped": not store.service().get("running")}


def approval_roundtrip(store, workspace, *, pause=False):
    observed, errors = [], []
    marker = workspace / ("paused-goal-command.txt" if pause else "declined-goal-command.txt")
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observed.append(payload)
                if len(observed) == 1:
                    tool = next(entry["function"]["name"] for entry in payload["tools"] if entry["function"]["name"].endswith("exec_command"))
                    arguments = {"cmd": "Set-Content -LiteralPath '" + str(marker).replace("'", "''") + "' -Value 'must not execute'", "sandbox_permissions": "require_escalated", "justification": "Synthetic goal approval-denial transport fixture"}
                    delta = {"tool_calls": [{"index": 0, "id": "synthetic-denied-goal", "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}]}
                    finish = "tool_calls"
                else:
                    delta = {"content": json.dumps({"decision": "ready_for_review", "summary": "Synthetic denial inspected", "plan": ["Request command", "Inspect denial"], "completed_steps": ["Inspect denial"], "next_step": "User review", "evidence": ["Native tool rejection observed"], "outputs": []})}
                    finish = "stop"
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Connection", "close"); self.end_headers()
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n").encode()); self.wfile.flush()
            except Exception as exc:
                errors.append(type(exc).__name__)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    identity = store.action({"action": "create", "workspace": str(workspace), "profile_id": "synthetic", "sandbox": "workspace-write",
        "objective": "Synthetic native approval rejection fixture", "success_criteria": "Observe rejection and ensure the denied command did not execute", "interval_seconds": 0, "max_requests": 3, "max_token_estimate": 1_000_000})
    goal = store.claim()
    result = []
    thread = threading.Thread(target=lambda: result.append(execute_cycle(store, goal, endpoint=ChatEndpoint(f"http://127.0.0.1:{server.server_port}", "synthetic-approval-provider", ""))))
    thread.start()
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            snapshot = store.get(identity)
            if snapshot["approvals"]:
                request = snapshot["approvals"][0]
                assert snapshot["state"] == "awaiting_approval"
                # No GUI security control is clicked and no permission granted.
                if pause:
                    store.action({"action": "pause", "id": identity})
                else:
                    store.respond(identity, request["id"], False)
                break
            if not thread.is_alive():
                raise RuntimeError("Goal ended before requesting native approval: " + snapshot["detail"])
            time.sleep(.1)
        else:
            raise TimeoutError("Native approval was not recorded")
        thread.join(timeout=30)
        if thread.is_alive():
            raise TimeoutError("Goal did not handle its pause or scoped Deny response")
        if pause:
            stopped = result[0]
            checks = {"native_cycle_pause_releases_session": stopped["state"] == "paused" and stopped["lease"] is None,
                "pause_did_not_execute_pending_command": not marker.exists(),
                "paused_approval_expired": not stopped["approvals"]}
            store.action({"action": "resume", "id": identity})
            resumed = execute_cycle(store, store.claim(), endpoint=ChatEndpoint(f"http://127.0.0.1:{server.server_port}", "synthetic-approval-provider", ""))
            checks["explicit_resume_reuses_paused_session"] = resumed["state"] == "ready_for_review" and resumed["session_id"] == stopped["session_id"]
            checks["pause_fixture_valid"] = not errors and len(observed) == 2
            return checks
        return {"approval_persisted_and_delivered": bool(result and result[0]["state"] == "ready_for_review"),
            "denied_command_not_executed": not marker.exists(),
            "denial_in_native_tool_history": any(message.get("role") == "tool" and any(word in str(message.get("content", "")).lower() for word in ("reject", "declin", "denied")) for request in observed for message in request["messages"]),
            "approval_fixture_valid": len(observed) == 2 and not errors}
    finally:
        if thread.is_alive():
            store.action({"action": "stop", "id": identity})
            thread.join(timeout=50)
        server.shutdown(); server.server_close()


def main():
    root = Path(__file__).resolve().parents[1]
    scratch = Path(tempfile.mkdtemp(prefix="native-goals-", dir=root / "build"))
    workspace = scratch / "project"
    workspace.mkdir()
    os.environ["SMARA_NATIVE_HOME"] = str(scratch / "home")
    os.environ["SMARA_DESKTOP_STATE"] = str(scratch / "desktop.json")
    os.environ["SMARA_DESKTOP_CREDENTIALS"] = str(scratch / "credentials.json")
    observed, errors = [], []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observed.append(payload)
                index = len(observed)
                if index == 1:
                    tool = next(entry["function"]["name"] for entry in payload["tools"] if entry["function"]["name"].endswith("current_time"))
                    delta = {"tool_calls": [{"index": 0, "id": "synthetic-clock", "type": "function", "function": {"name": tool, "arguments": "{}"}}]}
                    finish = "tool_calls"
                else:
                    checkpoint = {"decision": "continue" if index == 2 else "ready_for_review",
                        "summary": "Synthetic clock observation recorded" if index == 2 else "Synthetic continuation checked",
                        "plan": ["Read clock", "Review saved observation"], "completed_steps": ["Read clock"] if index == 2 else ["Read clock", "Review saved observation"],
                        "next_step": "Review saved observation" if index == 2 else "User review",
                        "evidence": ["Native clock tool returned a timestamp"], "outputs": []}
                    delta, finish = {"content": json.dumps(checkpoint)}, "stop"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
            except Exception as exc:
                errors.append(type(exc).__name__)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    store = GoalStore(scratch / "home" / "native-goals.sqlite")
    identity = store.action({"action": "create", "workspace": str(workspace), "profile_id": "synthetic",
        "objective": "Synthetic clock checkpoint and cold continuation fixture", "success_criteria": "Read the native clock then resume the saved session", "interval_seconds": 0,
        "max_cycles": 2, "max_requests": 5, "max_token_estimate": 1_000_000})
    endpoint = ChatEndpoint(f"http://127.0.0.1:{server.server_port}", "synthetic-goal-provider", "")
    report = {"synthetic_provider": True, "scratch": str(scratch)}
    try:
        first = execute_cycle(store, store.claim(), endpoint=endpoint)
        if first["state"] != "queued":
            raise RuntimeError("First cycle did not checkpoint: " + first["detail"])
        # A new app server and provider adapter are created by every cycle.
        restarted_store = GoalStore(store.path)
        restarted_store.action({"action": "feedback", "id": identity, "text": "Synthetic feedback: inspect the saved observation."})
        second = execute_cycle(restarted_store, restarted_store.claim(), endpoint=endpoint)
        report["checks"] = {
            "same_native_session": first["session_id"] == second["session_id"] and bool(first["session_id"]),
            "real_clock_tool_returned": any(message.get("role") == "tool" and "local" in str(message.get("content", "")).lower() for request in observed for message in request["messages"]),
            "cold_continuation": second["cycles"] == 2,
            "feedback_delivered": any("Synthetic feedback:" in str(request["messages"]) for request in observed[2:]),
            "model_success_requires_review": second["state"] == "ready_for_review",
            "cumulative_requests": second["requests"] == len(observed) == 3,
            "no_fixture_errors": not errors,
        }
        restarted_store.action({"action": "accept", "id": identity})
        report["checks"]["user_acceptance_completes"] = restarted_store.get(identity)["state"] == "completed"
        report["checks"].update(approval_roundtrip(store, workspace))
        report["checks"].update(approval_roundtrip(store, workspace, pause=True))
        report["checks"].update(service_lifecycle(store))
        report["status"] = "passed" if all(report["checks"].values()) else "failed"
    except Exception as exc:
        report.update(status="failed", failure=type(exc).__name__, detail=str(exc))
    finally:
        server.shutdown(); server.server_close()
    target = root / "build/native-goals-acceptance.json"
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
