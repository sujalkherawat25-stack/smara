"""Launch the actual source-built GUI with isolated state and synthetic workspace."""
from pathlib import Path
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import threading
import time
from smara.native_profiles import load_profiles


def offline_provider():
    """Declared stream fixture, not a model/answer or tool-result substitute."""
    counters = {"requests": 0, "completed_streams": 0, "closed_streams": 0}
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
            self.rfile.read(length)  # Never persist the native prompt or history.
            with lock:
                counters["requests"] += 1
                allowed = counters["requests"] <= 2
            if not allowed:
                self.send_error(429)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
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
    parser.add_argument("--offline-stream", action="store_true", help="Own loopback stream fixture for Stop testing; no real inference")
    parser.add_argument("--binary", type=Path, help="Inspect a packaged/installed Desktop using isolated offline test state")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.binary and (not args.offline_stream or not args.binary.is_file()):
        raise ValueError("An explicit packaged binary requires --offline-stream and an existing file")
    candidate = root / "build" / ("native-desktop-installed-offline" if args.binary else ("native-desktop-offline" if args.offline_stream else "native-desktop-candidate"))
    workspace = candidate / "workspace"
    data = candidate / "data"
    workspace.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    sessions = data / "native-runtime" / "sessions"
    existing_rollouts = set(sessions.rglob("*.jsonl")) if sessions.exists() else set()
    server = None
    if args.offline_stream:
        server, counters = offline_provider()
        selected = "native_protocol_fixture"
        profiles = [{"id": selected, "label": "OFFLINE stream fixture — not AI", "provider": "openai", "model": "synthetic-protocol",
            "base_url": f"http://127.0.0.1:{server.server_port}/v1", "auth_header": "authorization", "credential_name": "SMARA_OFFLINE_TEST_CREDENTIAL", "updated_at": "2026-10-09T00:00:00+05:30"}]
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
        from smara.desktop_executor import _protect_windows
        vault = data / "credentials.json"
        vault.write_text(json.dumps({"SMARA_OFFLINE_TEST_CREDENTIAL": {"protected": _protect_windows("synthetic-loopback-only")}}), encoding="utf-8")
        env["SMARA_DESKTOP_CREDENTIALS"] = str(vault)
    binary = (args.binary or root / "apps/desktop/src-tauri/target/debug/smara-desktop.exe").resolve()
    process = subprocess.Popen([str(binary)], env=env,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    print(json.dumps({"pid": process.pid, "binary": str(binary), "workspace": str(workspace), "isolated_state": str(data), "offline_protocol_fixture": bool(server),
        "status_url": f"http://127.0.0.1:{server.server_port}/status" if server else None}), flush=True)
    if server:
        try:
            process.wait()
        finally:
            server.shutdown()
            server.server_close()
            # Read only new isolated-test ledgers, never user history or full
            # prompts. These checks prove native interruption/transport closure;
            # which GUI controls the operator used is separately documented.
            events = []
            for rollout in sessions.rglob("*.jsonl"):
                if rollout in existing_rollouts:
                    continue
                for line in rollout.read_text(encoding="utf-8").splitlines():
                    record = json.loads(line)
                    payload = record.get("payload", {})
                    if record.get("type") == "event_msg" and payload.get("type") in {"task_started", "task_complete", "turn_aborted"}:
                        events.append({"type": payload["type"], "turn_id": payload.get("turn_id"), "reason": payload.get("reason")})
            checks = {"one_local_request": counters["requests"] == 1,
                      "stream_closed_before_completion": counters["closed_streams"] == 1 and counters["completed_streams"] == 0,
                      "one_native_turn": sum(event["type"] == "task_started" for event in events) == 1,
                      "native_interrupted": sum(event["type"] == "turn_aborted" and event["reason"] == "interrupted" for event in events) == 1,
                      "no_false_native_completion": not any(event["type"] == "task_complete" for event in events)}
            report = {"status": "passed" if all(checks.values()) else "failed", "scope": "offline stream/native-ledger cancellation, not real-model quality",
                      "gui_actions_machine_verified": False, "offline_provider": counters, "native_events": events, "checks": checks,
                      "paid_requests": 0, "installed_or_published": False}
            (candidate / "stop-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
