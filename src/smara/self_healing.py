"""Reflect-Act-Verify (RAV) Autonomous Self-Healing Engine for Smara.

Enables the single-agent runtime (CLI & Desktop) to diagnose failed tool
executions, formulate a self-correcting hypothesis, pivot parameters or
methods, and retry autonomously up to a bounded budget without halting.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional


class SelfHealingEngine:
    """Orchestrates the Reflect-Act-Verify loop for autonomous self-correction."""

    def __init__(self, max_attempts: int = 3):
        self.max_attempts = max_attempts

    def diagnose_failure(self, error: str, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Analyze tool error, determine root cause, and generate a remediation strategy."""
        err_lower = error.lower()
        cause = "Unknown runtime error"
        strategy = "Retry with fallback defaults"
        mutated_payload = dict(payload)

        # 1. Path / File Not Found
        if "not found" in err_lower or "no such file" in err_lower or "does not exist" in err_lower:
            cause = "File or directory path was not resolved properly on disk."
            strategy = "Switch to autonomous path resolution or search sibling user directories."
            # If path was in payload, clean quotes or check if path needs location
            if "path" in mutated_payload:
                curr_p = str(mutated_payload["path"]).strip("'\"`")
                mutated_payload["path"] = curr_p
                mutated_payload["operation"] = "locate_and_read"
            elif "folder" in mutated_payload:
                curr_f = str(mutated_payload["folder"]).strip("'\"`")
                mutated_payload["folder"] = curr_f
                mutated_payload["operation"] = "locate_and_read"

        # 2. Syntax / Parser Errors
        elif "syntaxerror" in err_lower or "invalid syntax" in err_lower:
            cause = "Malformed syntax in input script or query."
            strategy = "Normalize code quotes, strip escape artifacts, and validate AST."
            if "code" in mutated_payload:
                code_str = str(mutated_payload["code"]).replace("\\n", "\n")
                mutated_payload["code"] = code_str

        # 3. Key / Missing Argument Errors
        elif "keyerror" in err_lower or "missing required" in err_lower or "parameter" in err_lower:
            cause = "Required key was missing in tool arguments payload."
            strategy = "Inject standard required parameters and defaults."
            if capability == "local_file_read":
                mutated_payload.setdefault("operation", "read_file")
                mutated_payload.setdefault("share_content", True)
            elif capability == "local_file_write":
                mutated_payload.setdefault("operation", "write")
            elif capability == "local_integration":
                mutated_payload.setdefault("provider", "tavily")
                mutated_payload.setdefault("operation", "search")
                mutated_payload.setdefault("max_results", 5)

        # 4. Command Non-Zero Exit / Shell Failure
        elif "exit status" in err_lower or "returncode" in err_lower or "failed with" in err_lower or "command not found" in err_lower:
            cause = "Terminal command exited with an error code or missing binary."
            strategy = "Simplify shell arguments or invoke python with explicit current interpreter."
            if "command" in mutated_payload:
                cmd = str(mutated_payload["command"])
                if cmd.startswith("pytest"):
                    mutated_payload["command"] = f"python -m {cmd} -q --basetemp=.pytest_tmp"
                elif cmd.startswith("python "):
                    # Ensure python is run as module or script safely
                    pass

        # 5. Permission / Access Denied
        elif "access is denied" in err_lower or "permissionerror" in err_lower:
            cause = "Permission denied on system temporary directory or target file."
            strategy = "Redirect destination to local workspace scratch or .smara directory."
            if "path" in mutated_payload:
                mutated_payload["path"] = f".smara/{Path(mutated_payload['path']).name}"

        return {
            "diagnosed_cause": cause,
            "strategy": strategy,
            "mutated_payload": mutated_payload,
        }

    def execute_with_healing(
        self,
        executor_fn: Callable[[str, dict[str, Any], str], dict[str, Any]],
        capability: str,
        initial_payload: dict[str, Any],
        title: str,
        on_reflection: Optional[Callable[[str], None]] = None,
    ) -> dict[str, Any]:
        """Execute capability through the Reflect-Act-Verify (RAV) loop."""
        current_payload = dict(initial_payload)
        reflections: list[dict[str, Any]] = []

        for attempt in range(1, self.max_attempts + 1):
            # Act
            result = executor_fn(capability, current_payload, title)

            # Verify
            is_error = False
            err_msg = ""
            if isinstance(result, dict):
                if result.get("error"):
                    is_error = True
                    err_msg = str(result["error"])
                elif result.get("status") == "failed":
                    is_error = True
                    err_msg = str(result.get("error") or result.get("message") or "Action status failed")
                elif result.get("returncode", 0) != 0 and "output" in result:
                    is_error = True
                    err_msg = f"Non-zero return code ({result['returncode']}): {result.get('output', '')[:200]}"

            if not is_error:
                # Succeeded!
                if attempt > 1:
                    result["_self_healed"] = True
                    result["_attempts"] = attempt
                    result["_reflections"] = reflections
                return result

            # Reflect & Pivot (if we have retries left)
            if attempt < self.max_attempts:
                diagnosis = self.diagnose_failure(err_msg, capability, current_payload)
                refl_text = (
                    f"Attempt {attempt} failed ({err_msg[:80]}...). "
                    f"Diagnosed cause: {diagnosis['diagnosed_cause']} "
                    f"Pivoting strategy: {diagnosis['strategy']}"
                )
                reflections.append({
                    "attempt": attempt,
                    "error": err_msg,
                    "diagnosis": diagnosis["diagnosed_cause"],
                    "strategy": diagnosis["strategy"],
                })

                if on_reflection:
                    on_reflection(refl_text)

                # Mutate payload for next attempt
                current_payload = diagnosis["mutated_payload"]

        # If budget exhausted, return last result with reflection audit
        result["_self_healed"] = False
        result["_attempts"] = self.max_attempts
        result["_reflections"] = reflections
        return result
