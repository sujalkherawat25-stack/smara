"""Autonomous Long-Horizon Goal Engine for Smara.

Executes complex, open-ended objectives unattended across multi-step DAG
dependency graphs with durable step-by-step checkpointing, error recovery,
and stop condition evaluation.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class GoalStep:
    id: str
    title: str
    objective: str
    capability: str
    payload: dict[str, Any]
    dependencies: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | running | completed | failed | skipped
    evidence: Any = None
    duration_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalStep:
        return cls(**data)


@dataclass
class GoalSession:
    goal_id: str
    objective: str
    status: str  # created | running | completed | failed
    created_at: float
    updated_at: float
    steps: list[GoalStep]
    final_deliverable: Optional[str] = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["steps"] = [s.to_dict() if isinstance(s, GoalStep) else s for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalSession:
        raw_steps = data.get("steps", [])
        steps = [GoalStep.from_dict(s) if isinstance(s, dict) else s for s in raw_steps]
        data_copy = dict(data)
        data_copy["steps"] = steps
        return cls(**data_copy)


class GoalPlanner:
    """Decomposes high-level objectives into ordered, dependency-tracked GoalSteps."""

    @staticmethod
    def plan(objective: str) -> list[GoalStep]:
        obj_lower = objective.lower()

        # 1. Market Research & Deep Analysis Objective
        if any(k in obj_lower for k in ["market", "research", "competitor", "pricing", "compute", "industry", "landscape"]):
            return [
                GoalStep(
                    id="step_1",
                    title="Multi-Vector Research Fan-Out",
                    objective=f"Formulate orthogonal research vectors and queries for: {objective}",
                    capability="deep_research",
                    payload={"operation": "fan_out_queries", "topic": objective},
                    dependencies=[],
                ),
                GoalStep(
                    id="step_2",
                    title="Multi-Source Neural Search & Retrieval",
                    objective="Query live neural search engines and fetch primary industry reports",
                    capability="deep_research",
                    payload={"operation": "retrieve_sources", "topic": objective},
                    dependencies=["step_1"],
                ),
                GoalStep(
                    id="step_3",
                    title="Primary Source Deep Scraping & Fact Triangulation",
                    objective="Scrape key target web domains, documentation, and pricing sheets via Browser Engine",
                    capability="deep_research",
                    payload={"operation": "scrape_primary_evidence", "topic": objective},
                    dependencies=["step_2"],
                ),
                GoalStep(
                    id="step_4",
                    title="Quantitative Market & Competitive Synthesis",
                    objective="Synthesize competitive landscape, hardware supply chain, unit economics, and bottlenecks",
                    capability="deep_research",
                    payload={"operation": "synthesize_analysis", "topic": objective},
                    dependencies=["step_3"],
                ),
                GoalStep(
                    id="step_5",
                    title="Executive Deliverable Compilation",
                    objective="Compile full verified executive report and save artifact to reports/",
                    capability="local_file_write",
                    payload={"operation": "compile_report", "topic": objective},
                    dependencies=["step_4"],
                ),
            ]

        # 2. Code Engineering & Refactoring Objective
        if any(k in obj_lower for k in ["refactor", "build", "implement", "fix", "test", "rewrite", "migrate"]):
            return [
                GoalStep(
                    id="step_1",
                    title="AST Blast Radius & Dependency Inspection",
                    objective="Map affected symbols and call graphs before mutation",
                    capability="local_graph",
                    payload={"operation": "inspect_symbol", "symbol": "CodePropertyGraph"},
                    dependencies=[],
                ),
                GoalStep(
                    id="step_2",
                    title="Pre-Flight Rollback Snapshot",
                    objective="Capture pristine workspace snapshot in .smara/snapshots/",
                    capability="local_refactor",
                    payload={"operation": "snapshot", "description": objective[:50]},
                    dependencies=["step_1"],
                ),
                GoalStep(
                    id="step_3",
                    title="Scoped Code Mutation & AST Verification",
                    objective="Apply code mutations with syntax tree validation",
                    capability="local_file_write",
                    payload={"operation": "mutate_code", "objective": objective},
                    dependencies=["step_2"],
                ),
                GoalStep(
                    id="step_4",
                    title="Test Suite Execution & Traceback Healing",
                    objective="Run pytest test suite and auto-fix any failing assertions",
                    capability="local_test_fixer",
                    payload={"operation": "run_tests"},
                    dependencies=["step_3"],
                ),
                GoalStep(
                    id="step_5",
                    title="Audit Signing & Semantic Commit",
                    objective="Verify security bounds and generate AI conventional commit",
                    capability="local_git",
                    payload={"operation": "smart_commit", "message": f"feat: {objective[:60]}"},
                    dependencies=["step_4"],
                ),
            ]

        # 3. General Multi-Step Autonomous Workflow
        return [
            GoalStep(
                id="step_1",
                title="Workspace & Resource Discovery",
                objective=f"Analyze current state and gather initial context for: {objective}",
                capability="local_file_read",
                payload={"operation": "git_summary"},
                dependencies=[],
            ),
            GoalStep(
                id="step_2",
                title="Action Plan Execution",
                objective="Execute core actions to fulfill target goal",
                capability="local_terminal",
                payload={"command": "python -V"},
                dependencies=["step_1"],
            ),
            GoalStep(
                id="step_3",
                title="Verification & Quality Audit",
                objective="Validate deliverable criteria and record evidence",
                capability="local_file_read",
                payload={"operation": "workspace_snapshot"},
                dependencies=["step_2"],
            ),
        ]


class GoalRunner:
    """Manages stateful, resumable execution of long-horizon goals with step checkpointing."""

    def __init__(self, workspace: Path | str | None = None):
        self.workspace = (Path(workspace) if workspace else Path.cwd()).resolve()
        self.goals_dir = self.workspace / ".smara" / "goals"
        self.goals_dir.mkdir(parents=True, exist_ok=True)

    def _session_file(self, goal_id: str) -> Path:
        return self.goals_dir / f"{goal_id}.json"

    def save_checkpoint(self, session: GoalSession) -> None:
        """Durable checkpoint to disk after every step mutation."""
        session.updated_at = time.time()
        p = self._session_file(session.goal_id)
        p.write_text(json.dumps(session.to_dict(), indent=2), encoding="utf-8")

    def load_session(self, goal_id: str) -> Optional[GoalSession]:
        p = self._session_file(goal_id)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return GoalSession.from_dict(data)
        except Exception:
            return None

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = []
        for f in self.goals_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sessions.append({
                    "goal_id": data.get("goal_id", f.stem),
                    "objective": data.get("objective", ""),
                    "status": data.get("status", "unknown"),
                    "steps_completed": sum(1 for s in data.get("steps", []) if s.get("status") == "completed"),
                    "total_steps": len(data.get("steps", [])),
                    "updated_at": data.get("updated_at", 0),
                })
            except Exception:
                continue
        return sorted(sessions, key=lambda x: x["updated_at"], reverse=True)

    def execute_goal(
        self,
        objective: str,
        executor_fn: Callable[[str, dict[str, Any], str], dict[str, Any]],
        on_event: Optional[Callable[[str, GoalStep, str], None]] = None,
        goal_id: Optional[str] = None,
    ) -> GoalSession:
        """Run long-horizon goal loop with step checkpointing and dependency resolution."""
        gid = goal_id or f"goal_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        
        # Check if resuming existing session
        session = self.load_session(gid)
        if not session:
            steps = GoalPlanner.plan(objective)
            session = GoalSession(
                goal_id=gid,
                objective=objective,
                status="running",
                created_at=time.time(),
                updated_at=time.time(),
                steps=steps,
                metrics={"total_steps": len(steps), "completed_steps": 0},
            )
            self.save_checkpoint(session)
        else:
            session.status = "running"
            self.save_checkpoint(session)

        completed_step_ids = {s.id for s in session.steps if s.status == "completed"}

        for step in session.steps:
            # Skip already completed steps
            if step.status == "completed":
                continue

            # Check dependencies
            unresolved = [d for d in step.dependencies if d not in completed_step_ids]
            if unresolved:
                step.status = "failed"
                step.error = f"Unresolved dependencies: {unresolved}"
                self.save_checkpoint(session)
                if on_event:
                    on_event("step_failed", step, f"Missing prerequisite steps: {unresolved}")
                session.status = "failed"
                self.save_checkpoint(session)
                return session

            # Run step
            step.status = "running"
            self.save_checkpoint(session)
            if on_event:
                on_event("step_start", step, f"Executing {step.title}...")

            t0 = time.time()
            try:
                output = executor_fn(step.capability, step.payload, step.title)
                duration_ms = int((time.time() - t0) * 1000)
                step.duration_ms = duration_ms

                # Verify result
                is_failed = False
                if isinstance(output, dict):
                    if output.get("error") or output.get("status") == "failed":
                        is_failed = True
                        step.error = str(output.get("error") or output.get("message") or "Step execution failed")

                if is_failed:
                    step.status = "failed"
                    self.save_checkpoint(session)
                    if on_event:
                        on_event("step_failed", step, f"Failed: {step.error}")
                    session.status = "failed"
                    self.save_checkpoint(session)
                    return session

                # Mark complete
                step.status = "completed"
                step.evidence = output
                completed_step_ids.add(step.id)
                session.metrics["completed_steps"] = len(completed_step_ids)
                self.save_checkpoint(session)

                if on_event:
                    on_event("step_complete", step, f"Completed in {duration_ms}ms")

            except Exception as exc:
                step.status = "failed"
                step.error = str(exc)
                step.duration_ms = int((time.time() - t0) * 1000)
                self.save_checkpoint(session)
                if on_event:
                    on_event("step_failed", step, f"Exception: {exc}")
                session.status = "failed"
                self.save_checkpoint(session)
                return session

        # All steps completed successfully
        session.status = "completed"
        # Check if there is an executive report or final deliverable
        for s in reversed(session.steps):
            if isinstance(s.evidence, dict) and s.evidence.get("report_path"):
                session.final_deliverable = s.evidence.get("report_path")
                break
        self.save_checkpoint(session)

        if on_event:
            on_event("goal_complete", session.steps[-1], f"All {len(session.steps)} steps satisfied")

        return session
