"""Launch the actual source-built GUI with isolated state and synthetic workspace."""
from pathlib import Path
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import threading
import time
import uuid
from smara.native_profiles import load_profiles


def candidate_directory(root, packaged=False, offline=False):
    """Never overwrite an earlier GUI fixture, ledger or failed report."""
    prefix = "native-desktop-installed-offline" if packaged else ("native-desktop-offline" if offline else "native-desktop-candidate")
    candidate = root.resolve() / "build" / (prefix + "-" + uuid.uuid4().hex)
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def clock_fixture_response(request, position):
    """Declared offline fixture: only request the clock, never grant it."""
    if position == 1:
        tools = [entry["function"] for entry in request.get("tools", [])
                 if entry.get("function", {}).get("name", "").endswith("current_time")]
        if len(tools) != 1:
            raise ValueError("Enable the readers manually before connecting this fixture")
        return {"tool_calls": [{"index": 0, "id": "offline-clock-deny",
            "function": {"name": tools[0]["name"], "arguments": "{}"}}]}, "tool_calls", False
    if position == 2:
        results = [entry.get("content", "") for entry in request.get("messages", []) if entry.get("role") == "tool"]
        rejected = bool(results) and "user rejected MCP tool call" in str(results[-1])
        return {"content": "OFFLINE permission fixture finished — not an AI answer. " +
            ("Native tool rejection observed." if rejected else "Expected rejection was NOT observed.")}, "stop", rejected
    raise ValueError("Offline clock fixture allows only two local requests")


def denied_clock_item(item):
    """Read the durable native item, not an unpersisted transport notification."""
    return (item.get("type") == "McpToolCall" and item.get("server") == "smara_readers"
            and item.get("tool") == "current_time" and item.get("arguments") == {}
            and item.get("status") == "failed" and item.get("result") is None
            and item.get("error") == {"message": "user rejected MCP tool call"})


