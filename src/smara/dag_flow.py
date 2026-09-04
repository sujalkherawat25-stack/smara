"""
Interactive DAG Flow Engine for Smara
Provides robust, multi-stage Directed Acyclic Graph orchestration:
- Arbitrary branching, joins, and parallel execution paths
- Cycle detection via Kahn's algorithm with topological ordering
- Interactive controls: step, run, pause, resume, retry_node, inject_node
- Node status lifecycle: PENDING -> READY -> RUNNING -> COMPLETED / FAILED / BLOCKED
- Visual ASCII diagram rendering and JSON serialization for UI/TUI
"""

from __future__ import annotations
import collections
import datetime as dt
import enum
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("smara.dag_flow")


class NodeStatus(str, enum.Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class CycleDetectedError(Exception):
    """Raised when a circular dependency is detected in the DAG."""
    pass


@dataclass
class DAGNode:
    id: str
    title: str
    capability: str
    payload: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    duration_ms: int = 0
    retries: int = 0
    max_retries: int = 2

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DAGNode:
        data_copy = dict(data)
        if "status" in data_copy and isinstance(data_copy["status"], str):
            data_copy["status"] = NodeStatus(data_copy["status"])
        return cls(**data_copy)


class DAGWorkflow:
    """Interactive Directed Acyclic Graph workflow manager."""

    def __init__(self, workflow_id: Optional[str] = None, title: str = "Smara DAG Workflow"):
        self.id = workflow_id or f"dag_{int(time.time())}"
        self.title = title
        self.nodes: Dict[str, DAGNode] = {}
        self.is_paused: bool = False

    def add_node(self, node: DAGNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"Node with id '{node.id}' already exists in workflow.")
        self.nodes[node.id] = node

    def get_node(self, node_id: str) -> Optional[DAGNode]:
        return self.nodes.get(node_id)

    def validate(self) -> List[str]:
        """
        Validate graph using Kahn's algorithm for topological sorting and cycle detection.
        Returns the valid topological order of node IDs, or raises CycleDetectedError.
        """
        in_degree: Dict[str, int] = {node_id: 0 for node_id in self.nodes}
        adj: Dict[str, List[str]] = collections.defaultdict(list)

        for node_id, node in self.nodes.items():
            for dep in node.depends_on:
                if dep not in self.nodes:
                    raise ValueError(f"Node '{node_id}' depends on non-existent node '{dep}'.")
                adj[dep].append(node_id)
                in_degree[node_id] += 1

        queue = collections.deque([n for n, deg in in_degree.items() if deg == 0])
        topological_order = []

        while queue:
            curr = queue.popleft()
            topological_order.append(curr)
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(topological_order) != len(self.nodes):
            remaining = [n for n, deg in in_degree.items() if deg > 0]
            raise CycleDetectedError(f"Circular dependency detected involving nodes: {remaining}")

        return topological_order

    def update_node_readiness(self) -> None:
        """Mark PENDING nodes whose dependencies are all COMPLETED as READY, or BLOCKED if a dep FAILED."""
        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue

            deps = [self.nodes[d] for d in node.depends_on]
            if any(d.status in (NodeStatus.FAILED, NodeStatus.BLOCKED) for d in deps):
                node.status = NodeStatus.BLOCKED
                node.error = "Blocked by upstream dependency failure."
            elif all(d.status == NodeStatus.COMPLETED for d in deps):
                node.status = NodeStatus.READY

    def get_ready_nodes(self) -> List[DAGNode]:
        """Return all nodes currently in READY status."""
        self.update_node_readiness()
        return [n for n in self.nodes.values() if n.status == NodeStatus.READY]

    def step(self, executor_callback: Callable[[DAGNode], Any]) -> List[DAGNode]:
        """
        Execute one step: finds all currently READY nodes, executes them,
        records outputs, and updates graph states.
        """
        if self.is_paused:
            logger.info("Workflow execution is paused.")
            return []

        ready_nodes = self.get_ready_nodes()
        if not ready_nodes:
            return []

        executed = []
        for node in ready_nodes:
            node.status = NodeStatus.RUNNING
            t0 = time.time()
            try:
                result = executor_callback(node)
                node.result = result
                node.status = NodeStatus.COMPLETED
                node.error = None
            except Exception as e:
                logger.error(f"Node '{node.id}' execution failed: {e}")
                node.error = str(e)
                if node.retries < node.max_retries:
                    node.retries += 1
                    node.status = NodeStatus.READY
                    logger.info(f"Retrying node '{node.id}' ({node.retries}/{node.max_retries})")
                else:
                    node.status = NodeStatus.FAILED
            finally:
                node.duration_ms = int((time.time() - t0) * 1000)
                executed.append(node)

        self.update_node_readiness()
        return executed

    def run_until_complete(
        self,
        executor_callback: Callable[[DAGNode], Any],
        max_steps: int = 100
    ) -> Dict[str, Any]:
        """Run workflow stepping until all nodes reach terminal state or max_steps reached."""
        self.validate()
        steps_taken = 0

        while steps_taken < max_steps and not self.is_paused:
            executed = self.step(executor_callback)
            if not executed:
                break
            steps_taken += 1

        is_done = all(
            n.status in (NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.BLOCKED, NodeStatus.SKIPPED)
            for n in self.nodes.values()
        )
        has_failures = any(n.status == NodeStatus.FAILED for n in self.nodes.values())

        return {
            "workflow_id": self.id,
            "title": self.title,
            "is_complete": is_done,
            "has_failures": has_failures,
            "steps_taken": steps_taken,
            "node_statuses": {nid: n.status.value for nid, n in self.nodes.items()}
        }

    def inject_node(
        self,
        new_node: DAGNode,
        after_node_id: Optional[str] = None,
        before_node_id: Optional[str] = None
    ) -> None:
        """Dynamically insert a node into the graph (e.g. test healing or diagnostic branch)."""
        if after_node_id and after_node_id in self.nodes:
            if after_node_id not in new_node.depends_on:
                new_node.depends_on.append(after_node_id)

        if before_node_id and before_node_id in self.nodes:
            target = self.nodes[before_node_id]
            if after_node_id and after_node_id in target.depends_on:
                target.depends_on.remove(after_node_id)
            if new_node.id not in target.depends_on:
                target.depends_on.append(new_node.id)

        self.add_node(new_node)
        self.validate()
        self.update_node_readiness()

    def retry_node(self, node_id: str) -> None:
        """Reset a failed node and all its downstream descendants back to PENDING."""
        if node_id not in self.nodes:
            return

        downstream = collections.defaultdict(list)
        for nid, n in self.nodes.items():
            for dep in n.depends_on:
                downstream[dep].append(nid)

        to_reset = set()
        queue = [node_id]
        while queue:
            curr = queue.pop(0)
            to_reset.add(curr)
            for child in downstream[curr]:
                if child not in to_reset:
                    queue.append(child)

        for nid in to_reset:
            n = self.nodes[nid]
            n.status = NodeStatus.PENDING
            n.error = None
            n.result = None
            n.retries = 0

        self.update_node_readiness()

    def render_ascii(self) -> str:
        """Render a readable text diagram of graph progress."""
        lines = [f"=== Workflow: {self.title} ({self.id}) ==="]
        for node_id, node in self.nodes.items():
            status_icon = {
                NodeStatus.COMPLETED: "[OK]",
                NodeStatus.FAILED: "[FAIL]",
                NodeStatus.RUNNING: "[RUN]",
                NodeStatus.READY: "[READY]",
                NodeStatus.PENDING: "[PEND]",
                NodeStatus.BLOCKED: "[BLCK]",
                NodeStatus.SKIPPED: "[SKIP]",
            }.get(node.status, "[?]")

            deps_str = f" <- ({', '.join(node.depends_on)})" if node.depends_on else ""
            timing = f" ({node.duration_ms}ms)" if node.duration_ms > 0 else ""
            lines.append(f"  {status_icon} {node.id}: {node.title} [{node.capability}]{deps_str}{timing}")
            if node.error:
                lines.append(f"       Error: {node.error}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "is_paused": self.is_paused,
            "nodes": [n.to_dict() for n in self.nodes.values()]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DAGWorkflow:
        wf = cls(workflow_id=data.get("id"), title=data.get("title", "Smara DAG Workflow"))
        wf.is_paused = data.get("is_paused", False)
        for nd in data.get("nodes", []):
            wf.add_node(DAGNode.from_dict(nd))
        return wf
