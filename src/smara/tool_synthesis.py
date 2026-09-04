"""Dynamic Tool Synthesis Engine for Smara.

Allows the agent to write, verify, smoke-test, register, and execute custom
Python tools on the fly, creating a self-expanding tool library that persists
across sessions.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional


class DynamicToolError(Exception):
    """Raised when dynamic tool synthesis or execution fails."""
    pass


class DynamicToolSynthesizer:
    """Synthesizes, validates, tests, and persists custom tools at runtime."""

    BANNED_FUNCTIONS = {"eval", "exec", "compile", "__subclasses__"}
    BANNED_MODULES = {"shutil.rmtree", "os.system"}

    def __init__(self, workspace: Path | str | None = None):
        ws = Path(workspace).resolve() if workspace else Path.cwd().resolve()
        self.workspace = ws
        self.tools_dir = self.workspace / ".smara" / "tools"
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.tools_dir / "registry.json"
        self._ensure_registry()

    def _ensure_registry(self) -> None:
        if not self.registry_file.exists():
            self._save_registry({})

    def _load_registry(self) -> dict[str, Any]:
        try:
            return json.loads(self.registry_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_registry(self, data: dict[str, Any]) -> None:
        self.registry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def validate_code_ast(self, code: str) -> tuple[bool, str]:
        """Verify Python code syntax and safety policies via AST static analysis."""
        try:
            tree = ast.parse(code)
        except SyntaxError as err:
            return False, f"Syntax error in synthesized tool code: {err}"

        has_run_func = False
        for node in ast.walk(tree):
            # Check for entrypoint function 'run'
            if isinstance(node, ast.FunctionDef) and node.name == "run":
                has_run_func = True
                # Check args
                args = [a.arg for a in node.args.args]
                if not args or args[0] != "payload":
                    return False, "Entrypoint 'run' must accept 'payload' as its first argument."

            # Check banned calls
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in self.BANNED_FUNCTIONS:
                    return False, f"Use of '{node.func.id}' is prohibited for safety reasons."
                if isinstance(node.func, ast.Attribute) and node.func.attr in self.BANNED_FUNCTIONS:
                    return False, f"Attribute access to '{node.func.attr}' is prohibited."

        if not has_run_func:
            return False, "Synthesized code must define an entrypoint function: 'def run(payload: dict) -> dict | Any:'."

        return True, "AST analysis passed cleanly."

    def smoke_test(self, code: str, sample_payload: dict[str, Any]) -> tuple[bool, Any, str]:
        """Execute the code in an isolated module namespace with sample payload."""
        temp_module_name = "_smara_temp_smoke_tool"
        module = importlib.util.module_from_spec(
            importlib.util.spec_from_loader(temp_module_name, loader=None)
        )
        # Sandbox globals
        exec_globals = {
            "__builtins__": __builtins__,
            "json": json,
            "os": os,
            "sys": sys,
            "Path": Path,
        }
        try:
            exec(code, exec_globals)
            run_fn = exec_globals.get("run")
            if not callable(run_fn):
                return False, None, "Entrypoint 'run' is not callable."
            result = run_fn(sample_payload)
            return True, result, "Smoke test passed successfully."
        except Exception as exc:
            return False, None, f"Smoke test runtime failure: {exc}"

    def synthesize_tool(
        self,
        name: str,
        description: str,
        code: str,
        parameters: dict[str, Any] | None = None,
        sample_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate, smoke-test, and register a new custom dynamic tool."""
        clean_name = name.strip().lower().replace("-", "_")
        if not clean_name.isidentifier():
            raise DynamicToolError(f"Tool name '{name}' is not a valid Python identifier.")

        # 1. AST Safety Validation
        ok, msg = self.validate_code_ast(code)
        if not ok:
            raise DynamicToolError(f"AST validation failed: {msg}")

        # 2. Smoke Test Execution
        test_payload = sample_payload or {}
        smoke_ok, test_res, smoke_msg = self.smoke_test(code, test_payload)
        if not smoke_ok:
            raise DynamicToolError(f"Smoke test failed: {smoke_msg}")

        # 3. Persist code file
        tool_file = self.tools_dir / f"{clean_name}.py"
        tool_file.write_text(code, encoding="utf-8")

        # 4. Update registry
        registry = self._load_registry()
        meta = {
            "name": clean_name,
            "description": description.strip(),
            "file": str(tool_file),
            "parameters": parameters or {"type": "object", "properties": {}},
            "sample_output": test_res,
            "status": "active",
        }
        registry[clean_name] = meta
        self._save_registry(registry)

        return {
            "action": "tool_synthesized",
            "name": clean_name,
            "description": description,
            "path": str(tool_file),
            "smoke_test_result": test_res,
            "status": "ready",
        }

    def list_dynamic_tools(self) -> list[dict[str, Any]]:
        """List all synthesized dynamic tools available in the workspace."""
        registry = self._load_registry()
        return list(registry.values())

    def get_tool_metadata(self, name: str) -> Optional[dict[str, Any]]:
        clean_name = name.strip().lower().replace("-", "_")
        return self._load_registry().get(clean_name)

    def execute_dynamic_tool(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute an active registered dynamic tool by name with the given payload."""
        clean_name = name.strip().lower().replace("-", "_")
        meta = self.get_tool_metadata(clean_name)
        if not meta:
            raise DynamicToolError(f"Dynamic tool '{name}' is not registered.")

        tool_file = Path(meta["file"])
        if not tool_file.exists():
            raise DynamicToolError(f"Tool file '{tool_file}' is missing from disk.")

        code = tool_file.read_text(encoding="utf-8")
        exec_globals = {
            "__builtins__": __builtins__,
            "json": json,
            "os": os,
            "sys": sys,
            "Path": Path,
        }
        try:
            exec(code, exec_globals)
            run_fn = exec_globals.get("run")
            if not callable(run_fn):
                raise DynamicToolError(f"Tool '{name}' entrypoint 'run' is not callable.")
            output = run_fn(payload)
            return {
                "action": "dynamic_tool_exec",
                "tool": clean_name,
                "status": "success",
                "result": output,
            }
        except Exception as exc:
            return {
                "action": "dynamic_tool_exec",
                "tool": clean_name,
                "status": "failed",
                "error": str(exc),
            }
