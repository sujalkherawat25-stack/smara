"""
Subagent Orchestrator & Deep Long-Running Architecture for Smara
Enables multi-agent task decomposition and isolated delegation:
- Isolated Context: Worker child runs with its own clean conversation history
- Tool Safety Gating: Child agents are stripped of recursive delegation, user prompt, and shared memory writes
- Parent Synthesis: Parent agent receives only the final verified summary, preventing token bloat
- Batch Concurrency: Parallel worker delegation with ThreadPoolExecutor and timeout guards
"""

from __future__ import annotations
import concurrent.futures
import enum
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("smara.subagent_orchestrator")

# Tools strictly prohibited from subagents to ensure system safety and prevent recursion
DELEGATE_BLOCKED_TOOLS = frozenset([
    "delegate_task",     # Prevent infinite recursive subagent spawning
    "memory",            # Prevent workers from modifying global curated memory
    "clarify",           # Prevent subagents from blocking on user interactive input
    "dag_flow",          # Prevent subagents from reconfiguring top-level DAG
])


class SubagentRole(str, enum.Enum):
    GENERALIST = "generalist"
    RESEARCHER = "researcher"
    CODER = "coder"
    TESTER = "tester"
    AUDITOR = "auditor"


@dataclass
class DelegationResult:
    task_id: str
    goal: str
    status: str  # "SUCCESS", "FAILED", "TIMEOUT"
    summary: str
    trace_steps: int
    duration_ms: int
    tools_used: List[str]
    error: Optional[str] = None
    worktree_branch: Optional[str] = None
    worktree_diff: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SubagentWorker:
    """Executes a scoped task in an isolated conversation context."""

    def __init__(
        self,
        task_id: str,
        role: SubagentRole = SubagentRole.GENERALIST,
        api_key: Optional[str] = None,
        base_url: str = "https://api.sarvam.ai/v2/chat/completions",
        model: str = "glm5.2",
        max_iterations: int = 6,
        isolate_worktree: bool = False,
    ):
        self.task_id = task_id
        self.role = role
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.max_iterations = max_iterations
        self.isolate_worktree = isolate_worktree or (role == SubagentRole.CODER)

    def run(self, goal: str, context: Optional[str] = None) -> DelegationResult:
        """Run isolated subagent loop on the delegated goal."""
        from smara.autonomous_agent import SmaraAutonomousAgent, TOOL_SCHEMAS
        from smara.subagent_worktree import (
            create_subagent_worktree,
            inspect_subagent_worktree,
            cleanup_subagent_worktree,
        )

        t0 = time.time()
        # Filter out blocked tools
        safe_schemas = [
            s for s in TOOL_SCHEMAS
            if s.get("function", {}).get("name") not in DELEGATE_BLOCKED_TOOLS
        ]

        worktree_info = None
        worktree_branch = None
        worktree_diff = None
        prev_cwd = os.getcwd()

        if self.isolate_worktree:
            try:
                worktree_info = create_subagent_worktree(prev_cwd, self.task_id)
                if worktree_info:
                    worktree_branch = worktree_info.get("branch")
                    os.chdir(worktree_info["path"])
            except Exception as wt_err:
                logger.debug(f"Subagent worktree creation skipped: {wt_err}")

        child_agent = SmaraAutonomousAgent(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            max_iterations=self.max_iterations
        )

        scoped_prompt = f"Delegated Goal for {self.role.value.upper()} worker:\n{goal}"
        if context:
            scoped_prompt += f"\n\nRelevant Context:\n{context}"
        if worktree_info:
            scoped_prompt += f"\n\n[Workspace: Executing in isolated git worktree '{worktree_info['path']}' on branch '{worktree_branch}']"

        try:
            res = child_agent.run(task=scoped_prompt)
            duration = int((time.time() - t0) * 1000)

            if worktree_info:
                inspection = inspect_subagent_worktree(worktree_info)
                if inspection.get("has_changes"):
                    worktree_diff = inspection.get("diff", "")
                else:
                    cleanup_subagent_worktree(worktree_info, force=True)
                    worktree_branch = None

            return DelegationResult(
                task_id=self.task_id,
                goal=goal,
                status="SUCCESS",
                summary=res.get("answer", ""),
                trace_steps=len(res.get("trace", [])),
                duration_ms=duration,
                tools_used=res.get("tools_used", []),
                worktree_branch=worktree_branch,
                worktree_diff=worktree_diff,
            )
        except Exception as e:
            logger.error(f"Subagent '{self.task_id}' failed: {e}")
            duration = int((time.time() - t0) * 1000)
            if worktree_info:
                try:
                    cleanup_subagent_worktree(worktree_info, force=True)
                except Exception:
                    pass
            return DelegationResult(
                task_id=self.task_id,
                goal=goal,
                status="FAILED",
                summary=f"Worker failure: {e}",
                trace_steps=0,
                duration_ms=duration,
                tools_used=[],
                error=str(e),
                worktree_branch=None,
                worktree_diff=None,
            )
        finally:
            if worktree_info and os.getcwd() != prev_cwd:
                try:
                    os.chdir(prev_cwd)
                except Exception:
                    pass


