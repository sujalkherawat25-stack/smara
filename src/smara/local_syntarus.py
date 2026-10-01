"""Explicit, workspace-scoped Syntarus SDK access for local coding clients."""
from __future__ import annotations

import hashlib
import os
import json
from pathlib import Path


class LocalSyntarus:
    def __init__(self, workspace: Path):
        self.base_url = (os.getenv("SYNTARUS_BASE_URL") or "https://ai.syntarus.com/syntarus-api/v1").rstrip("/")
        self.key = os.getenv("SYNTARUS_API_KEY")
        self.user_id = os.getenv("SMARA_USER_ID")
        if not self.user_id:
            try:
                config = json.loads((workspace / ".smara" / "syntarus.json").read_text(encoding="utf-8"))
                self.user_id = config.get("user_id") if isinstance(config, dict) else None
            except (OSError, ValueError):
                pass
        self.agent_id = "smara-workspace-" + hashlib.sha256(str(workspace.resolve()).casefold().encode()).hexdigest()[:24]

    @property
    def configured(self) -> bool:
        return bool(self.key and isinstance(self.user_id, str) and self.user_id.strip() and self.key != "sk_mem_community")

    def client(self):
        if not self.configured:
            raise ValueError("Set SYNTARUS_API_KEY and a stable SMARA_USER_ID; shared fallback credentials are disabled")
        from syntarus import MemoryClient
        return MemoryClient(self.key, base_url=self.base_url, timeout=3.0)

    def search(self, query: str, limit: int = 5) -> list[str]:
        with self.client() as client:
            value = client.search(query[:4000], user_id=self.user_id, agent_id=self.agent_id, top_k=max(1, min(limit, 8)))
        context = value.get("context") or value.get("context_string")
        if context:
            return [str(context)[:8000]]
        rows = value.get("results") or value.get("memories") or []
        return [str(item.get("memory") or item.get("content") or item.get("text") or "")[:2000]
                for item in rows[:limit] if isinstance(item, dict)]

    def add(self, title: str, content: str, memory_id: str) -> dict:
        digest = hashlib.sha256((self.agent_id + title + content).encode()).hexdigest()
        with self.client() as client:
            event = client.add(user_id=self.user_id, agent_id=self.agent_id,
                messages=[{"role": "user", "content": title[:2000]}, {"role": "assistant", "content": content[:12000]}],
                metadata={"source": "smara_explicit_memory", "workspace_id": self.agent_id, "memory_id": memory_id},
                idempotency_key="smara-memory-" + digest)
        # The provider queues processing; acceptance is NOT a verified recall.
        return event


def coding_context_for_turn(workspace: Path, query: str) -> str:
    """Local-only bounded recall; never upload coding prompts implicitly."""
    parts = []
    path = workspace / ".smara" / "local_architectural_memory.json"
    try:
        if path.stat().st_size <= 256_000:
            notes = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(notes, list):
                for note in notes[:200]:
                    if not isinstance(note, dict) or note.get("id") in {"arch_001", "arch_002"}:
                        continue
                    text = str(note.get("content", ""))
                    if any(word.casefold() in text.casefold() for word in query.split()):
                        parts.append("Local saved note: " + text[:2000])
                        if len(parts) >= 3:
                            break
    except (OSError, ValueError):
        pass
    return "\n".join(parts)[:8000]
