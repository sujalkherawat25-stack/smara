"""Real native loop + clock + one tool-free format repair; local scripted provider.

This is a protocol/recovery gate, not a live-model quality evaluation.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from smara.native_goals import GoalStore
from smara.native_goal_runtime import execute_cycle
from smara.native_provider import ChatEndpoint


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    root = parser.parse_args(argv).out.resolve(); root.mkdir(parents=True, exist_ok=False)
    workspace = root / "project"; workspace.mkdir()
    os.environ.update(SMARA_NATIVE_HOME=str(root / "runtime"), SMARA_DESKTOP_STATE=str(root / "desktop.json"), SMARA_DESKTOP_CREDENTIALS=str(root / "credentials.json"))
    requests, errors = [], []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); requests.append(payload)
                index = len(requests)
                if index == 1:
                    name = next(t["function"]["name"] for t in payload["tools"] if t["function"]["name"].endswith("current_time"))
                    delta = {"tool_calls": [{"index": 0, "id": "synthetic-clock-call", "type": "function", "function": {"name": name, "arguments": "{}"}}]}
                    finish = "tool_calls"
                elif index == 2:
                    delta, finish = {"content": "The real native clock was read. This deliberately lacks checkpoint JSON."}, "stop"
                elif index == 3:
                    assert "tools" not in payload and "tool_choice" not in payload
                    assert payload["response_format"]["type"] == "json_schema"
                    delta, finish = {"content": json.dumps({"decision": "ready_for_review", "summary": "Real native clock result retained",
                        "plan": [], "completed_steps": ["Read native clock"], "next_step": "User review", "evidence": ["Native current_time result in transcript"],
                        "outputs": [], "claims": [], "coverage": []})}, "stop"
                else: raise ValueError("Unexpected inference beyond the bounded repair")
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Connection", "close"); self.end_headers()
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n").encode()); self.wfile.flush()
            except Exception as exc:
                errors.append(type(exc).__name__)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    store = GoalStore()
    identity = store.action({"action": "create", "workspace": str(workspace), "profile_id": "synthetic", "objective": "Synthetic actual-clock checkpoint protocol fixture",
        "success_criteria": "Retain actual native observation and format without replay", "max_requests": 4, "cycle_seconds": 60, "interval_seconds": 0})
    try:
        goal = execute_cycle(store, store.claim(), endpoint=ChatEndpoint(f"http://127.0.0.1:{server.server_port}", "synthetic", ""))
        checks = {"ready_for_review": goal["state"] == "ready_for_review", "one_bounded_repair": len(requests) == 3,
            "repair_has_no_tools": len(requests) == 3 and "tools" not in requests[-1],
            "native_clock_returned": any(m.get("role") == "tool" and "utc" in str(m.get("content", "")) for request in requests for m in request["messages"]),
            "same_goal_budget": goal["requests"] == 3, "no_fixture_error": not errors}
        report = {"mode": "scripted_local_provider_real_native_runtime", "passed": all(checks.values()), "checks": checks, "detail": goal["detail"]}
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        return 0 if report["passed"] else 1
    finally:
        server.shutdown(); server.server_close()


if __name__ == "__main__": raise SystemExit(main())
