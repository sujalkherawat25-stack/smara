"""Model-free native patch probe: never permits an unsandboxed retry."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import uuid

from smara.native_provider import ChatEndpoint, ResponsesAdapter
from scripts.native_session import NativeSession
import scripts.native_session as transport


def main():
    root = Path(__file__).resolve().parents[1]
    # These directories are temporary projects used by the real filesystem
    # sandbox. Inherit ordinary workspace ACLs: tempfile.mkdtemp applies a
    # private owner-only ACL on Windows, which intentionally blocks Smara's
    # restricted-token sandbox and makes the fixture unlike a user project.
    scratch = root / (".smara-patch-sandbox-" + uuid.uuid4().hex)
    scratch.mkdir()
    report = {"synthetic_provider": True, "scratch": str(scratch),
        "mxc": "Not selected by Smara until its separate workspace-write gate passes.", "backends": []}
    for mxc in (False,):
        workspace = scratch / "restricted-token"
        workspace.mkdir()
        (workspace / "answer.txt").write_text("before\n", encoding="utf-8")
        outside = scratch / "outside-restricted.txt"
        outside.write_text("must remain unchanged\n", encoding="utf-8")
        outputs, calls = [], []
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(payload)
                if len(calls) <= 2:
                    tool = next(t["function"]["name"] for t in payload["tools"] if t["function"]["name"].endswith("apply_patch"))
                    patch = "*** Begin Patch\n*** Update File: answer.txt\n@@\n-before\n+after\n*** End Patch" if len(calls) == 1 else "*** Begin Patch\n*** Update File: " + str(outside) + "\n@@\n-must remain unchanged\n+escape must be rejected\n*** End Patch"
                    delta = {"tool_calls": [{"index": 0, "id": "synthetic-patch-" + str(len(calls)), "type": "function", "function": {"name": tool, "arguments": json.dumps({"input": patch})}}]}
                else:
                    outputs.extend(str(m.get("content")) for m in payload["messages"] if m.get("role") == "tool")
                    delta = {"content": "Synthetic probe finished; inspect the native result."}
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Connection", "close"); self.end_headers()
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls" if len(calls) <= 2 else "stop"}]}) + "\n\ndata: [DONE]\n\n").encode()); self.wfile.flush()
        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        original = transport.launch_options
        def options(*args, **kwargs):
            options, env = original(*args, **kwargs)
            return [*options, "-c", "features.prefer_mxc=" + str(mxc).lower()], env
        transport.launch_options = options
        try:
            with ResponsesAdapter(ChatEndpoint(f"http://127.0.0.1:{server.server_port}", "offline-patch-probe", "")) as adapter:
                session = NativeSession(adapter, workspace, scratch / ("home-mxc" if mxc else "home-restricted"))
                try:
                    session.initialize()
                    thread = session.rpc("thread/start", {"cwd": str(workspace), "approvalPolicy": "never", "sandbox": "workspace-write"})["thread"]
                    turn = session.rpc("turn/start", {"threadId": thread["id"], "input": [{"type": "text", "text": "Synthetic sandbox patch probe", "text_elements": []}]})["turn"]
                    session.wait(lambda m: m.get("method") == "turn/completed" and m["params"]["turn"]["id"] == turn["id"])
                    report["backends"].append({"name": "windows-restricted-token", "prefer_mxc": mxc, "patched": (workspace / "answer.txt").read_text() == "after\n", "outside_unchanged": outside.read_text() == "must remain unchanged\n", "outputs": outputs,
                        "approval_requests": sum(bool(m.get("id") is not None and m.get("method")) for m in session.events)})
                finally: session.close()
        finally:
            transport.launch_options = original
            server.shutdown(); server.server_close()
    (root / "build/patch-sandbox-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if all(b["patched"] and b["outside_unchanged"] and not b["approval_requests"] for b in report["backends"]) else 1


if __name__ == "__main__": raise SystemExit(main())
