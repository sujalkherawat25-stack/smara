"""Model Context Protocol (MCP) Client for Smara.

Enables standard stdio JSON-RPC 2.0 MCP server integration:
- Auto-discovers mcp.json / .mcp/servers.json in workspace.
- Manages stdio MCP subprocesses with lifecycle control.
- Exposes external tools with typed OpenAI/Sarvam schemas.
"""
from __future__ import annotations

import json
import io
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MCPServerProcess:
    """Manages a single stdio-based MCP server subprocess."""

    def __init__(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[Path | str] = None,
    ):
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.cwd = Path(cwd).resolve() if cwd else Path.cwd()
        self.proc: Optional[subprocess.Popen] = None
        self.tools: List[Dict[str, Any]] = []
        self._req_id = 0
        self._id_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: Dict[int, Tuple[threading.Event, Dict[str, Any]]] = {}
        # A very fast server (or a buffered test transport) can publish a
        # response between process start and registration of the waiter. Keep
        # a small unmatched buffer so that response is not lost to a race.
        self._unmatched: Dict[int, Dict[str, Any]] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._reader_stop = threading.Event()
        self._running = False

    def start(self, timeout: float = 10.0) -> bool:
        """Start subprocess and perform MCP initialize handshake."""
        full_env = {**os.environ, **self.env}
        cmd = [self.command] + self.args
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(self.cwd),
                env=full_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            self._running = True

            # A single reader owns stdout.  Reading stdout in the request
            # caller made the old client vulnerable to silent servers,
            # notifications, and out-of-order JSON-RPC responses.  The
            # reader now correlates every response by its JSON-RPC id while
            # callers wait on a bounded Event.
            self._reader_stop.clear()
            self._reader_thread = threading.Thread(
                target=self._read_stdout, name=f"smara-mcp-{self.name}-stdout", daemon=True
            )
            self._reader_thread.start()
            # stderr is a separate pipe and must be drained or a chatty MCP
            # server can block once the OS pipe buffer fills.  Test doubles
            # often expose MagicMock streams, so only start this loop for a
            # real IO stream.
            if isinstance(self.proc.stderr, io.IOBase):
                self._stderr_thread = threading.Thread(
                    target=self._drain_stderr, name=f"smara-mcp-{self.name}-stderr", daemon=True
                )
                self._stderr_thread.start()
            
            # 1. Handshake initialize
            init_resp = self._send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "smara-mcp-client", "version": "0.1.0"},
                },
                timeout=timeout,
            )
            if not init_resp or "error" in init_resp:
                logger.warning(f"MCP server {self.name} failed initialize: {init_resp}")
                self.stop()
                return False

            # Send initialized notification
            self._send_notification("notifications/initialized", {})

            # 2. List tools
            list_resp = self._send_request("tools/list", {}, timeout=timeout)
            if not list_resp or "error" in list_resp or "result" not in list_resp:
                logger.warning("MCP server %s failed tools/list: %s", self.name, list_resp)
                self.stop()
                return False
            raw_tools = list_resp["result"].get("tools", [])
            self.tools = raw_tools
            logger.info(f"Discovered {len(self.tools)} tools from MCP server '{self.name}'")
            return True
        except Exception as e:
            logger.error(f"Failed starting MCP server '{self.name}': {e}")
            self.stop()
            return False

    def _next_id(self) -> int:
        with self._id_lock:
            self._req_id += 1
            return self._req_id

    def _read_stdout(self) -> None:
        """Continuously read JSON-RPC responses and resolve waiting calls."""
        stream = self.proc.stdout if self.proc else None
        if stream is None:
            return
        try:
            while not self._reader_stop.is_set():
                try:
                    line = stream.readline()
                except StopIteration:
                    # Some in-memory transports signal temporary exhaustion
                    # with StopIteration. A real pipe uses an empty string for
                    # EOF; keep the reader alive for a later response.
                    time.sleep(0.01)
                    continue
                if not line:
                    break
                try:
                    message = json.loads(line.strip())
                except (TypeError, json.JSONDecodeError):
                    logger.warning("Ignoring malformed MCP message from '%s'", self.name)
                    continue
                if not isinstance(message, dict):
                    continue
                # Notifications have no id and intentionally do not unblock a
                # request.  They are useful for diagnostics but are not
                # exposed as tool results.
                response_id = message.get("id")
                if response_id is None:
                    logger.debug("MCP notification from '%s': %s", self.name, message.get("method"))
                    continue
                with self._pending_lock:
                    waiter = self._pending.get(response_id)
                if waiter is None:
                    with self._pending_lock:
                        if len(self._unmatched) >= 64:
                            self._unmatched.pop(next(iter(self._unmatched)))
                        self._unmatched[response_id] = message
                    logger.debug("Buffering early/unknown MCP response id %s from '%s'", response_id, self.name)
                    continue
                event, slot = waiter
                slot["response"] = message
                event.set()
        except (OSError, ValueError) as exc:
            # A dead process or an exhausted test double is equivalent to EOF.
            logger.debug("MCP stdout reader for '%s' stopped: %s", self.name, exc)
        finally:
            self._running = False

    def _drain_stderr(self) -> None:
        stream = self.proc.stderr if self.proc else None
        if stream is None:
            return
        try:
            while not self._reader_stop.is_set():
                line = stream.readline()
                if not line:
                    break
                logger.debug("MCP[%s] %s", self.name, str(line).rstrip())
        except (OSError, ValueError) as exc:
            logger.debug("MCP stderr reader for '%s' stopped: %s", self.name, exc)

    def _send_request(self, method: str, params: Dict[str, Any], timeout: float = 15.0) -> Optional[Dict[str, Any]]:
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            return None

        req_id = self._next_id()
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        event = threading.Event()
        slot: Dict[str, Any] = {}
        with self._pending_lock:
            self._pending[req_id] = (event, slot)
            early_response = self._unmatched.pop(req_id, None)
        if early_response is not None:
            slot["response"] = early_response
            event.set()
        try:
            with self._write_lock:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
        except Exception as exc:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            logger.error("Error writing MCP request %s to %s: %s", method, self.name, exc)
            return None

        # A silent server must never hang the agent.  Remove the waiter on
        # timeout so a later response cannot be mistaken for a new request.
        if not event.wait(max(0.0, float(timeout))):
            with self._pending_lock:
                self._pending.pop(req_id, None)
            logger.warning("MCP request %s to '%s' timed out after %.2fs", method, self.name, timeout)
            return None
        with self._pending_lock:
            self._pending.pop(req_id, None)
        return slot.get("response")

    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            return
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        with self._write_lock:
            try:
                self.proc.stdin.write(json.dumps(msg) + "\n")
                self.proc.stdin.flush()
            except Exception:
                pass

    def call_tool(self, tool_name: str, arguments: Dict[str, Any], timeout: float = 30.0) -> Dict[str, Any]:
        """Execute a tool call against the MCP server."""
        resp = self._send_request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=timeout,
        )
        if not resp:
            return {"status": "error", "error": f"MCP server '{self.name}' did not respond"}
        if "error" in resp:
            return {"status": "error", "error": str(resp["error"])}
        
        result = resp.get("result", {})
        content = result.get("content", [])
        text_parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                text_parts.append(str(c.get("text", "")))
            elif isinstance(c, str):
                text_parts.append(c)
        return {
            "status": "ok" if not result.get("isError") else "error",
            "output": "\n".join(text_parts) if text_parts else json.dumps(result),
            "is_error": bool(result.get("isError")),
        }

    def stop(self) -> None:
        """Terminate MCP subprocess."""
        self._reader_stop.set()
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except Exception:
                if self.proc.poll() is None:
                    self.proc.kill()
            with self._pending_lock:
                for event, _slot in self._pending.values():
                    event.set()
                self._pending.clear()
                self._unmatched.clear()
        self._running = False


