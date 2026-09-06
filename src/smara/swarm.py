"""Real multi-agent orchestration for Smara.

Each role is a real :class:`SubagentWorker` backed by SmaraAutonomousAgent.
Workers receive only the context needed for their role, and coder/tester/
auditor work is isolated in disposable Git worktrees. This module deliberately
does not invent files, test counts, patches, or commit messages.
"""
from __future__ import annotations

import datetime as dt
import enum
import hashlib
import json
import os
import queue
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .code_graph import CodeGraph
from .coding_memory import CodingMemoryEngine
from .dual_plane_memory import DualPlaneMemoryBridge
from .subagent_orchestrator import DelegationResult, SubagentRole, SubagentWorker


class SwarmAgentRole(str, enum.Enum):
    ARCHITECT = "architect"
    IMPLEMENTER = "implementer"
    VERIFIER = "verifier"
    AUDITOR = "auditor"


@dataclass
class SwarmMessage:
    from_role: SwarmAgentRole
    to_role: SwarmAgentRole
    action: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["from_role"] = self.from_role.value
        value["to_role"] = self.to_role.value
        return value


@dataclass
class ArchitectPlan:
    objective: str
    target_symbols: list[str]
    blast_radius: list[str]
    adrs_consulted: list[str]
    conventions_noted: list[str]
    steps: list[str]
    risk_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SwarmTaskResult:
    session_id: str
    objective: str
    status: str
    duration_ms: int
    architect_plan: Optional[ArchitectPlan]
    files_modified: list[str]
    tests_run: int
    tests_passed: int
    healing_applied: bool
    audit_passed: bool
    commit_message: Optional[str]
    inter_agent_messages: list[SwarmMessage]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.architect_plan:
            value["architect_plan"] = self.architect_plan.to_dict()
        value["inter_agent_messages"] = [message.to_dict() for message in self.inter_agent_messages]
        return value


def _worker_config() -> dict[str, str | None]:
    return {
        "api_key": os.getenv("SMARA_AGENT_API_KEY") or os.getenv("SMARA_MODEL_SARVAM_API_KEY") or os.getenv("SARVAM_API_KEY"),
        "base_url": os.getenv("SMARA_AGENT_BASE_URL", "https://api.sarvam.ai/v2/chat/completions"),
        "model": os.getenv("SMARA_AGENT_MODEL", "glm5.2"),
    }


def _files_from_diff(diff: str | None) -> list[str]:
    files: list[str] = []
    for line in (diff or "").splitlines():
        match = re.match(r"\+\+\+ b/(.+)", line)
        if match and match.group(1) not in files:
            files.append(match.group(1).strip())
    return files


class LeadArchitectAgent:
    """Build a plan from real workspace graph and memory observations."""

    def __init__(self, workspace_root: Path):
        self.workspace = workspace_root
        self.memory_bridge = DualPlaneMemoryBridge(self.workspace)
        self.code_graph = CodeGraph(self.workspace)

    def plan_objective(self, objective: str) -> tuple[ArchitectPlan, list[SwarmMessage]]:
        self.code_graph.index()
        tokens = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", objective) if token in self.code_graph.symbols]
        target_symbols = list(dict.fromkeys(tokens[:8]))
        blast_radius: list[str] = []
        for symbol in target_symbols:
            try:
                blast_radius.extend(self.code_graph.calculate_blast_radius(symbol).get("affected_symbols", []))
            except Exception:
                continue
        try:
            self.memory_bridge.recall(objective, top_k=3)
            adrs = [item.title for item in self.memory_bridge.coding_engine.adr_manager.list_adrs()[:3]]
            conventions = self.memory_bridge.coding_engine.convention_learner.get_conventions()
            convention_notes = conventions.key_patterns[:5]
        except Exception:
            adrs, convention_notes = [], []
        risk = "HIGH" if len(set(blast_radius)) > 8 else "MEDIUM" if blast_radius else "LOW"
        plan = ArchitectPlan(
            objective=objective,
            target_symbols=target_symbols,
            blast_radius=list(dict.fromkeys(blast_radius)),
            adrs_consulted=adrs,
            conventions_noted=convention_notes,
            steps=[
                "Inspect the requested objective and relevant symbols with local tools.",
                "Reproduce the observed behavior in the isolated worktree.",
                "Apply the smallest evidence-backed change and capture a diff.",
                "Run focused verification and report exact commands and outcomes.",
                "Audit workspace boundaries, secrets, and regression evidence.",
            ],
            risk_level=risk,
        )
        return plan, [SwarmMessage(SwarmAgentRole.ARCHITECT, SwarmAgentRole.IMPLEMENTER, "HANDOFF_PLAN", {"plan": plan.to_dict()})]


