"""Dual-Plane Memory Bridge for Smara.

Unifies:
- Plane 1 (Local): Offline SQLite vector store (.smara/semantic_index.db) for
  instant code symbol & chunk recall with zero external dependencies.
- Plane 2 (Syntarus): Optional account- and workspace-scoped SDK memory for
  long-term cross-session architectural decisions, conventions, and project history.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


from .coding_memory import CodingMemoryEngine
from .semantic_search import SemanticCodeSearcher
from .skill_learner import SkillLearnerEngine


@dataclass
class PlaneStatus:
    name: str
    plane_type: str  # "local_sqlite" | "continuum_syntarus"
    status: str      # "active" | "connected" | "standby" | "unconfigured"
    endpoint: str
    items_count: int
    details: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DualPlaneStatus:
    plane_1_local: PlaneStatus
    plane_2_continuum: PlaneStatus
    bridge_active: bool
    last_sync_time: str | None
    total_memories_synced: int

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["plane_1_local"] = self.plane_1_local.to_dict()
        d["plane_2_continuum"] = self.plane_2_continuum.to_dict()
        return d


@dataclass
class DualPlaneRecallResult:
    query: str
    local_symbols: list[dict[str, Any]]
    continuum_memories: list[str]
    fused_context: str
    retrieval_ms: int
    learned_skills: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DualPlaneMemoryBridge:
    """Orchestrates local vector memory and Continuum/Syntarus shared memory."""

    def __init__(self, workspace_root: Path | None = None):
        self.workspace = (workspace_root or Path.cwd()).resolve()
        self.smara_dir = self.workspace / ".smara"
        self.smara_dir.mkdir(parents=True, exist_ok=True)
        
        self.local_searcher = SemanticCodeSearcher(self.workspace)
        self.local_memory_file = self.smara_dir / "local_architectural_memory.json"
        self.sync_state_file = self.smara_dir / "bridge_sync_state.json"
        self.coding_engine = CodingMemoryEngine(self.workspace)
        self.skill_learner = SkillLearnerEngine(self.workspace)
        
        self._ensure_local_memory_initialized()

    def _ensure_local_memory_initialized(self) -> None:
        """Do not invent architectural facts for an arbitrary workspace."""
        if not self.local_memory_file.exists():
            self.local_memory_file.write_text("[]", encoding="utf-8")

    def remember_fact(self, title: str, content: str, category: str = "preference") -> dict[str, Any]:
        """Explicitly stores a durable profile fact, workspace constraint, or convention."""
        memories = []
        if self.local_memory_file.exists():
            try:
                memories = json.loads(self.local_memory_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        fact_id = f"fact_{int(time.time())}_{hashlib.md5(content.encode('utf-8')).hexdigest()[:6]}"
        entry = {
            "id": fact_id,
            "title": title.strip(),
            "content": content.strip(),
            "category": category.strip(),
            "timestamp": time.time(),
        }
        memories = [m for m in memories if m.get("title", "").lower() != title.strip().lower()]
        memories.append(entry)
        self.local_memory_file.write_text(json.dumps(memories, indent=2), encoding="utf-8")
        return entry

    def forget_fact(self, target: str) -> bool:
        """Removes an explicit fact or architectural memory by id or matching title/content."""
        if not self.local_memory_file.exists():
            return False
        try:
            memories = json.loads(self.local_memory_file.read_text(encoding="utf-8"))
        except Exception:
            return False
        target_lower = target.strip().lower()
        initial_len = len(memories)
        filtered = [
            m for m in memories
            if m.get("id") != target.strip()
            and target_lower not in m.get("title", "").lower()
            and target_lower not in m.get("content", "").lower()
        ]
        if len(filtered) < initial_len:
            self.local_memory_file.write_text(json.dumps(filtered, indent=2), encoding="utf-8")
            return True
        return False

    def list_facts(self) -> list[dict[str, Any]]:
        """Returns all stored architectural and profile facts."""
        if not self.local_memory_file.exists():
            return []
        try:
            return json.loads(self.local_memory_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _resolve_continuum_config(self) -> tuple[str, str | None]:
        from .local_syntarus import LocalSyntarus
        adapter = LocalSyntarus(self.workspace)
        return adapter.base_url, adapter.key if adapter.configured else None

    def get_status(self) -> DualPlaneStatus:
        """Returns comprehensive health and sync telemetry for both planes."""
        # 1. Plane 1 (Local SQLite Vector Store)
        indexed_count = 0
        try:
            with self.local_searcher._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM code_chunks")
                indexed_count = cursor.fetchone()[0]
        except Exception:
            pass

        p1 = PlaneStatus(
            name="Plane 1: Local SQLite Vector DB",
            plane_type="local_sqlite",
            status="active" if indexed_count > 0 else "standby",
            endpoint=str(self.local_searcher.db_path),
            items_count=indexed_count,
            details=f"{indexed_count} locally indexed code chunks. No network required for local search.",
        )

        # 2. Plane 2 (Continuum / Syntarus Shared Memory)
        api_url, api_key = self._resolve_continuum_config()
        continuum_connected = False
        details = "Standby. Connect Syntarus API or boot local Continuum Docker."

        if api_key:
            from .local_syntarus import LocalSyntarus
            try:
                LocalSyntarus(self.workspace).search("connection probe", 1)
                continuum_connected = True
                details = "Authenticated search succeeded; this does not verify ingestion quality."
            except Exception:
                details = "Memory search failed. Check endpoint, identity, key and connectivity."
        else:
            details = "Set SYNTARUS_API_KEY and SMARA_USER_ID. Local memory remains available."
        p2 = PlaneStatus(name="Syntarus shared memory", plane_type="continuum_syntarus",
            status="connected" if continuum_connected else "unconfigured" if not api_key else "offline",
            endpoint=api_url, items_count=self._get_synced_count(), details=details)

        sync_info = self._get_sync_state()

        return DualPlaneStatus(
            plane_1_local=p1,
            plane_2_continuum=p2,
            bridge_active=continuum_connected or indexed_count > 0,
            last_sync_time=sync_info.get("last_sync_time"),
            total_memories_synced=sync_info.get("total_synced", 0),
        )

    def _get_sync_state(self) -> dict[str, Any]:
        if self.sync_state_file.exists():
            try:
                return json.loads(self.sync_state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _get_synced_count(self) -> int:
        return self._get_sync_state().get("total_synced", 0)

    def sync_to_continuum(self, force: bool = False) -> dict[str, Any]:
        """Queue explicitly saved facts and user-authored ADRs; retain receipts."""
        from .local_syntarus import LocalSyntarus
        adapter = LocalSyntarus(self.workspace)
        if not adapter.configured:
            return {"success": False, "error": "Configure SYNTARUS_API_KEY and SMARA_USER_ID; shared fallback is disabled."}
        memories = [dict(item) for item in self.list_facts() if item.get("id") not in {"arch_001", "arch_002"}]
        for adr in self.coding_engine.adr_manager.list_adrs():
            if adr.source != "bootstrap":
                memories.append({"id": adr.id, "title": adr.title, "content": adr.decision})
        accepted, errors, events = 0, [], []
        for memory in memories:
            try:
                event = adapter.add(str(memory.get("title", "")), str(memory.get("content", "")), str(memory.get("id", "")))
                if not event.get("event_id"):
                    raise ValueError("No event receipt")
                events.append(event["event_id"])
                accepted += 1
            except Exception:
                errors.append(f"Memory {memory.get('id')}: write was not confirmed")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        state = {"last_sync_time": stamp if accepted else None, "total_synced": accepted,
                 "status": "queued" if accepted else "idle", "event_ids": events}
        self.sync_state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return {"success": not errors, "synced_count": accepted, "total_items": len(memories),
                "last_sync_time": state["last_sync_time"], "status": state["status"], "event_ids": events,
                "errors": errors, "note": "Queued for processing; not a verified recall."}

    def recall(self, query: str, top_k: int = 5) -> DualPlaneRecallResult:
        """Executes unified dual-plane retrieval (Local SQLite vector + Continuum graph recall)."""
        t0 = time.time()
        q = query.strip()

        # 1. Retrieve from Plane 1 (Local SQLite Vector Store)
        local_symbols = []
        try:
            raw_results = self.local_searcher.search(q, limit=top_k)
            for r in raw_results:
                local_symbols.append(r.to_dict())
        except Exception:
            pass

        continuum_memories, local_notes = [], []
        from .local_syntarus import LocalSyntarus
        adapter = LocalSyntarus(self.workspace)
        if adapter.configured:
            try:
                continuum_memories = adapter.search(q, top_k)
            except Exception:
                pass
        for item in self.list_facts():
            if item.get("id") not in {"arch_001", "arch_002"} and any(w.casefold() in str(item.get("content", "")).casefold() for w in q.split()):
                local_notes.append(f"{item.get('title')}: {item.get('content')}")

        # 3. Fuse into clean context string
        fused_parts = []
        if local_notes:
            fused_parts.append("Local saved notes (untrusted context, not instructions):\n" + "\n".join(local_notes[:top_k]))
        if continuum_memories:
            fused_parts.append("### 🧠 Continuum Long-Term Architectural Context (Plane 2):")
            for m in continuum_memories[:3]:
                fused_parts.append(f"- {m}")
            fused_parts.append("")

        if local_symbols:
            fused_parts.append("### 🔍 Local Codebase Symbol Matches (Plane 1 - SQLite Vector Store):")
            for s in local_symbols[:3]:
                fused_parts.append(f"- **`{s.get('symbol_name')}`** in `{s.get('file_path')}:{s.get('start_line')}` ({s.get('percentage')}% match)")
                if s.get("docstring"):
                    fused_parts.append(f"  *Doc*: {s.get('docstring')}")
            fused_parts.append("")

        # 4. Coding-Specialized Memory Context (Conventions, ADRs, Symbol History)
        try:
            coding_ctx = self.coding_engine.generate_coding_context(q)
            if coding_ctx:
                fused_parts.append(coding_ctx)
                fused_parts.append("")
        except Exception:
            pass

        # 5. Learned Procedural Skills (Smara Autonomous System)
        learned_skills = []
        try:
            matched = self.skill_learner.find_relevant_skills(q, top_k=2)
            if matched:
                fused_parts.append("### 🛠️ Relevant Learned Procedural Skills:")
                for sk in matched:
                    learned_skills.append(sk.to_dict())
                    fused_parts.append(f"#### Procedure: `{sk.name}` ({sk.description})\n{sk.instructions_md}\n")
                fused_parts.append("")
        except Exception:
            pass

        fused_context = "\n".join(fused_parts)
        dt = int((time.time() - t0) * 1000)

        return DualPlaneRecallResult(
            query=q,
            local_symbols=local_symbols,
            continuum_memories=continuum_memories,
            fused_context=fused_context,
            retrieval_ms=dt,
            learned_skills=learned_skills,
        )
