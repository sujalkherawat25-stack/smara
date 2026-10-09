"""Acceptance transport: raw native RPC, no alternate planner or tool executor."""
import json
import os
import queue
import subprocess
import threading
import time
from smara.native_runtime import launch_options, native_binary


class NativeSession:
    def __init__(self, adapter, workspace, home, tools_enabled=False, timeout=120, request_handler=None):
        options, env = launch_options(adapter, home=home, workspace=workspace, tools_enabled=tools_enabled)
        self.process = subprocess.Popen([str(native_binary()), *options, "app-server", "--listen", "stdio://"],
            cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.messages = queue.Queue()
        self.events = []
        self.next_id = 0
        self.timeout = timeout
        # Acceptance-only, explicitly scoped fixture decisions. Normal probes
        # still deny every permission; production Desktop requires the user.
        self.request_handler = request_handler
        def read():
            try:
                for line in self.process.stdout:
                    self.messages.put(json.loads(line))
            finally:
                self.messages.put({"transport_exited": True})
        def drain():
            for _line in self.process.stderr:
                pass
        threading.Thread(target=read, daemon=True).start()
        threading.Thread(target=drain, daemon=True).start()

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def next(self, timeout=None):
        message = self.messages.get(timeout=self.timeout if timeout is None else max(.01, timeout))
        if message.get("transport_exited"):
            raise RuntimeError("Native transport exited")
        self.events.append(message)
        if message.get("id") is not None and message.get("method"):
            method = message["method"]
            if self.request_handler:
                result = self.request_handler(message)
            elif method in {"item/fileChange/requestApproval", "item/commandExecution/requestApproval"}:
                result = {"decision": "decline"}
            elif method == "item/permissions/requestApproval":
                result = {"permissions": {}, "scope": "turn"}
            elif method == "mcpServer/elicitation/request":
                result = {"action": "decline", "content": None, "_meta": None}
            else:
                raise RuntimeError("Unexpected native client request: " + method)
            self.send({"id": message["id"], "result": result})
        return message

    def rpc(self, method, params=None):
        self.next_id += 1
        identifier = self.next_id
        self.send({"id": identifier, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self.next(deadline - time.monotonic())
            if message.get("id") == identifier and not message.get("method"):
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]
        raise TimeoutError(method)

    def initialize(self):
        self.rpc("initialize", {"clientInfo": {"name": "smara_native_acceptance", "version": "0.1.8"}, "capabilities": None})
        self.send({"method": "initialized"})

    def wait(self, predicate, since=0):
        # Notifications can arrive before an RPC acknowledgment. Do not lose
        # them, and do not mistake an earlier turn's completion for this one.
        for message in self.events[since:]:
            if predicate(message):
                return message
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self.next(deadline - time.monotonic())
            if predicate(message):
                return message
        raise TimeoutError("Native acceptance event")

    def close(self, crash=False):
        if self.process.poll() is None:
            if crash:
                self.process.kill()
            else:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
