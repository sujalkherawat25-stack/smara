"""Smara Task Planner and Progress Tracking Module.

Provides an in-memory, revision-tracked task checklist that Smara uses to decompose
complex tasks, track progress across multi-turn trajectories, and maintain focus across
long conversations.

Active tasks ([ ] pending, [>] in_progress) are automatically preserved across context
compaction events so the agent never loses state or forgets pending subtasks.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

VALID_STATUSES = {"pending", "in_progress", "completed", "cancelled"}

MAX_TODO_CONTENT_CHARS = 4000
MAX_TODO_ITEMS = 256
_TRUNCATION_MARKER = "… [truncated]"

TODO_INJECTION_HEADER = (
    "[Active Task List Preserved Across Compaction]"
)


class SmaraTaskPlanner:
    """In-memory task planner. One instance per Smara agent session.

    Items are ordered (list position represents priority).
    Each item contains:
      - id: unique string identifier (e.g. '1', 'setup', 'test_auth')
      - content: concise description of the task
      - status: 'pending' | 'in_progress' | 'completed' | 'cancelled'
      - parent: optional parent id for hierarchical subtasks
    """

    def __init__(self) -> None:
        self._items: List[Dict[str, str]] = []
        self._revision = 0

    def write(self, todos: List[Dict[str, Any]], merge: bool = False) -> List[Dict[str, str]]:
        """Write todos into the store.

        Args:
            todos: list of dicts with keys {id, content, status, optional parent}.
            merge: if False (default), replaces entire checklist.
                   if True, updates existing items by id and appends new ones.

        Returns:
            The full current list of items after update.
        """
        before = self.read()
        if not merge:
            self._items = self._normalize_order(
                [self._validate(t) for t in self._dedupe_by_id(todos)]
            )
        else:
            existing = {item["id"]: item for item in self._items}
            for t in self._dedupe_by_id(todos):
                item_id = str(t.get("id", "")).strip()
                if not item_id:
                    continue

                if item_id in existing:
                    if "content" in t and t["content"]:
                        existing[item_id]["content"] = self._cap_content(str(t["content"]).strip())
                    if "status" in t and t["status"]:
                        status = str(t["status"]).strip().lower()
                        if status in VALID_STATUSES:
                            existing[item_id]["status"] = status
                    if "parent" in t:
                        parent = str(t["parent"] or "").strip()
                        if parent:
                            existing[item_id]["parent"] = parent
                        else:
                            existing[item_id].pop("parent", None)
                else:
                    validated = self._validate(t)
                    existing[validated["id"]] = validated
                    self._items.append(validated)

            seen = set()
            rebuilt = []
            for item in self._items:
                current = existing.get(item["id"], item)
                if current["id"] not in seen:
                    rebuilt.append(current)
                    seen.add(current["id"])
            self._items = self._normalize_order(rebuilt)

        if len(self._items) > MAX_TODO_ITEMS:
            self._items = self._items[:MAX_TODO_ITEMS]
        self._sanitize_parents(self._items)

        if self._items != before:
            self._revision += 1
        return self.read()

    def read(self) -> List[Dict[str, str]]:
        """Return a copy of the current checklist."""
        return [item.copy() for item in self._items]

    def has_items(self) -> bool:
        """Check if any items exist in the planner."""
        return bool(self._items)

    def summary(self) -> Dict[str, int]:
        """Return counts by status."""
        items = self._items
        return {
            "total": len(items),
            "pending": sum(1 for i in items if i["status"] == "pending"),
            "in_progress": sum(1 for i in items if i["status"] == "in_progress"),
            "completed": sum(1 for i in items if i["status"] == "completed"),
            "cancelled": sum(1 for i in items if i["status"] == "cancelled"),
        }

    def snapshot(self) -> Dict[str, Any]:
        """Return state snapshot with monotonic revision."""
        return {"todos": self.read(), "revision": self._revision, "summary": self.summary()}

    def restore(self, todos: List[Dict[str, Any]], revision: int = 0) -> List[Dict[str, str]]:
        """Restore checklist from snapshot."""
        self._items = self._normalize_order(
            [self._validate(t) for t in self._dedupe_by_id(todos)]
        )[:MAX_TODO_ITEMS]
        self._revision = max(0, int(revision or 0))
        return self.read()

    def format_for_injection(self) -> Optional[str]:
        """Render active checklist items for context compaction re-injection.

        Preserves pending and in-progress items, including active hierarchies,
        so the model resumes without repeating completed work.
        """
        if not self._items:
            return None

        markers = {
            "completed": "[x]",
            "in_progress": "[>]",
            "pending": "[ ]",
            "cancelled": "[~]",
        }

        active = {"pending", "in_progress"}
        children: Dict[str, List[Dict[str, str]]] = {}
        roots: List[Dict[str, str]] = []
        for item in self._items:
            parent = item.get("parent")
            if parent:
                children.setdefault(parent, []).append(item)
            else:
                roots.append(item)

        def render(item: Dict[str, str], depth: int, out: List[str]) -> bool:
            kid_lines: List[str] = []
            has_active_kid = False
            for kid in children.get(item["id"], []):
                has_active_kid |= render(kid, depth + 1, kid_lines)
            keep = item["status"] in active or has_active_kid
            if keep:
                marker = markers.get(item["status"], "[?]")
                out.append(
                    f"{'  ' * depth}- {marker} {item['id']}. "
                    f"{item['content']} ({item['status']})"
                )
                out.extend(kid_lines)
            return keep

        lines = [TODO_INJECTION_HEADER]
        for item in roots:
            render(item, 0, lines)

        if len(lines) == 1:
            return None

        return "\n".join(lines)

    @staticmethod
    def _cap_content(content: str) -> str:
        if len(content) > MAX_TODO_CONTENT_CHARS:
            keep = MAX_TODO_CONTENT_CHARS - len(_TRUNCATION_MARKER)
            return content[:keep] + _TRUNCATION_MARKER
        return content

    @staticmethod
    def _validate(item: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(item, dict):
            return {"id": "?", "content": "(invalid item)", "status": "pending"}

        item_id = str(item.get("id", "")).strip() or "?"
        content = str(item.get("content", "")).strip()
        if not content:
            content = "(no description)"
        else:
            content = SmaraTaskPlanner._cap_content(content)

        status = str(item.get("status", "pending")).strip().lower()
        if status not in VALID_STATUSES:
            status = "pending"

        result = {"id": item_id, "content": content, "status": status}
        parent = str(item.get("parent") or "").strip()
        if parent and parent != item_id:
            result["parent"] = parent
        return result

    @staticmethod
    def _sanitize_parents(items: List[Dict[str, str]]) -> None:
        ids = {item["id"] for item in items}
        by_id = {item["id"]: item for item in items}
        for item in items:
            parent = item.get("parent")
            if parent and parent not in ids:
                item.pop("parent", None)
        for item in items:
            seen = {item["id"]}
            node = item
            while node.get("parent"):
                if node["parent"] in seen:
                    item.pop("parent", None)
                    break
                seen.add(node["parent"])
                node = by_id[node["parent"]]

    @staticmethod
    def _dedupe_by_id(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        last_index: Dict[str, int] = {}
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                last_index[f"__invalid_{i}"] = i
                continue
            item_id = str(item.get("id", "")).strip() or "?"
            last_index[item_id] = i
        return [todos[i] for i in sorted(last_index.values())]

    @staticmethod
    def _normalize_order(items: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if any(item.get("parent") for item in items):
            return items
        active_index = next(
            (i for i, item in enumerate(items) if item["status"] == "in_progress"),
            None,
        )
        if active_index is None:
            return items

        pending_index = next(
            (
                i for i, item in enumerate(items[:active_index])
                if item["status"] == "pending"
            ),
            None,
        )
        if pending_index is None:
            return items

        normalized = items.copy()
        active_item = normalized.pop(active_index)
        normalized.insert(pending_index, active_item)
        return normalized