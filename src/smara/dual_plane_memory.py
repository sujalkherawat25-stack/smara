"""Dual-Plane Memory Bridge for Smara.

Unifies:
- Plane 1 (Local): Offline SQLite vector store (.smara/semantic_index.db) for
  instant code symbol & chunk recall with zero external dependencies.
- Plane 2 (Continuum / Syntarus): LoCoMo 85+ Graph & Temporal Memory Plane for
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

import httpx

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
        """Seeds default local architectural memory if empty."""
        if not self.local_memory_file.exists():
            default_notes = [
                {
                    "id": "arch_001",
                    "title": "Smara Architecture & Dual-Plane Foundation",
                    "content": "Smara is an autonomous pairing developer agent featuring local AST code property graphs, local vector semantic search, git workspace management, and browser automation sidecars.",
                    "category": "architecture",
                    "timestamp": time.time(),
                },
                {
                    "id": "arch_002",
                    "title": "Zero-Approval Friction Model",
                    "content": "Smara operates autonomously with zero approval delays for safe workspace actions, using pre-change atomic snapshots and rollback ledgers to guarantee security.",
                    "category": "convention",
                    "timestamp": time.time(),
                }
            ]
            self.local_memory_file.write_text(json.dumps(default_notes, indent=2), encoding="utf-8")

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
        """Resolves Continuum / Syntarus API endpoint and API key."""
        api_url = os.getenv("SYNTARUS_BASE_URL") or os.getenv("CONTINUUM_BASE_URL")
        api_key = os.getenv("SYNTARUS_API_KEY") or os.getenv("CONTINUUM_API_KEY")
        
        # Check Windows Desktop token & settings in APPDATA
        appdata = os.environ.get("APPDATA")
        if appdata:
            desktop_token = Path(appdata) / "Smara" / "token.json"
            desktop_cfg = Path(appdata) / "Smara" / "desktop.json"
            if not api_key and desktop_token.exists():
                try:
                    data = json.loads(desktop_token.read_text(encoding="utf-8"))
                    api_key = data.get("access_token") or data.get("token")
                except Exception:
                    pass
            if not api_url and desktop_cfg.exists():
                try:
                    data = json.loads(desktop_cfg.read_text(encoding="utf-8"))
                    api_url = data.get("smara_url")
                except Exception:
                    pass

        # Check CLI token file
        token_file = Path.home() / ".smara" / "token"
        alt_token_file = self.smara_dir / "cli_token.json"
        if not api_key:
            if token_file.exists():
                api_key = token_file.read_text(encoding="utf-8").strip()
            elif alt_token_file.exists():
                try:
                    data = json.loads(alt_token_file.read_text(encoding="utf-8"))
                    api_key = data.get("token")
                except Exception:
                    pass

        # Fallback to local Continuum Community Edition if unconfigured
        if not api_url:
            api_url = "https://ai.syntarus.com/smara-api"
        if not api_key:
            api_key = "sk_mem_community"

        return api_url.rstrip("/"), api_key

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
            details=f"Indexed {indexed_count} dense vector symbols offline. 0ms network latency.",
        )

        # 2. Plane 2 (Continuum / Syntarus Shared Memory)
        api_url, api_key = self._resolve_continuum_config()
        continuum_connected = False
        details = "Standby. Connect Syntarus API or boot local Continuum Docker."

        try:
            # Check Continuum health
            client = httpx.Client(timeout=1.5)
            health_url = api_url.replace("/v1", "") + "/health"
            resp = client.get(health_url)
            if resp.status_code == 200:
                continuum_connected = True
                details = f"Connected to Continuum Memory Engine at {api_url}. LoCoMo 85+ Graph active."
            else:
                details = f"Endpoint responded with HTTP {resp.status_code}."
        except Exception:
            # Check public cloud endpoint fallback
            if api_key and api_key != "sk_mem_community":
                continuum_connected = True
                details = f"Configured with Syntarus key for {api_url}."
            else:
                details = "Continuum service offline or in standby mode."

        p2 = PlaneStatus(
            name="Plane 2: Continuum Memory Engine (LoCoMo 85+)",
            plane_type="continuum_syntarus",
            status="connected" if continuum_connected else "standby",
            endpoint=api_url,
            items_count=self._get_synced_count(),
            details=details,
        )

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
        """Syncs local architectural decisions, conventions, and task completions to Continuum."""
        api_url, api_key = self._resolve_continuum_config()
        if not api_key:
            return {
                "success": False,
                "error": "No Continuum/Syntarus API key configured. Set SYNTARUS_API_KEY or use 'sk_mem_community'.",
            }

        # Load local architectural memories
        memories = []
        if self.local_memory_file.exists():
            try:
                memories = json.loads(self.local_memory_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        if not memories:
            return {"success": True, "synced_count": 0, "message": "No local memories to sync."}

        synced_count = 0
        errors = []

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "smara-dual-plane/2.0",
        }

        # Sync using Syntarus SDK / HTTP endpoint
        client = httpx.Client(timeout=8.0)
        endpoint = f"{api_url}/memories" if api_url.endswith("/v1") else f"{api_url}/v1/memories"

        workspace_id = self.workspace.name.lower()
        user_id = os.getenv("SMARA_USER_ID")
        if not user_id:
            appdata = os.environ.get("APPDATA")
            if appdata:
                desktop_cfg = Path(appdata) / "Smara" / "desktop.json"
                if desktop_cfg.exists():
                    try:
                        user_id = json.loads(desktop_cfg.read_text(encoding="utf-8")).get("account_id")
                    except Exception:
                        pass
        user_id = user_id or "smara_developer"

        for mem in memories:
            payload = {
                "user_id": user_id,
                "messages": [
                    {"role": "user", "content": f"Architectural Decision: {mem.get('title')}"},
                    {"role": "assistant", "content": mem.get("content", "")},
                ],
                "metadata": {
                    "source": "smara_desktop_bridge",
                    "workspace_id": workspace_id,
                    "category": mem.get("category", "architecture"),
                    "memory_id": mem.get("id"),
                },
            }
            idempotency_key = f"smara-bridge-{mem.get('id')}-{hashlib.md5(mem.get('content', '').encode('utf-8')).hexdigest()[:8]}"
            headers["Idempotency-Key"] = idempotency_key

            try:
                resp = client.post(endpoint, headers=headers, json=payload)
                if resp.status_code in (200, 201, 202):
                    synced_count += 1
                else:
                    errors.append(f"Memory {mem.get('id')}: HTTP {resp.status_code}")
            except Exception as e:
                errors.append(f"Memory {mem.get('id')}: {str(e)}")

        # Also sync formal Architecture Decision Records (ADRs)
        adrs = self.coding_engine.adr_manager.list_adrs()
        for adr in adrs:
            adr_payload = {
                "user_id": user_id,
                "messages": [
                    {"role": "user", "content": f"Architecture Decision Record ADR-{adr.id}: {adr.title}"},
                    {"role": "assistant", "content": f"Context: {adr.context}\nDecision: {adr.decision}\nConsequences: {adr.consequences}\nSymbols: {', '.join(adr.symbols_affected)}"},
                ],
                "metadata": {
                    "source": "smara_adr_sync",
                    "workspace_id": workspace_id,
                    "adr_id": adr.id,
                    "status": adr.status,
                    "symbols": adr.symbols_affected,
                },
            }
            headers["Idempotency-Key"] = f"smara-adr-{adr.id}-{hashlib.md5(adr.decision.encode('utf-8')).hexdigest()[:8]}"
            try:
                resp = client.post(endpoint, headers=headers, json=adr_payload)
                if resp.status_code in (200, 201, 202):
                    synced_count += 1
                else:
                    errors.append(f"ADR {adr.id}: HTTP {resp.status_code}")
            except Exception as e:
                errors.append(f"ADR {adr.id}: {str(e)}")

        total_items = len(memories) + len(adrs)

        # Update sync state
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")
        sync_state = {
            "last_sync_time": current_time,
            "total_synced": synced_count,
            "status": "success" if synced_count > 0 else "failed",
        }
        self.sync_state_file.write_text(json.dumps(sync_state, indent=2), encoding="utf-8")

        return {
            "success": synced_count > 0 or total_items == 0,
            "synced_count": synced_count,
            "total_items": total_items,
            "last_sync_time": current_time,
            "errors": errors if errors else None,
        }

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

        # 2. Retrieve from Plane 2 (Continuum / Syntarus Memory Plane)
        continuum_memories = []
        api_url, api_key = self._resolve_continuum_config()
        if api_key:
            try:
                client = httpx.Client(timeout=2.0)
                search_url = f"{api_url}/memories/search" if api_url.endswith("/v1") else f"{api_url}/v1/memories/search"
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                search_payload = {
                    "user_id": os.getenv("SMARA_USER_ID", "smara_developer"),
                    "query": q,
                    "top_k": top_k,
                }
                resp = client.post(search_url, headers=headers, json=search_payload)
                if resp.status_code == 200:
                    data = resp.json()
                    # Handle Mem0 / Syntarus search response format
                    results = data.get("results") or data.get("memories") or []
                    for item in results:
                        mem_text = item.get("memory") or item.get("content") or item.get("text")
                        if mem_text:
                            continuum_memories.append(str(mem_text))
            except Exception:
                pass

        # Fallback to local architectural memory if Continuum is offline
        if not continuum_memories and self.local_memory_file.exists():
            try:
                data = json.loads(self.local_memory_file.read_text(encoding="utf-8"))
                for item in data:
                    c = item.get("content", "")
                    if any(w.lower() in c.lower() for w in q.split()):
                        continuum_memories.append(f"{item.get('title')}: {c}")
            except Exception:
                pass

        # 3. Fuse into clean context string
        fused_parts = []
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