class ImplementerAgent:
    """Run a real coder worker in an isolated Git worktree."""

    def __init__(self, workspace_root: Path):
        self.workspace = workspace_root

    def execute_plan(self, plan: ArchitectPlan) -> tuple[list[str], DelegationResult, list[SwarmMessage]]:
        config = _worker_config()
        worker = SubagentWorker(
            task_id=f"swarm-implementer-{int(time.time() * 1000)}",
            role=SubagentRole.CODER,
            api_key=config["api_key"],
            base_url=str(config["base_url"]),
            model=str(config["model"]),
            max_iterations=12,
            isolate_worktree=True,
            workspace_root=self.workspace,
        )
        result = worker.run(
            goal=(
                f"Implement and verify this objective: {plan.objective}\n"
                "Use only Smara's typed local tools. Inspect before editing; reproduce the issue; make a minimal "
                "patch; run focused checks; and return a precise summary. Do not claim a change without a real diff."
            ),
            context=json.dumps(plan.to_dict(), ensure_ascii=False),
        )
        files = _files_from_diff(result.worktree_diff)
        message = SwarmMessage(
            SwarmAgentRole.IMPLEMENTER,
            SwarmAgentRole.VERIFIER,
            "HANDOFF_IMPLEMENTATION",
            {
                "status": result.status,
                "files_modified": files,
                "worktree_branch": result.worktree_branch,
                "diff_sha256": hashlib.sha256((result.worktree_diff or "").encode()).hexdigest(),
                "error": result.error,
            },
        )
        return files, result, [message]


class VerificationAgent:
    """Have an isolated tester worker verify the implementation evidence."""

    def __init__(self, workspace_root: Path):
        self.workspace = workspace_root

    def verify(self, plan: ArchitectPlan, files: list[str], implementation: DelegationResult) -> tuple[bool, int, int, bool, DelegationResult, list[SwarmMessage]]:
        config = _worker_config()
        worker = SubagentWorker(
            task_id=f"swarm-verifier-{int(time.time() * 1000)}",
            role=SubagentRole.TESTER,
            api_key=config["api_key"],
            base_url=str(config["base_url"]),
            model=str(config["model"]),
            max_iterations=8,
            isolate_worktree=True,
            workspace_root=self.workspace,
        )
        result = worker.run(
            goal=(
                f"Verify the proposed implementation for: {plan.objective}. Review the supplied diff, reproduce the "
                "reported behavior in your isolated worktree when possible, and run focused tests or static checks. "
                "This is verification only: do not edit production files and do not report success without evidence."
            ),
            context=json.dumps({"files": files, "implementation_summary": implementation.summary, "diff": implementation.worktree_diff or ""}, ensure_ascii=False),
        )
        passed = result.status == "SUCCESS"
        message = SwarmMessage(
            SwarmAgentRole.VERIFIER,
            SwarmAgentRole.AUDITOR,
            "HANDOFF_VERIFICATION",
            {"status": result.status, "trace_steps": result.trace_steps, "passed": passed, "error": result.error},
        )
        # The worker reports trace steps, not a fabricated test count.
        return passed, 0, 0, False, result, [message]


class SecurityAuditorAgent:
    """Use an isolated auditor worker; never invent a commit or audit result."""

    def __init__(self, workspace_root: Path):
        self.workspace = workspace_root
        self.coding_engine = CodingMemoryEngine(self.workspace)

    def audit_and_sign(self, plan: ArchitectPlan, files: list[str], verified: bool, evidence: DelegationResult) -> tuple[bool, str, list[SwarmMessage]]:
        if not verified:
            return False, "", [SwarmMessage(SwarmAgentRole.AUDITOR, SwarmAgentRole.ARCHITECT, "AUDIT_BLOCKED", {"reason": "verification_failed", "files_modified": files})]
        config = _worker_config()
        worker = SubagentWorker(
            task_id=f"swarm-auditor-{int(time.time() * 1000)}",
            role=SubagentRole.AUDITOR,
            api_key=config["api_key"],
            base_url=str(config["base_url"]),
            model=str(config["model"]),
            max_iterations=6,
            isolate_worktree=True,
            workspace_root=self.workspace,
        )
        result = worker.run(
            goal=(
                f"Audit the proposed change for: {plan.objective}. Check path boundaries, secret leakage, unsafe "
                "commands, and whether the verification evidence is sufficient. Return a review only; do not commit."
            ),
            context=json.dumps({"files": files, "verification": evidence.summary, "diff": evidence.worktree_diff or ""}, ensure_ascii=False),
        )
        passed = result.status == "SUCCESS"
        return passed, "", [SwarmMessage(SwarmAgentRole.AUDITOR, SwarmAgentRole.ARCHITECT, "AUDIT_RESULT", {"status": result.status, "passed": passed, "review": result.summary, "error": result.error, "commit_created": False})]