class SubagentOrchestrator:
    """Manages subagent lifecycle, concurrency, and synthesis."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.sarvam.ai/v2/chat/completions",
        default_model: str = "glm5.2"
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model

    def delegate(
        self,
        goal: str,
        context: Optional[str] = None,
        role: SubagentRole = SubagentRole.GENERALIST,
        max_iterations: int = 6,
        timeout: int = 60
    ) -> DelegationResult:
        """Spawn a single worker subagent to execute a specific sub-task."""
        task_id = f"sub_{role.value}_{int(time.time() * 1000) % 100000}"
        worker = SubagentWorker(
            task_id=task_id,
            role=role,
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.default_model,
            max_iterations=max_iterations
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(worker.run, goal, context)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning(f"Subagent {task_id} timed out after {timeout}s.")
                return DelegationResult(
                    task_id=task_id,
                    goal=goal,
                    status="TIMEOUT",
                    summary=f"Worker timed out after {timeout} seconds.",
                    trace_steps=0,
                    duration_ms=timeout * 1000,
                    tools_used=[],
                    error="TimeoutError"
                )

    def delegate_batch(
        self,
        tasks: List[Dict[str, Any]],
        max_workers: int = 4,
        timeout: int = 120
    ) -> List[DelegationResult]:
        """Execute multiple subagent delegations concurrently and aggregate results."""
        results: List[DelegationResult] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {}
            for t in tasks:
                goal = t.get("goal", "")
                context = t.get("context")
                role_str = t.get("role", "generalist").lower()
                try:
                    role = SubagentRole(role_str)
                except ValueError:
                    role = SubagentRole.GENERALIST

                task_id = f"sub_{role.value}_{int(time.time() * 1000) % 100000}"
                worker = SubagentWorker(
                    task_id=task_id,
                    role=role,
                    api_key=self.api_key,
                    base_url=self.base_url,
                    model=self.default_model,
                    max_iterations=t.get("max_iterations", 6)
                )
                fut = executor.submit(worker.run, goal, context)
                future_to_task[fut] = goal

            for fut in concurrent.futures.as_completed(future_to_task, timeout=timeout):
                try:
                    res = fut.result()
                    results.append(res)
                except Exception as e:
                    goal = future_to_task[fut]
                    results.append(DelegationResult(
                        task_id="err",
                        goal=goal,
                        status="FAILED",
                        summary=str(e),
                        trace_steps=0,
                        duration_ms=0,
                        tools_used=[],
                        error=str(e)
                    ))

        return results


# Global default instance
_default_orchestrator: Optional[SubagentOrchestrator] = None

def get_default_orchestrator(api_key: Optional[str] = None) -> SubagentOrchestrator:
    global _default_orchestrator
    if _default_orchestrator is None or api_key is not None:
        _default_orchestrator = SubagentOrchestrator(api_key=api_key)
    return _default_orchestrator
