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
    """Validate a model-produced plan, with a transparent no-model fallback."""

    CAPABILITIES = frozenset({
        "deep_research", "local_file_read", "local_file_write", "local_terminal",
        "local_graph", "local_integration", "local_refactor", "local_test_fixer", "local_git",
    })

    @classmethod
    def plan(cls, objective: str, model_reasoner: Callable[[str], Any] | None = None) -> list[GoalStep]:
        if not str(objective).strip():
            raise ValueError("Goal objective cannot be empty.")
        if model_reasoner is not None:
            proposed = model_reasoner(str(objective).strip())
            validated = cls._validate_model_plan(proposed, str(objective).strip())
            if validated:
                return validated
            raise ValueError("The goal planner returned no valid steps.")
        # No keyword-driven fake specialization. Without a model, preserve the
        # objective and create only a bounded inspect/act/verify scaffold.
        return [
            GoalStep("step_1", "Inspect workspace", f"Inspect the workspace and gather evidence for: {objective}", "local_file_read", {"operation": "workspace_snapshot", "objective": objective}),
            GoalStep("step_2", "Execute objective", f"Execute the approved actions required by: {objective}", "local_terminal", {"objective": objective}, ["step_1"]),
            GoalStep("step_3", "Verify outcome", f"Verify the deliverables and evidence for: {objective}", "local_file_read", {"operation": "workspace_snapshot", "objective": objective}, ["step_2"]),
        ]

    @classmethod
    def _validate_model_plan(cls, proposed: Any, objective: str) -> list[GoalStep]:
        if isinstance(proposed, dict):
            proposed = proposed.get("steps")
        if not isinstance(proposed, list):
            return []
        steps: list[GoalStep] = []
        ids: set[str] = set()
        for index, raw in enumerate(proposed, 1):
            if not isinstance(raw, dict):
                continue
            step_id = str(raw.get("id") or f"step_{index}").strip()
            capability = str(raw.get("capability") or "").strip()
            title = str(raw.get("title") or raw.get("objective") or f"Step {index}").strip()
            if not step_id or step_id in ids or capability not in cls.CAPABILITIES:
                continue
            deps = [str(dep).strip() for dep in raw.get("dependencies", []) if str(dep).strip()] if isinstance(raw.get("dependencies", []), list) else []
            steps.append(GoalStep(step_id, title, str(raw.get("objective") or objective), capability, dict(raw.get("payload") or {}), deps))
            ids.add(step_id)
        if not steps:
            return []
        # Reject missing dependencies and cycles before persisting a session.
        graph = {step.id: step.dependencies for step in steps}
        if any(dep not in ids for deps in graph.values() for dep in deps):
            raise ValueError("The goal planner returned an unknown dependency.")
        visiting: set[str] = set()
        visited: set[str] = set()
        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("The goal planner returned a dependency cycle.")
            if node in visited:
                return
            visiting.add(node)
            for dep in graph[node]:
                visit(dep)
            visiting.remove(node)
            visited.add(node)
        for node in graph:
            visit(node)
        return steps


class GoalRunner:
    """Manages stateful, resumable execution of long-horizon goals with step checkpointing."""

    def __init__(self, workspace: Path | str | None = None, planner: GoalPlanner | None = None):
        self.workspace = (Path(workspace) if workspace else Path.cwd()).resolve()
        self.goals_dir = self.workspace / ".smara" / "goals"
        self.goals_dir.mkdir(parents=True, exist_ok=True)
        self.planner = planner or GoalPlanner()

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
        model_reasoner: Callable[[str], Any] | None = None,
    ) -> GoalSession:
        """Run long-horizon goal loop with step checkpointing and dependency resolution."""
        gid = goal_id or f"goal_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        
        # Check if resuming existing session
        session = self.load_session(gid)
        if not session:
            steps = self.planner.plan(objective, model_reasoner=model_reasoner)
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
