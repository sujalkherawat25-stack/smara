"""
Local Task Memory Engine for Smara
Provides durable, file-backed curated memory persisting across sessions:
- MEMORY.md: Project observations, environment facts, discovered conventions, tool quirks
- USER.md: User preferences, communication style, workflow habits

Features:
- Frozen Snapshot Pattern: System prompt receives stable snapshot at session start (preserves prefix caching)
- Durable Immediate Writes: Updates write to disk atomically
- Unique Substring Matching: 'replace' and 'remove' locate targets without fragile line numbers
- Section Delimiter: Items separated by '§'
- Threat / Injection Filter: Sanitizes inputs before saving to prevent prompt injection
"""

from __future__ import annotations
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("smara.task_memory")

ENTRY_DELIMITER = "\n§\n"
DEFAULT_MAX_CHARS = 12000

# Basic threat / prompt-injection patterns
SUSPICIOUS_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*prompt\s*override", re.IGNORECASE),
    re.compile(r"<script.*?>.*?</script>", re.IGNORECASE | re.DOTALL),
    re.compile(r"(export|set)\s+[A-Z_]+_API_KEY\s*=", re.IGNORECASE),
]


def sanitize_memory_content(content: str) -> Optional[str]:
    """Scan memory entry for dangerous injection patterns. Returns error string if blocked."""
    for pattern in SUSPICIOUS_PATTERNS:
        if pattern.search(content):
            return "Memory write blocked: Content matches suspicious prompt injection or credential pattern."
    return None


class TaskMemoryStore:
    """Manages file-backed local memory (MEMORY.md and USER.md)."""

    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            home = Path.home() / ".smara" / "memory"
            self.storage_dir = home
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.storage_dir / "MEMORY.md"
        self.user_file = self.storage_dir / "USER.md"
        self._ensure_files()

    def _ensure_files(self) -> None:
        if not self.memory_file.exists():
            self.memory_file.write_text("# Project & Environment Notes\n", encoding="utf-8")
        if not self.user_file.exists():
            self.user_file.write_text("# User Profile & Preferences\n", encoding="utf-8")

    def _get_target_file(self, target: str) -> Path:
        t = target.lower().strip()
        if t in ("user", "user.md", "profile"):
            return self.user_file
        return self.memory_file

    def read_entries(self, target: str = "memory") -> List[str]:
        """Read list of discrete memory entries separated by delimiter."""
        file_path = self._get_target_file(target)
        if not file_path.exists():
            return []
        raw = file_path.read_text(encoding="utf-8")
        parts = [p.strip() for p in raw.split("§") if p.strip()]
        # Filter out markdown title headers if present as first element
        entries = []
        for p in parts:
            if p.startswith("# ") and "\n" not in p:
                continue
            entries.append(p)
        return entries

    def add_entry(self, content: str, target: str = "memory") -> Dict[str, Any]:
        """Add a new memory entry."""
        content = content.strip()
        if not content:
            return {"status": "error", "message": "Memory content cannot be empty."}

        threat_err = sanitize_memory_content(content)
        if threat_err:
            return {"status": "error", "message": threat_err}

        entries = self.read_entries(target)
        # Avoid exact duplicate
        if content in entries:
            return {"status": "noop", "message": "Identical entry already exists in memory."}

        entries.append(content)
        self._save_entries(entries, target)
        return {"status": "success", "message": f"Added entry to {target}.", "entry_count": len(entries)}

    def replace_entry(self, old_substring: str, new_content: str, target: str = "memory") -> Dict[str, Any]:
        """Replace an entry identified by a unique substring match."""
        old_substring = old_substring.strip()
        new_content = new_content.strip()
        if not old_substring or not new_content:
            return {"status": "error", "message": "Both old_substring and new_content are required."}

        threat_err = sanitize_memory_content(new_content)
        if threat_err:
            return {"status": "error", "message": threat_err}

        entries = self.read_entries(target)
        matches = [i for i, entry in enumerate(entries) if old_substring.lower() in entry.lower()]

        if len(matches) == 0:
            return {"status": "error", "message": f"No entry matching '{old_substring}' was found in {target}."}
        if len(matches) > 1:
            return {
                "status": "error",
                "message": f"Ambiguous match: Found {len(matches)} entries matching '{old_substring}'. Provide a more specific substring."
            }

        idx = matches[0]
        entries[idx] = new_content
        self._save_entries(entries, target)
        return {"status": "success", "message": f"Replaced entry in {target}.", "updated_entry": new_content}

    def remove_entry(self, substring: str, target: str = "memory") -> Dict[str, Any]:
        """Remove an entry identified by a unique substring match."""
        substring = substring.strip()
        if not substring:
            return {"status": "error", "message": "Substring is required to identify target entry."}

        entries = self.read_entries(target)
        matches = [i for i, entry in enumerate(entries) if substring.lower() in entry.lower()]

        if len(matches) == 0:
            return {"status": "error", "message": f"No entry matching '{substring}' was found in {target}."}
        if len(matches) > 1:
            return {
                "status": "error",
                "message": f"Ambiguous match: Found {len(matches)} entries matching '{substring}'. Provide a more specific substring."
            }

        removed = entries.pop(matches[0])
        self._save_entries(entries, target)
        return {"status": "success", "message": f"Removed entry from {target}.", "removed_entry": removed}

    def search_entries(self, query: str, target: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search entries across memory stores by keyword or query."""
        targets = [target] if target else ["memory", "user"]
        results = []
        tokens = [t.lower() for t in query.split() if len(t) > 1]

        for t in targets:
            entries = self.read_entries(t)
            for entry in entries:
                score = sum(1 for tok in tokens if tok in entry.lower())
                if score > 0:
                    results.append({
                        "store": t,
                        "content": entry,
                        "relevance": score
                    })

        results.sort(key=lambda x: x["relevance"], reverse=True)
        return results

    def _save_entries(self, entries: List[str], target: str) -> None:
        file_path = self._get_target_file(target)
        header = "# Project & Environment Notes" if "memory" in file_path.name.lower() else "# User Profile & Preferences"
        body = ENTRY_DELIMITER.join(entries)
        full_text = f"{header}\n\n§\n{body}\n" if body else f"{header}\n"
        temp_file = file_path.with_suffix(".tmp")
        temp_file.write_text(full_text, encoding="utf-8")
        temp_file.replace(file_path)

    def render_frozen_snapshot(self, max_chars: int = DEFAULT_MAX_CHARS) -> str:
        """
        Produce a compact, structured markdown block of memory to inject
        into the system prompt at session start. Preserves prefix caching.
        """
        mem_entries = self.read_entries("memory")
        user_entries = self.read_entries("user")

        sections = []
        if user_entries:
            sections.append("### User Preferences & Context:")
            for e in user_entries:
                sections.append(f"- {e.strip()}")

        if mem_entries:
            sections.append("### Curated Project & Environment Notes:")
            for e in mem_entries:
                sections.append(f"- {e.strip()}")

        combined = "\n".join(sections)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + f"\n... [Truncated {len(combined) - max_chars} characters]"
        return combined


# Global default instance
_default_store: Optional[TaskMemoryStore] = None

def get_default_memory_store() -> TaskMemoryStore:
    global _default_store
    if _default_store is None:
        _default_store = TaskMemoryStore()
    return _default_store