class MCPManager:
    """Discovers and routes MCP servers in a Smara workspace."""

    def __init__(self, workspace_root: Path | str, trusted: Optional[bool] = None):
        self.workspace_root = Path(workspace_root).resolve()
        # ``None`` preserves the low-level manager's historical behaviour for
        # explicit callers/tests.  Application entry points pass an explicit
        # trust decision so repository configuration cannot execute silently.
        self.trusted = trusted
        self.servers: Dict[str, MCPServerProcess] = {}

    def discover_and_load(self) -> Dict[str, MCPServerProcess]:
        """Load mcp.json or .mcp/servers.json if present."""
        config_paths = [
            self.workspace_root / "mcp.json",
            self.workspace_root / ".mcp" / "servers.json",
            self.workspace_root / ".mcp.json",
        ]
        
        target_config = None
        for p in config_paths:
            if p.is_file():
                target_config = p
                break

        if not target_config:
            return self.servers

        if self.trusted is False:
            logger.warning("Ignoring MCP configuration in untrusted workspace: %s", target_config)
            return self.servers

        try:
            data = json.loads(target_config.read_text(encoding="utf-8"))
            server_defs = data.get("mcpServers") or data.get("servers") or {}
            for name, cfg in server_defs.items():
                if not isinstance(cfg, dict):
                    continue
                cmd = cfg.get("command")
                if not cmd:
                    continue
                args = cfg.get("args", [])
                env = cfg.get("env", {})
                server = MCPServerProcess(name, cmd, args, env, cwd=self.workspace_root)
                if server.start():
                    self.servers[name] = server
        except Exception as exc:
            logger.warning(f"Error loading MCP config from {target_config}: {exc}")

        return self.servers

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Convert all loaded MCP server tools to OpenAI function schemas."""
        schemas = []
        for server_name, server in self.servers.items():
            for tool in server.tools:
                raw_name = str(tool.get("name", ""))
                # Keep the historical spelling for simple names, while using
                # a delimiter that cannot be confused with a server-name
                # underscore for names such as ``alpha_beta``.
                t_name = (
                    f"mcp__{server_name}__{raw_name}"
                    if "_" in server_name
                    else f"mcp_{server_name}_{raw_name}"
                )
                schema = {
                    "type": "function",
                    "function": {
                        "name": t_name,
                        "description": f"[MCP: {server_name}] {tool.get('description', '')}",
                        "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                    },
                }
                schemas.append(schema)
        return schemas

    def execute_mcp_tool(self, full_tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        """Dispatch a legacy or canonical ``mcp__server__tool`` call."""
        if not full_tool_name.startswith("mcp_"):
            return None

        # Canonical names are unambiguous even when the server contains one
        # or more underscores.  Resolve against connected servers rather than
        # splitting the untrusted model-provided name.
        server_name = raw_tool_name = None
        if full_tool_name.startswith("mcp__"):
            payload = full_tool_name[len("mcp__"):]
            for candidate_server in self.servers:
                prefix = f"{candidate_server}__"
                if payload.startswith(prefix):
                    server_name = candidate_server
                    raw_tool_name = payload[len(prefix):]
                    break
            if server_name is None or not raw_tool_name:
                return None
        
        # Resolve against the registered tool set first.  This keeps the
        # legacy ``mcp_<server>_<tool>`` public name while correctly handling
        # server names that themselves contain underscores.
        if server_name is None:
            for candidate_server, candidate in self.servers.items():
                prefix = f"mcp_{candidate_server}_"
                if full_tool_name.startswith(prefix):
                    candidate_tool = full_tool_name[len(prefix):]
                    if any(t.get("name") == candidate_tool for t in candidate.tools):
                        server_name, raw_tool_name = candidate_server, candidate_tool
                        break
        if server_name is None:
            parts = full_tool_name.split("_", 2)
            if len(parts) < 3:
                return None
            server_name, raw_tool_name = parts[1], parts[2]
        server = self.servers.get(server_name)
        if not server:
            return f"Error: MCP server '{server_name}' is not connected."
        
        res = server.call_tool(raw_tool_name, args)
        if res.get("status") == "ok":
            return res.get("output", "OK")
        return f"MCP Tool Error ({server_name}:{raw_tool_name}): {res.get('error') or res.get('output')}"

    def shutdown(self) -> None:
        """Stop all running MCP servers."""
        for server in self.servers.values():
            server.stop()
        self.servers.clear()
