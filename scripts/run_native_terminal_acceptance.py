"""Bounded, offline acceptance for the sandboxed interactive Desktop terminal.

Exercises the copied command/exec implementation, never unsandboxed
process/spawn. Only synthetic scratch paths and an inert loopback model URL
are used. Leaves its uniquely named ignored fixture for inspection. This is
not live-model quality, network/path-escape certification or a GUI terminal.
"""
from __future__ import annotations

import base64
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import uuid

from smara.native_runtime import native_binary


def bootstrap_program(backend):
    if backend not in {"unelevated", "mxc"}:
        raise ValueError("Choose an explicit native Windows sandbox backend")
    # Select only the backend under test. Native execution, permissions,
    # output and confinement checks stay real; no tool outcome is replaced.
    return (
        "import smara.native_runtime as runtime\n"
        "original = runtime.launch_options\n"
        "def configured(*args, **kwargs):\n"
        " options, environment = original(*args, **kwargs)\n"
        f" options += ['-c', 'windows.sandbox=\"{backend}\"', '-c', 'features.prefer_mxc=false']\n"
        " return options, environment\n"
        "runtime.launch_options = configured\n"
        "raise SystemExit(runtime.serve_bootstrap())\n"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-backend", choices=["unelevated", "mxc"], default="mxc",
                        help="Actual imported backend under test; never switches to unsandboxed execution")
    args = parser.parse_args(argv)
    if os.name != "nt":
        raise SystemExit("This prerequisite tests the copied Windows sandbox")
    root = Path(__file__).resolve().parents[1]
    binary = native_binary()
    with binary.open("rb") as stream:
        native_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
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
    child = subprocess.Popen([sys.executable, "-u", "-c", bootstrap_program(args.windows_backend)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=root, env=environment, text=True, encoding="utf-8",
                             creationflags=subprocess.CREATE_NO_WINDOW)
    messages = queue.Queue()
    replies, output, caps = {}, {}, set()
    next_id = 0
    stderr_lines = []
    transport_errors = []

    def read():
        try:
            for line in child.stdout:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError as exc:
                    transport_errors.append(f"invalid JSON on native transport (line {exc.lineno}, column {exc.colno})")
                    return
        except Exception as exc:
            transport_errors.append(type(exc).__name__)
        finally:
            messages.put({"transport_exited": True})

    def drain():
        secret_values = [value for key, value in environment.items()
                         if re.search(r"(?:API[_-]?KEY|ACCESS[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", key, re.I)
                         and value]
        for line in child.stderr:
            safe = line.rstrip()
            for secret in secret_values:
                safe = safe.replace(secret, "<redacted>")
            safe = re.sub(r"(?i)(api[_-]?key|token|secret|password)(\s*[=:]\s*)[^\s,;]+", r"\1\2<redacted>", safe)
            if safe:
                stderr_lines.append(safe[:500])
                del stderr_lines[:-20]

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
            code = child.poll()
            reader_error = transport_errors[-1] if transport_errors else "none"
            raise RuntimeError(f"Native transport exited before terminal check finished (exit={code}, reader={reader_error})")
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
        policy = extra.pop("sandboxPolicy", None)
        params = {"command": [sys.executable, "-X", "utf8", "-u", "-c", code],
                  "cwd": str(workspace), "timeoutMs": 15000, **extra}
        if policy is None:
            params["permissionProfile"] = ":workspace"
        else:
            params["sandboxPolicy"] = policy
        return start("command/exec", params)

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

        helper_target = workspace / "fs-helper-write.txt"
        helper_target.write_text("existing file\n", encoding="utf-8")
        helper_request = {"operation": "fs/writeFile", "params": {"path": helper_target.as_uri(),
                          "dataBase64": base64.b64encode(b"native helper write").decode(), "followSymlinks": True}}
        code = ("import subprocess, sys\n"
                f"result = subprocess.run([{str(native_binary())!r}, '--codex-run-as-fs-helper'], input={json.dumps(helper_request) + chr(10)!r}, capture_output=True, text=True, timeout=10)\n"
                "print(result.stdout); print(result.stderr); sys.exit(result.returncode)\n")
        helper_result = result(command(code))
        assert helper_result["exitCode"] == 0 and helper_target.is_file() and helper_target.read_text() == "native helper write", "Native filesystem helper cannot write the selected project"
        checks.append("native_fs_helper_workspace_write")

        terminal_policy = {"type": "workspaceWrite", "writableRoots": [str(workspace)], "networkAccess": False}
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            code = ("import socket\n"
                    f"try:\n socket.create_connection(('127.0.0.1', {port}), timeout=1).close()\n"
                    " print('NETWORK_ALLOWED')\n"
                    "except OSError:\n print('NETWORK_BLOCKED')\n")
            network = result(command(code, sandboxPolicy=terminal_policy))
            assert network["exitCode"] == 0 and "NETWORK_BLOCKED" in network["stdout"], "Terminal sandbox allowed a loopback network connection"
        checks.append("network_disabled_loopback")

        # Calibrate each owned listener outside the sandbox first. A dead
        # listener or full calibration backlog must not masquerade as a deny.
        for host, family, label, descendant in (
            ("::1", socket.AF_INET6, "network_disabled_ipv6", False),
            ("127.0.0.1", socket.AF_INET, "network_disabled_descendant_without_proxy_env", True),
        ):
            with socket.socket(family, socket.SOCK_STREAM) as listener:
                listener.bind((host, 0))
                listener.listen(2)
                listener.settimeout(2)
                port = listener.getsockname()[1]
                socket.create_connection((host, port), timeout=1).close()
                calibration, _ = listener.accept()
                calibration.close()
                code = ("import socket\n"
                        f"try:\n socket.create_connection(({host!r}, {port}), timeout=1).close()\n"
                        " print('NETWORK_ALLOWED')\n"
                        "except OSError:\n print('NETWORK_BLOCKED')\n")
                if descendant:
                    code = ("import os, subprocess, sys\nos.environ.clear()\n"
                            f"child = subprocess.run([sys.executable, '-c', {code!r}], capture_output=True, text=True, timeout=5)\n"
                            "print(child.stdout)\nsys.exit(child.returncode)\n")
                network = result(command(code, sandboxPolicy=terminal_policy))
                assert network["exitCode"] == 0 and "NETWORK_BLOCKED" in network["stdout"], f"Sandbox failed {label}"
            checks.append(label)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.settimeout(.1)
            port = listener.getsockname()[1]
            stop_echo, delivered = threading.Event(), []
            def echo():
                while not stop_echo.is_set():
                    try:
                        payload, address = listener.recvfrom(100)
                    except socket.timeout:
                        continue
                    delivered.append(payload)
                    listener.sendto(payload, address)
            echo_thread = threading.Thread(target=echo, daemon=True)
            echo_thread.start()
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as calibration:
                    calibration.settimeout(1)
                    calibration.sendto(b"CALIBRATION", ("127.0.0.1", port))
                    assert calibration.recvfrom(100)[0] == b"CALIBRATION"
                code = ("import socket\ns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)\ns.settimeout(1)\n"
                        f"try:\n s.sendto(b'SMARA_NATIVE_UDP', ('127.0.0.1', {port}))\n s.recvfrom(100)\n"
                        " print('NETWORK_ALLOWED')\nexcept OSError:\n print('NETWORK_BLOCKED')\n")
                network = result(command(code, sandboxPolicy=terminal_policy))
                assert network["exitCode"] == 0 and "NETWORK_BLOCKED" in network["stdout"], "Sandbox allowed UDP echo"
                assert b"SMARA_NATIVE_UDP" not in delivered, "Sandbox leaked an outbound UDP packet"
            finally:
                stop_echo.set()
                echo_thread.join(timeout=1)
        checks.append("network_disabled_udp_no_outbound_delivery")

        junction = workspace / "outside-junction"
        escaped = outside / "junction-escaped.txt"
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                        f"New-Item -ItemType Junction -Path {quote(junction)} -Target {quote(outside)} -ErrorAction Stop | Out-Null"],
                       check=True, capture_output=True, timeout=15)
        code = ("from pathlib import Path\n"
                f"try:\n Path({str(junction / escaped.name)!r}).write_text('escape')\n print('ESCAPE_ALLOWED')\n"
                "except OSError:\n print('ESCAPE_BLOCKED')\n")
        boundary = result(command(code, sandboxPolicy=terminal_policy))
        assert boundary["exitCode"] == 0 and "ESCAPE_BLOCKED" in boundary["stdout"] and not escaped.exists(), "Workspace junction escaped its write boundary"
        checks.append("junction_write_escape_blocked")

        identifier = command("import sys; print('READY',flush=True); print('ECHO:'+sys.stdin.readline().strip(),flush=True)",
                             processId="input-check", tty=True, size={"rows": 24, "cols": 80}, sandboxPolicy=terminal_policy)
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

        identifier = command("import time; print('RUNNING',flush=True); time.sleep(14)", processId="stop-check", tty=True,
                             sandboxPolicy=terminal_policy)
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
                  "native_sha256": native_sha256,
                  "windows_backend": args.windows_backend, "desktop_gui_exercised": False, "network_or_path_escape_certification": False}
        (fixture / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))
        return 0
    except Exception as exc:
        detail = str(exc)
        report = {"status": "blocked" if "streaming command/exec is not supported with windows sandbox" in detail else "failed",
                  "checks_passed_before_stop": checks, "error": detail, "fixture": str(fixture), "paid_inference": False,
                  "native_sha256": native_sha256,
                  "sanitized_stderr_tail": stderr_lines[-20:],
                  "windows_backend": args.windows_backend,
                  "desktop_gui_exercised": False, "network_or_path_escape_certification": False}
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
