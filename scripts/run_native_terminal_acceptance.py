"""Bounded, offline prerequisite for a future interactive Desktop terminal.

Exercises the copied command/exec implementation, never unsandboxed
process/spawn. Only synthetic scratch paths and an inert loopback model URL
are used. Leaves its uniquely named ignored fixture for inspection. This is
not live-model quality, network/path-escape certification or a GUI terminal.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from smara.native_runtime import native_binary


def main() -> int:
    if os.name != "nt":
        raise SystemExit("This prerequisite tests the copied Windows sandbox")
    root = Path(__file__).resolve().parents[1]
    fixture = root / "build" / ("native-terminal-" + uuid.uuid4().hex)
    # Ordinary inherited ACLs, unlike tempfile's private Windows DACL: the
    # sandbox must be able to reach the selected synthetic workspace.
    workspace = fixture / "workspace"
    workspace.mkdir(parents=True)
    outside = fixture / "outside"
    outside.mkdir()
    state = fixture / "desktop.json"
    state.write_text('{"local_model_profiles": []}', encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONIOENCODING="utf-8",
                       SMARA_DESKTOP_STATE=str(state), SMARA_DESKTOP_CREDENTIALS=str(fixture / "empty-credentials.json"))
    child = subprocess.Popen([sys.executable, "-u", "-c", "from smara.native_runtime import serve_bootstrap; raise SystemExit(serve_bootstrap())"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=root, env=environment, text=True, encoding="utf-8",
                             creationflags=subprocess.CREATE_NO_WINDOW)
    messages = queue.Queue()
    replies, output, caps = {}, {}, set()
    next_id = 0
    stderr_lines = []

    def read():
        try:
            for line in child.stdout:
                messages.put(json.loads(line))
        finally:
            messages.put({"transport_exited": True})

    def drain():
        for _line in child.stderr:
            stderr_lines.append(1)  # Count only; no raw diagnostics in the report.

    threading.Thread(target=read, daemon=True).start()
    threading.Thread(target=drain, daemon=True).start()

    def send(value):
        child.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        child.stdin.flush()

    def start(method, params):
        nonlocal next_id
        next_id += 1
        send({"id": next_id, "method": method, "params": params})
        return next_id

    def observe(message):
        if message.get("transport_exited"):
            raise RuntimeError("Native transport exited before terminal check finished")
        if message.get("method") == "command/exec/outputDelta":
            params = message["params"]
            output.setdefault(params["processId"], bytearray()).extend(base64.b64decode(params["deltaBase64"], validate=True))
            if params.get("capReached"):
                caps.add(params["processId"])
        elif message.get("method") and message.get("id") is not None:
            # Unexpected authority requests are refused, not automatically
            # accepted. This prerequisite needs no elevation or paid model.
            send({"id": message["id"], "error": {"code": -32601, "message": "Offline terminal fixture does not grant authority"}})
            raise RuntimeError("Unexpected native authority request")
        elif message.get("id") is not None:
            replies[message["id"]] = message

    def wait(predicate):
        deadline = time.monotonic() + 25
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Bounded terminal prerequisite timed out")
            observe(messages.get(timeout=remaining))

    def result(identifier):
        wait(lambda: identifier in replies)
        message = replies.pop(identifier)
        if "error" in message:
            # Synthetic command parameters only; preserve the actual native
            # failure so unsupported PTY/sandbox paths cannot be called passes.
            raise RuntimeError("command/exec prerequisite rejected: " + json.dumps(message["error"]))
        return message["result"]

    def command(code, **extra):
        return start("command/exec", {"command": [sys.executable, "-X", "utf8", "-u", "-c", code],
                     "cwd": str(workspace), "permissionProfile": ":workspace", "timeoutMs": 15000, **extra})

    checks = []
    try:
        send({"binary": str(native_binary()), "workspace": str(workspace), "home": str(fixture / "home"),
              "base_url": "http://127.0.0.1:9/v1", "model": "offline-terminal-only", "api_key": "", "tools_enabled": False})
        result(start("initialize", {"clientInfo": {"name": "smara_terminal_prerequisite", "version": "1"}, "capabilities": {"experimentalApi": True}}))
        send({"method": "initialized"})
        buffered = result(command("import sys; print('NATIVE_EXEC'); sys.exit(7)"))
        assert buffered["exitCode"] == 7 and "NATIVE_EXEC" in buffered["stdout"], "Nonzero command outcome was not preserved"
        checks.append("buffered_output_and_nonzero_exit")

        inside_file, outside_file = workspace / "allowed.txt", outside / "blocked.txt"
        code = (f"from pathlib import Path\nPath({str(inside_file)!r}).write_text('inside')\n"
                f"try:\n Path({str(outside_file)!r}).write_text('outside')\n print('OUTSIDE_ALLOWED')\n"
                "except OSError:\n print('OUTSIDE_BLOCKED')\n")
        boundary = result(command(code))
        assert boundary["exitCode"] == 0 and inside_file.read_text() == "inside", "Workspace write failed"
        assert not outside_file.exists() and "OUTSIDE_BLOCKED" in boundary["stdout"], "Outside synthetic workspace write was allowed"
        checks.extend(["workspace_write", "outside_workspace_write_blocked"])

        identifier = command("import sys; print('READY',flush=True); print('ECHO:'+sys.stdin.readline().strip(),flush=True)",
                             processId="input-check", tty=True, size={"rows": 24, "cols": 80})
        wait(lambda: b"READY" in output.get("input-check", b"") or identifier in replies)
        if identifier in replies:
            result(identifier)
            raise RuntimeError("PTY exited before stdin could be tested")
        result(start("command/exec/resize", {"processId": "input-check", "size": {"rows": 32, "cols": 120}}))
        line = "synthetic स्मारा 日本語 😀"
        result(start("command/exec/write", {"processId": "input-check", "deltaBase64": base64.b64encode((line + "\r").encode()).decode()}))
        assert result(identifier)["exitCode"] == 0, "PTY input check failed"
        assert "ECHO:" + line in bytes(output["input-check"]).decode("utf-8"), "PTY lost Unicode input/output"
        assert "input-check" not in caps, "PTY output was unexpectedly capped"
        checks.extend(["pty_streaming", "pty_unicode_stdin", "pty_resize"])

        identifier = command("import time; print('RUNNING',flush=True); time.sleep(14)", processId="stop-check", tty=True)
        wait(lambda: b"RUNNING" in output.get("stop-check", b"") or identifier in replies)
        if identifier in replies:
            result(identifier)
            raise RuntimeError("PTY exited before terminate could be tested")
        result(start("command/exec/terminate", {"processId": "stop-check"}))
        assert result(identifier)["exitCode"] != 0, "Terminated PTY was reported as success"
        checks.append("pty_terminate")
        send({"method": "smara/shutdown"})
        assert child.wait(timeout=45) == 0, "Native transport shutdown failed"
        report = {"status": "passed", "checks": checks, "fixture": str(fixture), "paid_inference": False,
                  "gui_terminal_implemented": False, "network_or_path_escape_certification": False}
        (fixture / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))
        return 0
    except Exception as exc:
        detail = str(exc)
        report = {"status": "blocked" if "streaming command/exec is not supported with windows sandbox" in detail else "failed",
                  "checks_passed_before_stop": checks, "error": detail, "fixture": str(fixture), "paid_inference": False,
                  "gui_terminal_implemented": False, "network_or_path_escape_certification": False}
        (fixture / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))
        return 1  # A refused/unsupported interactive terminal is never a pass.
    finally:
        if child.poll() is None:
            child.stdin.close()
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
