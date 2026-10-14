"""Actual MXC file-patch probe with a declared local scripted provider.

No model quality claim, paid inference, permission grants or user files.
The patch and filesystem execution are real; unexpected requests are denied.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import uuid
from scripts.native_session import NativeSession
from smara.native_provider import ChatEndpoint, ResponsesAdapter
from smara.native_runtime import native_binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.report.resolve().is_relative_to(root / "build"):
        raise ValueError("Retain reports under ignored build")
    if args.report.exists():
        raise ValueError("Choose a new report path; previous failures must be retained")
    fixture = root / "build" / ("native-patch-" + uuid.uuid4().hex)
    workspace = fixture / "workspace"
    workspace.mkdir(parents=True)
    target = workspace / "patch.txt"
    target.write_text("original\n", encoding="utf-8")
    calls, approvals, errors, tool_results = [], [], [], []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(1)
                if len(calls) == 1:
                    tool = next(t["function"]["name"] for t in request["tools"] if t["function"]["name"].endswith("apply_patch"))
                    patch = "*** Begin Patch\n*** Update File: patch.txt\n@@\n-original\n+fixed\n*** End Patch"
                    delta = {"tool_calls": [{"index": 0, "id": "synthetic-patch", "type": "function", "function": {"name": tool, "arguments": json.dumps({"input": patch})}}]}
                    finish = "tool_calls"
                elif len(calls) == 2:
                    # This provider only handles the synthetic fixture above.
                    # Retain its actual native failure, not unrelated logs.
                    tool_results.extend(str(message.get("content", ""))[:2000]
                                        for message in request.get("messages", [])
                                        if message.get("role") == "tool")
                    delta, finish = {"content": "Declared offline patch fixture finished; not an AI answer."}, "stop"
                else:
                    raise ValueError("Two local requests only")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n").encode())
            except Exception as error:
                errors.append(type(error).__name__)
                self.send_error(500)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    session = None
    def deny(message):
        approvals.append({"method": message["method"], "reason": (message.get("params") or {}).get("reason")})
        return {"decision": "decline"}
    report = {"status": "failed", "fixture": str(fixture), "paid_requests": 0, "scripted_provider": True,
              "native_sha256": hashlib.sha256(native_binary().read_bytes()).hexdigest()}
    try:
        with ResponsesAdapter(ChatEndpoint(f"http://127.0.0.1:{server.server_port}/v1", "offline-patch", "")) as adapter:
            session = NativeSession(adapter, workspace, fixture / "home", request_handler=deny)
            session.initialize()
            thread = session.rpc("thread/start", {"cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})["thread"]["id"]
            session.rpc("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "Synthetic local patch acceptance only", "text_elements": []}]})
            turn = session.wait(lambda message: message.get("method") == "turn/completed")["params"]["turn"]
            history = session.rpc("thread/read", {"threadId": thread, "includeTurns": True})["thread"]
            patches = [item for t in history["turns"] for item in t["items"] if item["type"] == "fileChange"]
            checks = {"native_turn_completed": turn["status"] == "completed", "actual_file_changed": target.read_text() == "fixed\n",
                      "native_patch_completed": len(patches) == 1 and patches[0]["status"] == "completed", "no_widening_requested": not approvals, "no_provider_errors": not errors}
            report.update(status="passed" if all(checks.values()) else "failed", checks=checks, patches=patches)
    except Exception as error:
        report["error_type"] = type(error).__name__
    finally:
        if session: session.close()
        server.shutdown(); server.server_close()
        report.update(approvals=approvals, provider_requests=len(calls), provider_errors=errors,
                      synthetic_tool_results=tool_results)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as output:
            output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__": raise SystemExit(main())
