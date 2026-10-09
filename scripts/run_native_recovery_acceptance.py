"""Fault-injection: actual native Stop/crash and real child-process cleanup.

The model stream is a declared synthetic fixture, NOT model-quality evidence.
Native executes a real bounded PowerShell command. No tool result is mocked.
Only owned processes and disposable synthetic workspaces are touched.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import uuid
from smara.native_provider import ChatEndpoint, ResponsesAdapter
from scripts.native_session import NativeSession
from scripts.run_native_coding_acceptance import cleanup_scratch


def exercise(root, crash):
    scratch = root / "build" / ("native-coding-recovery-" + uuid.uuid4().hex)
    workspace = scratch / "workspace"
    workspace.mkdir(parents=True)
    marker = workspace / "must-not-appear.txt"
    requests = []
    provider_errors = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                if len(requests) == 1:
                    tools = [entry["function"] for entry in payload["tools"] if entry["function"]["name"].endswith("exec_command")]
                    assert len(tools) == 1
                    path = str(marker).replace("'", "''")
                    arguments = {"cmd": f"Write-Output 'NATIVE_ACTIVE'; Start-Sleep -Seconds 8; Set-Content -LiteralPath '{path}' -Value 'cleanup failed'", "yield_time_ms": 10000, "max_output_tokens": 512}
                    delta = {"tool_calls": [{"index": 0, "id": "synthetic-recovery-command", "type": "function", "function": {"name": tools[0]["name"], "arguments": json.dumps(arguments)}}]}
                    finish = "tool_calls"
                else:
                    delta, finish = {"content": "Synthetic fault-injection fixture; not real-model evidence."}, "stop"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
            except Exception as exc:
                provider_errors.append(type(exc).__name__)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    report = {"kind": "crash" if crash else "stop", "status": "failed", "synthetic_provider": True}
    session = None
    try:
        with ResponsesAdapter(ChatEndpoint(f"http://127.0.0.1:{provider.server_port}", "synthetic-recovery", "")) as adapter:
            session = NativeSession(adapter, workspace, scratch / "home", timeout=40)
            session.initialize()
            thread = session.rpc("thread/start", {"cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})["thread"]["id"]
            turn = session.rpc("turn/start", {"threadId": thread, "input": [{"type": "text", "text": "Synthetic cancellation fixture in a disposable workspace.", "text_elements": []}]})["turn"]["id"]
            session.wait(lambda event: event.get("method") == "item/commandExecution/outputDelta" and "NATIVE_ACTIVE" in event.get("params", {}).get("delta", ""))
            if crash:
                session.close(crash=True)
            else:
                session.rpc("turn/interrupt", {"threadId": thread, "turnId": turn})
                terminal = session.wait(lambda event: event.get("method") == "turn/completed")
                report["terminal_status"] = terminal["params"]["turn"]["status"]
                session.close()
            before = len(requests)
            session = NativeSession(adapter, workspace, scratch / "home", timeout=40)
            session.initialize()
            restored = session.rpc("thread/resume", {"threadId": thread, "cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})["thread"]
            # Allow longer than the command's delayed side effect, proving that
            # Stop/parent death didn't merely detach a still-running child.
            time.sleep(8.5)
            report["checks"] = {"saved_thread_resumed": restored["id"] == thread and bool(restored["turns"]),
                "no_tool_action_replayed": len(requests) == before,
                "delayed_child_write_prevented": not marker.exists(),
                "not_falsely_completed": all(t["status"] != "completed" for t in restored["turns"]),
                "no_provider_fixture_errors": not provider_errors}
            if not crash:
                report["checks"]["interrupt_reported"] = report["terminal_status"] == "interrupted"
            report["status"] = "passed" if all(report["checks"].values()) else "failed"
            report["provider_requests"] = len(requests)
    except Exception as exc:
        report.update(failure=type(exc).__name__, details=str(exc)[:1200])
    finally:
        if session:
            session.close()
        provider.shutdown()
        provider.server_close()
    if report["status"] == "passed":
        report.update(cleanup_scratch(scratch, root / "build"))
    else:
        report["retained_synthetic_workspace"] = str(scratch)
    return report


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=root / "build/native-recovery-2026-10-09.json")
    args = parser.parse_args()
    target = args.report.resolve()
    if not target.is_relative_to((root / "build").resolve()):
        raise SystemExit("Reports must stay under build")
    reports = [exercise(root, False), exercise(root, True)]
    report = {"status": "passed" if all(item["status"] == "passed" for item in reports) else "failed", "runs": reports}
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
