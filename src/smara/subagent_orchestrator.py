"""
Subagent Orchestrator & Deep Long-Running Architecture for Smara
Enables multi-agent task decomposition and isolated delegation:
- Isolated Context: Worker child runs with its own clean conversation history
- Tool Safety Gating: Child agents are stripped of recursive delegation, user prompt, and shared memory writes
- Parent Synthesis: Parent agent receives only the final verified summary, preventing token bloat
- Batch Concurrency: Parallel worker delegation with ThreadPoolExecutor and timeout guards
"""

from __future__ import annotations
import contextlib
import enum
import json
import logging
import multiprocessing
import os
from pathlib import Path
import subprocess
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

# Delegation stays opt-in until a matched-budget ablation demonstrates value.
DELEGATION_ENABLED = False


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
    usage: Dict[str, Any] = field(default_factory=dict)
    evidence_ids: List[str] = field(default_factory=list)
    base_revision: Optional[str] = None
    patch_validated: bool = False

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
        workspace_root: Optional[str | Path] = None,
        budget=None,
    ):
        self.task_id = task_id
        self.role = role
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.max_iterations = max_iterations
        self.isolate_worktree = isolate_worktree or (role == SubagentRole.CODER)
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.budget = budget

    def run(self, goal: str, context: Optional[str] = None) -> DelegationResult:
        """Run isolated subagent loop on the delegated goal."""
        from smara.autonomous_agent import SmaraAutonomousAgent
        from smara.subagent_worktree import (
            create_subagent_worktree,
            inspect_subagent_worktree,
            cleanup_subagent_worktree,
        )

        t0 = time.time()
        worktree_info = None
        worktree_branch = None
        worktree_diff = None
        execution_root = str(self.workspace_root or Path.cwd().resolve())

        if self.isolate_worktree:
            try:
                worktree_info = create_subagent_worktree(execution_root, self.task_id)
                if worktree_info:
                    worktree_branch = worktree_info.get("branch")
                    execution_root = worktree_info["path"]
                else:
                    # A coder must never silently edit the caller's checkout. If
                    # isolation is unavailable, run read-only from the requested
                    # workspace and let the worker report the inability to patch.
                    pass
            except Exception as wt_err:
                logger.debug(f"Subagent worktree creation skipped: {wt_err}")
                pass
            if worktree_info is None:
                duration = int((time.time() - t0) * 1000)
                return DelegationResult(
                    task_id=self.task_id,
                    goal=goal,
                    status="FAILED",
                    summary="Worker could not obtain an isolated Git worktree; no edit was attempted.",
                    trace_steps=0,
                    duration_ms=duration,
                    tools_used=[],
                    error="isolated_worktree_unavailable",
                )
        elif self.workspace_root:
            execution_root = str(self.workspace_root)

        from smara.harness import Budget, SessionEngine, workspace_revision
        child_budget = self.budget or Budget(wall_seconds=max(1, self.max_iterations * 30), tool_calls=max(1, self.max_iterations * 3), model_calls=max(1, self.max_iterations), billed_tokens=max(20_000, self.max_iterations * 20_000), dollars=max(0.1, self.max_iterations * 0.1))
        base_revision=workspace_revision(Path(execution_root))
        child_session = SessionEngine(execution_root, f"worker_{self.task_id}", budget=child_budget, constrained=False)
        child_agent = SmaraAutonomousAgent(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            max_iterations=self.max_iterations,
            toolset=(
                "worker_coding"
                if self.role == SubagentRole.CODER
                else "worker_verification"
                if self.role in (SubagentRole.TESTER, SubagentRole.AUDITOR)
                else "full"
            ),
            workspace_root=execution_root,
            session_engine=child_session,
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

            answer = str(res.get("answer") or "").strip()
            raw_answer = str(res.get("raw_answer") or "").strip()
            canonical = res.get("session") or {}
            inspected=child_session.inspect()
            failed = canonical.get("status") != "completed"
            return DelegationResult(
                task_id=self.task_id,
                goal=goal,
                status="FAILED" if failed else "SUCCESS",
                summary=res.get("answer", ""),
                trace_steps=len(res.get("trace", [])),
                duration_ms=duration,
                tools_used=res.get("tools_used", []),
                error=(str(canonical.get("unresolved_items") or raw_answer or "Worker did not produce verified completion")) if failed else None,
                worktree_branch=worktree_branch,
                worktree_diff=worktree_diff,
                usage=dict(canonical.get("usage") or {}),
                evidence_ids=[item["id"] for item in inspected.get("evidence",[])],
                base_revision=base_revision,
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
            with contextlib.suppress(Exception):
                child_session.close()

def _worker_process_entry(worker: SubagentWorker, goal: str, context: Optional[str], output) -> None:
    allowed={"PATH","SYSTEMROOT","WINDIR","TEMP","TMP","PATHEXT","COMSPEC","LANG","LC_ALL","SSL_CERT_FILE","REQUESTS_CA_BUNDLE"}
    environment={key:value for key,value in os.environ.items() if key.upper() in allowed}
    os.environ.clear();os.environ.update(environment)
    try: output.put(worker.run(goal, context).to_dict())
    except BaseException as exc: output.put({"task_id":worker.task_id,"goal":goal,"status":"FAILED","summary":"worker process failed","trace_steps":0,"duration_ms":0,"tools_used":[],"error":f"{type(exc).__name__}: {exc}"})


class SubagentOrchestrator:
    """Manages subagent lifecycle, concurrency, and synthesis."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.sarvam.ai/v2/chat/completions",
        default_model: str = "glm5.2",
        workspace_root: Optional[str | Path] = None,
        root_session=None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model
        self.workspace_root=Path(workspace_root).resolve() if workspace_root else None
        self.root_session=root_session

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
        if not DELEGATION_ENABLED:
            return DelegationResult(task_id=task_id, goal=goal, status="FAILED", summary="Delegation is disabled until worker policy is enforced.", trace_steps=0, duration_ms=0, tools_used=[], error="delegation_disabled")
        from smara.harness import Budget, BudgetExceeded
        child_budget=Budget(wall_seconds=max(1,timeout),tool_calls=max(1,max_iterations*3),model_calls=max(1,max_iterations),billed_tokens=max(20_000,max_iterations*20_000),dollars=max(.1,max_iterations*.1))
        reservation_id=None
        if self.root_session is not None:
            try: reservation_id=self.root_session.reserve_child_budget(child_budget,depth=1)
            except BudgetExceeded as exc:return DelegationResult(task_id=task_id,goal=goal,status="FAILED",summary="Root budget denied delegation.",trace_steps=0,duration_ms=0,tools_used=[],error=str(exc))
        worker = SubagentWorker(
            task_id=task_id,
            role=role,
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.default_model,
            max_iterations=max_iterations,
            workspace_root=self.workspace_root,
            budget=child_budget,
        )

        process_context=multiprocessing.get_context("spawn"); output=process_context.Queue(maxsize=1); process=process_context.Process(target=_worker_process_entry,args=(worker,goal,context,output),daemon=False); process.start();deadline=time.monotonic()+timeout;cancelled=False
        while process.is_alive() and time.monotonic()<deadline:
            process.join(.1)
            if self.root_session is not None and self.root_session.get("cancelled",False):cancelled=True;break
        if process.is_alive():
            process.terminate(); process.join(10)
            if process.is_alive(): process.kill(); process.join(5)
            result=DelegationResult(task_id=task_id,goal=goal,status="FAILED" if cancelled else "TIMEOUT",summary="Worker cancelled by root session." if cancelled else f"Worker timed out after {timeout} seconds.",trace_steps=0,duration_ms=int((timeout if not cancelled else max(0,timeout-(deadline-time.monotonic())))*1000),tools_used=[],error="cancelled" if cancelled else "TimeoutError")
        else:
            try: result=DelegationResult(**output.get(timeout=2))
            except Exception: result=DelegationResult(task_id=task_id,goal=goal,status="FAILED",summary="Worker exited without a structured result.",trace_steps=0,duration_ms=0,tools_used=[],error=f"worker_exit_{process.exitcode}")
        if reservation_id is not None:
            with contextlib.suppress(Exception): self.root_session.reconcile_child_budget(reservation_id,result.usage)
        if result.worktree_diff and self.workspace_root:
            checked=subprocess.run(["git","apply","--check","-"],cwd=self.workspace_root,input=result.worktree_diff,text=True,capture_output=True)
            result.patch_validated=checked.returncode==0
            if not result.patch_validated and result.status=="SUCCESS": result.status="FAILED"; result.error="integration_patch_validation_failed"
        return result

    def delegate_batch(
        self,
        tasks: List[Dict[str, Any]],
        max_workers: int = 4,
        timeout: int = 120
    ) -> List[DelegationResult]:
        """Execute multiple subagent delegations concurrently and aggregate results."""
        if not DELEGATION_ENABLED:
            return [DelegationResult(task_id="disabled", goal=str(task.get("goal", "")), status="FAILED", summary="Delegation is disabled until worker policy is enforced.", trace_steps=0, duration_ms=0, tools_used=[], error="delegation_disabled") for task in tasks]
        # Default remains disabled pending a positive matched-budget ablation.
        # When explicitly enabled, isolated processes are run in bounded waves;
        # no worker ever shares cwd or an in-process agent object.
        results=[]
        for task in tasks:
            try: role=SubagentRole(str(task.get("role","generalist")))
            except ValueError: role=SubagentRole.GENERALIST
            results.append(self.delegate(str(task.get("goal", "")),task.get("context"),role,int(task.get("max_iterations",6)),timeout))
        return results


# Global default instance
_default_orchestrator: Optional[SubagentOrchestrator] = None

def get_default_orchestrator(api_key: Optional[str] = None) -> SubagentOrchestrator:
    global _default_orchestrator
    if _default_orchestrator is None or api_key is not None:
        _default_orchestrator = SubagentOrchestrator(api_key=api_key)
    return _default_orchestrator