def offline_provider(clock=False):
    """Declared stream fixture, not a model/answer or tool-result substitute."""
    counters = {"requests": 0, "completed_streams": 0, "closed_streams": 0,
                "clock_rejection_observed": False, "fixture_errors": 0}
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path != "/status":
                self.send_error(404)
                return
            with lock:
                body = json.dumps(counters).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/chat/completions" or not 0 < length < 1_000_000:
                self.send_error(400)
                return
            raw = self.rfile.read(length)  # Never persist the native prompt or history.
            with lock:
                counters["requests"] += 1
                allowed = counters["requests"] <= 2
            if not allowed:
                self.send_error(429)
                return
            if clock:
                try:
                    delta, finish, rejected = clock_fixture_response(json.loads(raw), counters["requests"])
                except (ValueError, KeyError, TypeError):
                    with lock:
                        counters["fixture_errors"] += 1
                    self.send_error(400)
                    return
                with lock:
                    counters["clock_rejection_observed"] = rejected
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                if clock:
                    self.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\ndata: [DONE]\n\n").encode())
                    self.wfile.flush()
                    with lock:
                        counters["completed_streams"] += 1
                    return
                for index in range(240):
                    chunk = {"choices": [{"index": 0, "delta": {"content": "Offline protocol fixture. स्मारा 😀 → 日本語 " if index == 0 else "streaming… "}, "finish_reason": None}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(.25)
                end = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(("data: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
                with lock:
                    counters["completed_streams"] += 1
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                with lock:
                    counters["closed_streams"] += 1
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, counters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline-stream", action="store_true", help="Own loopback stream fixture for Stop testing; no real inference")
    mode.add_argument("--offline-clock-deny", action="store_true", help="Human Deny check on an actual native clock permission; no automatic grants or paid inference")
    parser.add_argument("--binary", type=Path, help="Inspect a packaged/installed Desktop using isolated offline test state")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    offline = args.offline_stream or args.offline_clock_deny
    if args.binary and (not offline or not args.binary.is_file()):
        raise ValueError("An explicit packaged binary requires an offline mode and an existing file")
    candidate = candidate_directory(root, packaged=bool(args.binary), offline=offline)
    workspace = candidate / "workspace"
    data = candidate / "data"
    workspace.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    sessions = data / "native-runtime" / "sessions"
    existing_rollouts = set(sessions.rglob("*.jsonl")) if sessions.exists() else set()
    server = None
    if offline:
        server, counters = offline_provider(clock=args.offline_clock_deny)
        selected = "native_protocol_fixture"
        profiles = [{"id": selected, "label": "OFFLINE clock Deny fixture — not AI" if args.offline_clock_deny else "OFFLINE stream fixture — not AI", "provider": "openai", "model": "synthetic-protocol",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1", "auth_header": "authorization", "credential_name": "SMARA_OFFLINE_TEST_CREDENTIAL", "updated_at": "2026-10-09T00:00:00+05:30"}]
        if args.offline_stream:
            # UI-only model switching stays on the same owned loopback server.
            # No user's profiles or keys are read, copied or contacted.
            profiles.append({**profiles[0], "id": "native_protocol_alternate", "label": "OFFLINE alternate fixture — not AI", "model": "synthetic-protocol-alternate"})
        if args.offline_clock_deny:
            # Fixture-only: ASK for the clock. This does not enable readers or
            # approve a call. The human must enable readers and click Deny.
            home = data / "native-runtime"
            home.mkdir()
            (home / "config.toml").write_text('[mcp_servers.smara_readers]\ndefault_tools_approval_mode = "prompt"\n', encoding="utf-8")
    else:
        profiles, selected, _credentials = load_profiles()
    fields = {"id", "label", "provider", "base_url", "model", "credential_name", "auth_header", "updated_at"}
    safe_profiles = [{key: value for key, value in profile.items() if key in fields} for profile in profiles]
    preferences = {"runtime_mode": "local", "workspace": str(workspace), "model_profile": "local:" + selected,
                   "allowed_roots": [str(workspace)], "local_model_profiles": safe_profiles}
    # Generated test state only: no credentials or personal conversations copied.
    (data / "desktop-ui.json").write_text(json.dumps(preferences), encoding="utf-8")
    env = dict(os.environ, SMARA_DESKTOP_DATA_DIR=str(data), SMARA_DESKTOP_STATE=str(data / "desktop.json"),
               SMARA_REPO_ROOT=str(root), SMARA_DESKTOP_EXECUTABLE=str(root / ".venv/Scripts/smara-desktop.exe"))
    if args.binary:
        # Test actual adjacent package resources, not editable Python or a
        # native binary inherited from the developer environment.
        for key in ("SMARA_REPO_ROOT", "SMARA_DESKTOP_EXECUTABLE", "SMARA_NATIVE_BINARY"):
            env.pop(key, None)
    if server:
        # Exercise the same protected-vault IPC as the real Desktop, without
        # reading or changing the user's vault or weakening credential checks.
        from smara.native_profiles import protect
        vault = data / "credentials.json"
        vault.write_text(json.dumps({"SMARA_OFFLINE_TEST_CREDENTIAL": {"protected": protect("synthetic-loopback-only")}}), encoding="utf-8")
        env["SMARA_DESKTOP_CREDENTIALS"] = str(vault)
    binary = (args.binary or root / "apps/desktop/src-tauri/target/debug/smara-desktop.exe").resolve()
    process = subprocess.Popen([str(binary)], env=env,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    print(json.dumps({"pid": process.pid, "binary": str(binary), "workspace": str(workspace), "isolated_state": str(data), "offline_protocol_fixture": bool(server),
        "status_url": f"http://127.0.0.1:{server.server_port}/status" if server else None,
        "human_clock_deny": args.offline_clock_deny}), flush=True)
    if server:
        try:
            process.wait()
        finally:
            server.shutdown()
            server.server_close()
            # Read only new isolated-test ledgers, never user history or full
            # prompts. These checks prove native interruption/transport closure;
            # which GUI controls the operator used is separately documented.
            events, clock_calls = [], []
            for rollout in sessions.rglob("*.jsonl"):
                if rollout in existing_rollouts:
                    continue
                for line in rollout.read_text(encoding="utf-8").splitlines():
                    record = json.loads(line)
                    payload = record.get("payload", {})
                    if record.get("type") == "event_msg" and payload.get("type") in {"task_started", "task_complete", "turn_aborted"}:
                        events.append({"type": payload["type"], "turn_id": payload.get("turn_id"), "reason": payload.get("reason")})
                    if args.offline_clock_deny and record.get("type") == "event_msg" and payload.get("type") == "item_completed" and payload.get("item", {}).get("type") == "McpToolCall":
                        clock_calls.append(payload["item"])
            checks = {"one_local_request": counters["requests"] == 1,
                      "stream_closed_before_completion": counters["closed_streams"] == 1 and counters["completed_streams"] == 0,
                      "one_native_turn": sum(event["type"] == "task_started" for event in events) == 1,
                      "native_interrupted": sum(event["type"] == "turn_aborted" and event["reason"] == "interrupted" for event in events) == 1,
                      "no_false_native_completion": not any(event["type"] == "task_complete" for event in events)}
            if args.offline_clock_deny:
                checks = {"two_local_requests": counters["requests"] == counters["completed_streams"] == 2,
                          "no_fixture_errors": counters["fixture_errors"] == 0,
                          "native_clock_denied": len(clock_calls) == 1 and denied_clock_item(clock_calls[0]),
                          "rejection_reached_provider": counters["clock_rejection_observed"],
                          "one_completed_native_turn": [entry["type"] for entry in events] == ["task_started", "task_complete"]}
            report = {"status": "passed" if all(checks.values()) else "failed", "scope": "offline clock/native denial, human GUI action separately verified" if args.offline_clock_deny else "offline stream/native-ledger cancellation, not real-model quality",
                      "gui_actions_machine_verified": False, "offline_provider": counters, "native_events": events, "checks": checks,
                      "paid_requests": 0, "binary": str(binary), "runner_installs_or_publishes": False}
            report_name = "clock-deny-report.json" if args.offline_clock_deny else "stop-report.json"
            (candidate / report_name).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report), flush=True)
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
