"""Exercise the actual source-built runtime without a paid inference request.

Uses a synthetic model URL; with --tool-roundtrip a scripted local provider
drives real native tool execution. No paid model endpoint is contacted.
Tests native handshake, safe thread configuration, listing and transport exit.
This is protocol acceptance, not real-model/task/sandbox-isolation acceptance.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--tool-roundtrip", action="store_true", help="Use a scripted local provider with real native tools; no paid inference")
    parser.add_argument("--console-entrypoint", action="store_true", help="Launch the installed development smara-desktop console entry point")
    parser.add_argument("--executor", type=Path, help="Test an explicitly selected frozen or installed Desktop executor")
    parser.add_argument("--native-binary", type=Path, help="Test the adjacent release runtime instead of the checkout candidate")
    parser.add_argument("--report", type=Path, help="Retain package/installed acceptance evidence inside build")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report_path = args.report.resolve() if args.report else None
    if report_path and not report_path.is_relative_to((root / "build").resolve()):
        raise ValueError("Keep acceptance reports inside build")
    binary = (args.native_binary or root / "native" / "dist" / "smara-native.exe").resolve()
    if not binary.is_file():
        raise SystemExit("Build the source-native executable before acceptance")
    with tempfile.TemporaryDirectory(prefix="native-protocol-", dir=root / "build") as temporary:
        sandbox = Path(temporary)
        workspace = sandbox / "workspace स्मारा 😀"
        workspace.mkdir()
        provider_requests = []
        provider_errors = []
        denied_callbacks = []
        accepted_patch_callbacks = []
        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                try:
                    payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    provider_requests.append(payload)
                    position = len(provider_requests)
                    if position in (1, 3):
                        suffix = "apply_patch" if position == 1 else "exec_command"
                        tools = [item["function"] for item in payload.get("tools", []) if item["function"]["name"].endswith(suffix)]
                        assert len(tools) == 1, f"Native tool is not exposed: {suffix}; available: {[item['function']['name'] for item in payload.get('tools', [])]}"
                        if position == 1:
                            arguments = {"input": "*** Begin Patch\n*** Add File: native-proof.txt\n+created by the copied native patch executor\n*** End Patch"}
                        else:
                            path = str(workspace / "denied-proof.txt").replace("'", "''")
                            arguments = {"cmd": f"Set-Content -LiteralPath '{path}' -Value 'must not execute'", "sandbox_permissions": "require_escalated", "justification": "Synthetic approval-denial acceptance only"}
                        delta = {"tool_calls": [{"index": 0, "id": f"synthetic-{position}", "type": "function", "function": {"name": tools[0]["name"], "arguments": json.dumps(arguments)}}]}
                        finish = "tool_calls"
                    else:
                        assert position in (2, 4), "Unexpected extra provider request"
                        assert any(item["role"] == "tool" for item in payload["messages"]), "Native tool result missing from model history"
                        delta = {"content": "Synthetic protocol fixture completed; not live-model quality evidence. स्मारा 😀 → 日本語"}
                        finish = "stop"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n").encode())
                    self.wfile.flush()
                except Exception as exc:
                    provider_errors.append(str(exc))
                    self.send_error(500)
        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider) if args.tool_roundtrip else None
        if provider:
            threading.Thread(target=provider.serve_forever, daemon=True).start()
        environment = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONIOENCODING="utf-8")
        code = "from smara.native_runtime import serve_bootstrap; raise SystemExit(serve_bootstrap())"
        command = [sys.executable, "-u", "-c", code]
        if args.console_entrypoint:
            entrypoint = Path(sys.executable).parent / "smara-desktop.exe"
            if not entrypoint.is_file():
                raise RuntimeError("Register the development CLI before console-entrypoint acceptance")
            command = [str(entrypoint), "--native-app-server"]
        if args.executor:
            if args.console_entrypoint or not args.executor.is_file():
                raise ValueError("Select one existing executor, not both executor and console-entrypoint")
            command = [str(args.executor.resolve()), "--native-app-server"]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", env=environment, cwd=root)
        messages: queue.Queue = queue.Queue()
        errors: list[str] = []
        def read():
            for line in process.stdout:
                messages.put(json.loads(line))
            messages.put({"transport_exited": True})
        def stderr():
            for line in process.stderr:
                errors.append(line)
        threading.Thread(target=read, daemon=True).start()
        threading.Thread(target=stderr, daemon=True).start()
        def send(value):
            process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
            process.stdin.flush()
        completed_turns = []
        native_errors = []
        streamed_text = []
        def observe(message):
            if message.get("method") == "item/agentMessage/delta":
                streamed_text.append(message["params"]["delta"])
            if message.get("method") == "error":
                native_errors.append(message)
            if message.get("id") is not None and message.get("method"):
                params = message.get("params", {})
                if message["method"] == "item/fileChange/requestApproval" and params.get("itemId") == "synthetic-1":
                    # Approve only the exact scratch-file patch emitted by this
                    # fixture. Production clients still require a human decision.
                    accepted_patch_callbacks.append(message)
                    send({"id": message["id"], "result": {"decision": "accept"}})
                else:
                    assert message["method"] == "item/commandExecution/requestApproval" and params.get("itemId") == "synthetic-3", "Unexpected approval: " + json.dumps(message)
                    denied_callbacks.append(message)
                    send({"id": message["id"], "result": {"decision": "decline"}})
            if message.get("method") == "turn/completed":
                completed_turns.append(message["params"]["turn"])
        def rpc(identifier, method, params):
            send({"id": identifier, "method": method, "params": params})
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                message = messages.get(timeout=max(.1, deadline - time.monotonic()))
                if message.get("transport_exited"):
                    raise RuntimeError("Native transport exited: " + "".join(errors)[-3000:])
                if message.get("id") == identifier:
                    if message.get("error"):
                        raise RuntimeError(json.dumps(message["error"]))
                    return message["result"]
                observe(message)
            raise TimeoutError(method)
        def wait_turn():
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                if completed_turns:
                    turn = completed_turns.pop(0)
                    assert turn["status"] == "completed", json.dumps(turn)
                    return
                message = messages.get(timeout=max(.1, deadline - time.monotonic()))
                if message.get("transport_exited"):
                    raise RuntimeError("Native transport exited during turn")
                observe(message)
            raise TimeoutError("native turn")
        try:
            send({"binary": str(binary), "workspace": str(workspace), "home": str(sandbox / "home"),
                "base_url": f"http://127.0.0.1:{provider.server_port}/v1" if provider else "http://127.0.0.1:9/v1", "model": "synthetic-protocol-only", "api_key": ""})
            handshake = rpc(1, "initialize", {"clientInfo": {"name": "smara_protocol_acceptance", "title": "Smara acceptance", "version": "0.1.8"}, "capabilities": None})
            send({"method": "initialized"})
            started = rpc(2, "thread/start", {"cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})
            assert started["model"] == "synthetic-protocol-only", "Configured model was not preserved: " + str(started["model"])
            assert started["thread"]["id"], "Native thread has no identity"
            assert started["approvalPolicy"] == "on-request", "Approval policy was not preserved"
            assert started["sandbox"]["type"] == "workspaceWrite", "Workspace sandbox was not preserved"
            checks = ["native_initialize", "thread_start", "approval_policy", "workspace_sandbox_config"]
            if args.tool_roundtrip:
                identifier = started["thread"]["id"]
                rpc(4, "turn/start", {"threadId": identifier, "input": [{"type": "text", "text": "Synthetic local protocol test: create native-proof.txt in this scratch workspace.", "text_elements": []}]})
                wait_turn()
                tool_results = [item["content"] for request in provider_requests for item in request["messages"] if item["role"] == "tool"]
                assert (workspace / "native-proof.txt").is_file(), "Native patch did not create a file. Actual tool results: " + json.dumps(tool_results) + "; approvals: " + json.dumps(denied_callbacks) + "; provider errors: " + json.dumps(provider_errors) + "; native errors: " + json.dumps(native_errors)
                assert (workspace / "native-proof.txt").read_text().strip() == "created by the copied native patch executor", "Native patch content differs"
                rpc(5, "turn/start", {"threadId": identifier, "input": [{"type": "text", "text": "Synthetic permission test: request an elevated command, which the client will deny.", "text_elements": []}]})
                wait_turn()
                assert denied_callbacks, "Native command approval was not requested"
                assert not (workspace / "denied-proof.txt").exists(), "Denied command executed"
                assert not provider_errors, provider_errors
                assert "स्मारा 😀 → 日本語" in "".join(streamed_text), "Native Unicode output was lost"
                resumed = rpc(6, "thread/resume", {"threadId": identifier, "cwd": str(workspace), "approvalPolicy": "on-request", "sandbox": "workspace-write"})
                assert len(resumed["thread"]["turns"]) >= 2, "Native history did not survive resume"
                checks += ["real_native_patch", "tool_result_history", "native_streamed_turn", "utf8_workspace_and_stream", "command_approval_declined", "denied_command_not_executed", "thread_resume"]
            listed = rpc(3, "thread/list", {"limit": 10})
            assert isinstance(listed["data"], list), "Invalid thread listing"
            send({"method": "smara/shutdown"})
            assert process.wait(timeout=55) == 0, "Native shutdown was not graceful"
            report = {"status": "passed", "binary": str(binary), "thread_id": started["thread"]["id"],
                "entrypoint": str(args.executor.resolve()) if args.executor else ("smara-desktop" if args.console_entrypoint else "python-bootstrap"),
                "handshake": bool(handshake), "checks": checks + ["thread_list", "transport_shutdown"],
                "scripted_provider_requests": len(provider_requests), "patch_approvals_granted": len(accepted_patch_callbacks), "paid_inference": False, "os_sandbox_isolation_tested": False}
            if report_path:
                report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report))
        finally:
            if process.poll() is None:
                # Even a failed acceptance should enter native cleanup before
                # falling back to termination; never leave a detached worker.
                process.stdin.close()
                try: process.wait(timeout=55)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
            if provider:
                provider.shutdown()
                provider.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