class SwarmOrchestrator:
    """Coordinate real role workers through an in-process event queue."""

    def __init__(self, workspace_root: Path | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.architect = LeadArchitectAgent(self.workspace)
        self.implementer = ImplementerAgent(self.workspace)
        self.verifier = VerificationAgent(self.workspace)
        self.auditor = SecurityAuditorAgent(self.workspace)
        self.sessions_path = self.workspace / ".smara" / "swarm_sessions.json"
        self.events: queue.Queue[SwarmMessage] = queue.Queue()

    def run_swarm(self, objective: str, on_event: Optional[Callable[[str, SwarmAgentRole, str], None]] = None) -> SwarmTaskResult:
        started = time.time()
        session_id = f"swarm-{int(time.time())}"
        all_messages: list[SwarmMessage] = []

        def publish(message: SwarmMessage) -> None:
            self.events.put(message)
            all_messages.append(message)

        def notify(role: SwarmAgentRole, status: str, detail: str) -> None:
            if on_event:
                try:
                    on_event(status, role, detail)
                except Exception:
                    pass

        notify(SwarmAgentRole.ARCHITECT, "THINKING", "Inspecting workspace graph and durable coding memory.")
        plan, messages = self.architect.plan_objective(objective)
        for message in messages:
            publish(message)
        notify(SwarmAgentRole.ARCHITECT, "COMPLETED", f"Plan created with {len(plan.target_symbols)} discovered symbols.")

        notify(SwarmAgentRole.IMPLEMENTER, "WORKING", "Running the real coder worker in an isolated Git worktree.")
        files, implementation, messages = self.implementer.execute_plan(plan)
        for message in messages:
            publish(message)
        notify(SwarmAgentRole.IMPLEMENTER, "COMPLETED", f"Worker returned {implementation.status} with {len(files)} changed files.")

        notify(SwarmAgentRole.VERIFIER, "WORKING", "Reviewing the implementation and running focused verification in isolation.")
        verified, tests_run, tests_passed, healed, verification, messages = self.verifier.verify(plan, files, implementation)
        for message in messages:
            publish(message)
        notify(SwarmAgentRole.VERIFIER, "COMPLETED", f"Verification worker returned {verification.status}.")

        notify(SwarmAgentRole.AUDITOR, "WORKING", "Auditing the proposed diff and evidence; no commit is created automatically.")
        audit_ok, _, messages = self.auditor.audit_and_sign(plan, files, verified, verification)
        for message in messages:
            publish(message)
        notify(SwarmAgentRole.AUDITOR, "COMPLETED", "Audit passed." if audit_ok else "Audit blocked the result.")

        status = "SUCCESS" if implementation.status == "SUCCESS" and verified and audit_ok else "FAILED"
        result = SwarmTaskResult(
            session_id=session_id,
            objective=objective,
            status=status,
            duration_ms=int((time.time() - started) * 1000),
            architect_plan=plan,
            files_modified=files,
            tests_run=tests_run,
            tests_passed=tests_passed,
            healing_applied=healed,
            audit_passed=audit_ok,
            commit_message=None,
            inter_agent_messages=all_messages,
        )
        self._record_session(result)
        return result

    def _record_session(self, result: SwarmTaskResult) -> None:
        self.sessions_path.parent.mkdir(parents=True, exist_ok=True)
        history: list[dict[str, Any]] = []
        if self.sessions_path.exists():
            try:
                history = json.loads(self.sessions_path.read_text(encoding="utf-8"))
            except Exception:
                history = []
        history.append(result.to_dict())
        self.sessions_path.write_text(json.dumps(history[-50:], indent=2), encoding="utf-8")

    def get_session_history(self) -> list[dict[str, Any]]:
        if not self.sessions_path.exists():
            return []
        try:
            return json.loads(self.sessions_path.read_text(encoding="utf-8"))
        except Exception:
            return []
