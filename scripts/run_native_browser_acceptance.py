"""Actual native browser MCP + Desktop approval helper on a public test page.

Scripted provider, not real-model research or browser task quality. No personal
browser state, uploads, private prompts, paid requests or security UI clicks.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import uuid

from smara.native_provider import ChatEndpoint, ResponsesAdapter
from smara.native_runtime import native_binary
from scripts.native_session import NativeSession


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=root / "build/native-browser-acceptance.json")
    target = parser.parse_args().report.resolve()
    if not target.is_relative_to(root / "build"):
        raise ValueError("Report must stay under ignored build")
    scratch = root / "build" / ("native-browser-" + uuid.uuid4().hex)
    workspace = scratch / "workspace"
    workspace.mkdir(parents=True)
    counts, approvals, errors = [], [], []
    url = "https://example.com/"
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_POST(self):
            try:
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                counts.append(len(payload["messages"]))
                position = len(counts)
                if position > 6:
                    raise RuntimeError("Unexpected scripted provider retry")
                if position in (1, 3, 5):
                    tools = [entry["function"] for entry in payload["tools"] if entry["function"]["name"].endswith("browser_open")]
                    if len(tools) != 1: raise RuntimeError("Native browser tool absent or ambiguous")
                    delta = {"tool_calls": [{"index": 0, "id": f"browser-fixture-{position}", "function": {"name": tools[0]["name"], "arguments": json.dumps({"url": url})}}]}
                    finish = "tool_calls"
                else:
                    delta, finish = {"content": "Offline browser protocol fixture finished; not AI task quality."}, "stop"
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Connection", "close"); self.end_headers()
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n").encode()); self.wfile.flush()
            except Exception as exc:
                errors.append(type(exc).__name__); self.send_error(500)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    report = {"status": "failed", "scope": "scripted provider + actual native MCP, real owned Chromium, public example.com, Desktop response helper",
              "paid_provider_requests": 0, "private_data_uploaded": False, "gui_security_controls_clicked": False,
              "fixture": str(scratch), "native_sha256": hashlib.sha256(native_binary().read_bytes()).hexdigest()}
    session = None
    def decide(message):
        params = message["params"]
        if message["method"] != "mcpServer/elicitation/request" or params.get("serverName") != "smara_browser" or params.get("_meta", {}).get("tool_params") != {"url": url} or "browser_open" not in params.get("message", ""):
            raise RuntimeError("Unexpected fixture permission scope")
        if len(approvals) >= 3: raise RuntimeError("Unexpected extra permission request")
        allow = len(approvals) == 1
        code = "import fs from 'node:fs';import vm from 'node:vm';import ts from 'typescript';const m={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/nativeView.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{module:m,exports:m.exports});const p=JSON.parse(fs.readFileSync(0,'utf8'));if(!m.exports.supportsApproval('mcpServer/elicitation/request',p.params))throw Error('unsupported native approval');process.stdout.write(JSON.stringify(m.exports.approvalResponse('mcpServer/elicitation/request',p.params,p.allow)));"
        response = subprocess.run(["node", "--input-type=module", "-e", code], input=json.dumps({"params": params, "allow": allow}), cwd=root / "apps/desktop", capture_output=True, text=True, timeout=30)
        if response.returncode: raise RuntimeError("Desktop helper failed")
        decision = json.loads(response.stdout)
        approvals.append(decision)
        return decision
    try:
        with ResponsesAdapter(ChatEndpoint(f"http://127.0.0.1:{provider.server_port}/v1", "offline-browser", "")) as adapter:
            session = NativeSession(adapter, workspace, scratch / "home", browser_origins=["https://example.com"], request_handler=decide, timeout=120)
            session.initialize()
            thread = session.rpc("thread/start", {"cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})["thread"]["id"]
            for instruction in ("Offline fixture: request example.com; deny", "Offline fixture: request example.com; allow only this action", "Offline fixture: request example.com again; deny"):
                before = len(session.events)
                session.rpc("turn/start", {"threadId": thread, "input": [{"type": "text", "text": instruction, "text_elements": []}]})
                completed = session.wait(lambda event: event.get("method") == "turn/completed" and event["params"].get("threadId") == thread, since=before)
                if completed["params"]["turn"]["status"] != "completed": raise RuntimeError("Native turn did not finish")
            items = [event["params"]["item"] for event in session.events if event.get("method") == "item/completed" and event["params"]["item"].get("type") == "mcpToolCall"]
            observed = json.loads(items[1]["result"]["content"][0]["text"])
            checks = {"three_native_action_confirmations": len(approvals) == 3,
                      "deny_allow_deny_without_persistent_grant": [entry["action"] for entry in approvals] == ["decline", "accept", "decline"] and all(entry["_meta"] is None for entry in approvals),
                      "denied_actions_not_executed": len(items) == 3 and all(items[index]["status"] == "failed" and not items[index].get("result") for index in (0, 2)),
                      # The live body is mutable and need not repeat its title.
                      # Verify real retrieval and pagination, not an old quote.
                      "real_public_page_observed": items[1]["status"] == "completed" and observed["url"] == url and observed["title"] == "Example Domain" and bool(observed["text"].strip()) and observed["offset"] == 0 and observed["total_chars"] >= len(observed["text"]) and (observed["next_offset"] == len(observed["text"]) if observed["next_offset"] is not None else observed["total_chars"] == len(observed["text"])),
                      "observation_grounding_returned": bool(observed["observation_id"]) and observed["modality"] == "DOM text; no vision",
                      "bounded_no_provider_errors": len(counts) == 6 and not errors}
            report.update(status="passed" if all(checks.values()) else "failed", checks=checks, page_title=observed["title"], observed_url=observed["url"], native_tool_status=items[1]["status"], body_preview=observed["text"][:300])
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc)[:1000])
    finally:
        if session: session.close()
        provider.shutdown(); provider.server_close()
    report.update(scripted_provider_requests=len(counts), approvals=approvals, provider_errors=errors)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
