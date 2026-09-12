"""Model Context Protocol (MCP) Client for Smara.

Enables standard stdio JSON-RPC 2.0 MCP server integration:
- Auto-discovers mcp.json / .mcp/servers.json in workspace.
- Manages stdio MCP subprocesses with lifecycle control.
- Exposes external tools with typed OpenAI/Sarvam schemas.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
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
        self._lock = threading.Lock()
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
                return False

            # Send initialized notification
            self._send_notification("notifications/initialized", {})

            # 2. List tools
            list_resp = self._send_request("tools/list", {}, timeout=timeout)
            if list_resp and "result" in list_resp:
                raw_tools = list_resp["result"].get("tools", [])
                self.tools = raw_tools
                logger.info(f"Discovered {len(self.tools)} tools from MCP server '{self.name}'")
            return True
        except Exception as e:
            logger.error(f"Failed starting MCP server '{self.name}': {e}")
            self.stop()
            return False

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

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
        with self._lock:
            try:
                line = json.dumps(msg) + "\n"
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
                
                # Read response line
                resp_line = self.proc.stdout.readline()
                if not resp_line:
                    return None
                return json.loads(resp_line.strip())
            except Exception as exc:
                logger.error(f"Error in MCP request {method} to {self.name}: {exc}")
                return None

    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            return
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        with self._lock:
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
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except Exception:
                if self.proc.poll() is None:
                    self.proc.kill()
        self._running = False


class MCPManager:
    """Discovers and routes MCP servers in a Smara workspace."""

    def __init__(self, workspace_root: Path | str):
        self.workspace_root = Path(workspace_root).resolve()
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
                t_name = f"mcp_{server_name}_{tool.get('name')}"
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
        """Dispatch a tool call prefixed with mcp_<server>_<tool>."""
        if not full_tool_name.startswith("mcp_"):
            return None
        
        parts = full_tool_name.split("_", 2)
        if len(parts) < 3:
            return None
        
        server_name = parts[1]
        raw_tool_name = parts[2]
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
