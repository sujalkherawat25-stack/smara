"""Actual native binary + public SDK, scripted HTTP fixtures, no paid services."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time

import httpx
from syntarus import MemoryClient
from smara import native_management, native_memory
from smara.native_provider import ChatEndpoint, ResponsesAdapter
from smara.native_runtime import launch_options, native_binary


def main():
    root = Path(__file__).resolve().parents[1]
    (root / "build").mkdir(exist_ok=True)
    checks, model_requests, sdk_requests, stored = {}, [], [], []
    with tempfile.TemporaryDirectory(prefix="memory-acceptance-", dir=root / "build") as temporary:
        test_root = Path(temporary)
        workspace = test_root / "project"; workspace.mkdir()
        home = test_root / "runtime"
        # Only this process uses isolated synthetic settings; personal state is untouched.
        os.environ["SMARA_DESKTOP_STATE"] = str(test_root / "desktop.json")
        os.environ["SMARA_DESKTOP_CREDENTIALS"] = str(test_root / "credentials.json")
        os.environ["SMARA_NATIVE_HOME"] = str(home)
        native_management.manage({"operation": "save_memory", "secret": "sk_mem_synthetic-native-acceptance-123456", "memory": {"enabled": True, "user_id": "synthetic-native-user", "scope": "project"}})
        skill = workspace / ".agents/skills/synthetic-procedure/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: synthetic-procedure\ndescription: Use only for the synthetic procedural fixture.\n---\nCheck the actual result before declaring completion.\n", encoding="utf-8")

        def memory_http(request):
            sdk_requests.append(request.url.path)
            assert str(request.url).startswith(native_memory.BASE_URL + "/")
            if request.url.path.endswith("/memories/search"):
                return httpx.Response(200, json={"context": "Synthetic user prefers morning meetings." if stored else ""})
            if request.url.path.endswith("/memories"):
                payload = json.loads(request.content)
                stored.append(payload)
                return httpx.Response(200, json={"event_id": "synthetic-native-event"})
            return httpx.Response(200, json={"status": "succeeded"})

        def memory_sdk(key):
            client = MemoryClient(api_key=key, base_url=native_memory.BASE_URL, timeout=4)
            client._client.close()
            # Test transport only. All production calls use the public SDK's
            # normal transport and fixed hosted endpoint, never this fixture.
            client._client = httpx.Client(base_url=native_memory.BASE_URL, headers={"Authorization": "Bearer " + key}, transport=httpx.MockTransport(memory_http), trust_env=False)
            return client

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                model_requests.append(payload)
                reply = "Synthetic fixture recorded; not real-model quality evidence."
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Connection", "close"); self.end_headers()
                self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": reply}, "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n").encode()); self.wfile.flush()

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        try:
            with ResponsesAdapter(ChatEndpoint(f"http://127.0.0.1:{provider.server_port}/v1", "synthetic-memory-fixture", "")) as adapter:
                adapter.memory = native_memory.ConversationMemory(workspace, client_factory=memory_sdk, background=False)
                args, env = launch_options(adapter, home=home, workspace=workspace)
                process = subprocess.Popen([str(native_binary()), *args, "app-server", "--listen", "stdio://"], cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                messages = queue.Queue()
                def read():
                    for line in process.stdout: messages.put(json.loads(line))
                threading.Thread(target=read, daemon=True).start()
                def send(method, params=None, identity=None):
                    value = {"method": method, "params": params or {}}
                    if identity: value["id"] = identity
                    process.stdin.write(json.dumps(value) + "\n"); process.stdin.flush()
                def wait(predicate):
                    deadline = time.monotonic() + 45
                    while time.monotonic() < deadline:
                        value = messages.get(timeout=max(.1, deadline - time.monotonic()))
                        if predicate(value): return value
                    raise RuntimeError("Native fixture timed out")
                def rpc(method, params, identity):
                    send(method, params, identity)
                    value = wait(lambda m: m.get("id") == identity and "method" not in m)
                    assert "error" not in value, value.get("error")
                    return value["result"]
                try:
                    rpc("initialize", {"clientInfo": {"name": "smara_memory_offline_acceptance", "version": "1"}}, "init")
                    send("initialized")
                    catalog = rpc("skills/list", {"cwds": [str(workspace)]}, "skills")
                    checks["native_file_skill_discovery"] = "synthetic-procedure" in json.dumps(catalog)
                    for number, prompt in enumerate(("Synthetic preference: I prefer morning meetings.", "What meeting time do I prefer? Use only retrieved synthetic evidence."), 1):
                        thread = rpc("thread/start", {"cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "read-only"}, f"thread-{number}")["thread"]
                        rpc("turn/start", {"threadId": thread["id"], "input": [{"type": "text", "text": prompt, "text_elements": []}]}, f"turn-{number}")
                        result = wait(lambda m: m.get("method") == "turn/completed" and m.get("params", {}).get("threadId") == thread["id"])
                        assert result["params"]["turn"]["status"] == "completed"
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline:
                            with native_memory.connect() as db:
                                if db.execute("SELECT count(*) FROM writes").fetchone()[0] >= number: break
                            time.sleep(.02)
                        adapter.memory.pump()
                        with native_memory.connect() as db: db.execute("UPDATE writes SET next_at=0")
                        adapter.memory.pump()
                    checks["two_native_threads_completed"] = len(model_requests) == 2
                    checks["completed_turns_captured_once"] = len(stored) == 2
                    checks["cross_session_recall_reaches_model"] = "Synthetic user prefers morning meetings." in json.dumps(model_requests[1])
                    checks["skill_body_not_uploaded_to_memory"] = "Check the actual result before declaring completion." not in json.dumps(stored)
                    checks["ingestion_status_verified"] = native_memory.status(workspace)["writes"].get("succeeded") == 2
                    checks["no_memory_key_in_native_launch"] = "SYNTARUS_API_KEY" not in env and "sk_mem_" not in " ".join(args)
                finally:
                    process.stdin.close()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
        finally:
            provider.shutdown(); provider.server_close()
    report = {"checks": checks, "passed": all(checks.values()), "scripted_model_requests": len(model_requests), "synthetic_sdk_requests": len(sdk_requests), "hosted_requests": 0, "live_model_quality_verified": False}
    (root / "build/native-memory-acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
