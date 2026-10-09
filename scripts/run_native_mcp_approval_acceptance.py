"""Native MCP action approval roundtrip using Desktop's actual response helper.

Scripted provider (not AI quality); real read-only MCP clock and copied Rust
permissions. No paid calls, private files, browser actions or general grants.
This does not click Desktop security controls or replace human UI acceptance.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import threading

from smara.native_provider import ChatEndpoint, ResponsesAdapter
from scripts.native_session import NativeSession


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=root / "build/native-mcp-approval-2026-10-09.json")
    target = parser.parse_args().report.resolve()
    if not target.is_relative_to((root / "build").resolve()):
        raise ValueError("Reports must stay inside build")
    requests, errors, approvals, results = [], [], [], []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                assert self.path == "/v1/chat/completions"
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(request)
                position = len(requests)
                assert 1 <= position <= 6, "Unexpected inference retry"
                if position in (1, 3, 5):
                    tools = [tool["function"] for tool in request["tools"] if tool["function"]["name"].endswith("current_time")]
                    assert len(tools) == 1, "MCP clock absent/ambiguous"
                    delta = {"tool_calls": [{"index": 0, "id": f"clock-{position}", "function": {"name": tools[0]["name"], "arguments": "{}"}}]}
                    finish = "tool_calls"
                else:
                    results.append([message["content"] for message in request["messages"] if message["role"] == "tool"][-1])
                    delta, finish = {"content": "Offline MCP approval protocol fixture finished; not an AI answer."}, "stop"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
            except Exception as exc:
                errors.append(str(exc))
                self.send_error(500)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    report = {"status": "failed", "paid_provider_requests": 0, "gui_security_controls_clicked": False,
              "scope": "scripted provider, actual native MCP permissions and Desktop response helper"}
    session = None
    try:
        with tempfile.TemporaryDirectory(prefix="native-mcp-approval-", dir=root / "build") as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            home = Path(temporary) / "home"
            home.mkdir()
            (home / "config.toml").write_text('[mcp_servers.smara_readers]\ndefault_tools_approval_mode = "prompt"\n', encoding="utf-8")

            def decide(message):
                assert message["method"] == "mcpServer/elicitation/request", "Unexpected authorization type"
                params = message["params"]
                assert params["serverName"] == "smara_readers" and params["_meta"]["tool_params"] == {}, "Unexpected MCP tool scope"
                assert "current_time" in params["message"], "Unexpected MCP action"
                assert len(approvals) < 3, "Unexpected permission retry"
                allow = len(approvals) == 1
                # Exercise the maintained Desktop helper with native-generated
                # parameters; never synthesize a grant for another tool/server.
                code = "import fs from 'node:fs'; import vm from 'node:vm'; import ts from 'typescript'; const m={exports:{}}; const c=ts.transpileModule(fs.readFileSync('src/nativeView.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText; vm.runInNewContext(c,{module:m,exports:m.exports}); const p=JSON.parse(fs.readFileSync(0,'utf8')); if(!m.exports.supportsApproval('mcpServer/elicitation/request',p.params)) throw Error('Desktop does not recognize native confirmation'); process.stdout.write(JSON.stringify(m.exports.approvalResponse('mcpServer/elicitation/request',p.params,p.allow)));"
                frontend = subprocess.run(["node", "--input-type=module", "-e", code], input=json.dumps({"params": params, "allow": allow}),
                                          cwd=root / "apps/desktop", capture_output=True, text=True, encoding="utf-8", timeout=30)
                assert frontend.returncode == 0, frontend.stderr
                result = json.loads(frontend.stdout)
                assert result["_meta"] is None, "Persistent permission unexpectedly granted"
                approvals.append({"native_params": params, "desktop_response": result})
                return result

            with ResponsesAdapter(ChatEndpoint(f"http://127.0.0.1:{provider.server_port}/v1", "offline-mcp-protocol", "")) as adapter:
                try:
                    session = NativeSession(adapter, workspace, home, tools_enabled=True, timeout=60, request_handler=decide)
                    session.initialize()
                    thread = session.rpc("thread/start", {"cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})["thread"]["id"]
                    for instruction in ("Offline fixture: request the local clock; the client will deny this call.",
                                        "Offline fixture: request the local clock again; the client will allow only this call.",
                                        "Offline fixture: request the local clock a third time; a new permission must be requested and denied."):
                        before = len(session.events)
                        session.rpc("turn/start", {"threadId": thread, "input": [{"type": "text", "text": instruction, "text_elements": []}]})
                        end = session.wait(lambda event: event.get("method") == "turn/completed", since=before)
                        assert end["params"]["turn"]["status"] == "completed"
                    items = [event["params"]["item"] for event in session.events if event.get("method") == "item/completed" and event["params"]["item"].get("type") == "mcpToolCall"]
                    # Native tool output is structured content, with JSON clock
                    # text nested inside the model's text-content array. Parse
                    # that protocol, not a substring of its escaped wire JSON.
                    clock = json.loads(items[1]["result"]["content"][0]["text"])
                    utc = datetime.fromisoformat(clock["utc"])
                    local = datetime.fromisoformat(clock["local"])
                    replay = json.loads(results[1])
                    replayed_clock = any(part.get("text") == json.dumps(clock, ensure_ascii=False) for part in replay)
                    checks = {"three_native_confirmations": len(approvals) == 3,
                              "denial_did_not_execute_clock": len(items) == 3 and all(items[index].get("status") == "failed" and not items[index].get("result") for index in (0, 2)),
                              "allowed_clock_returned_actual_time": items[1].get("status") == "completed" and utc.utcoffset().total_seconds() == 0 and abs((datetime.now(timezone.utc) - utc).total_seconds()) < 60 and abs((local - utc).total_seconds()) < 1,
                              "actual_clock_replayed_to_model": replayed_clock,
                              "no_persistent_grant": len(approvals) == 3 and [approval["desktop_response"]["action"] for approval in approvals] == ["decline", "accept", "decline"] and all(approval["desktop_response"]["_meta"] is None for approval in approvals),
                              "bounded_provider_no_errors": len(requests) == 6 and not errors}
                    report.update(checks=checks, approvals=approvals, clock_tool_items=items, tool_results=results, scripted_provider_requests=len(requests),
                                  status="passed" if all(checks.values()) else "failed")
                finally:
                    if session:
                        session.close()
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:1500], scripted_provider_requests=len(requests), provider_errors=errors)
    finally:
        provider.shutdown()
        provider.server_close()
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
