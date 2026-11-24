"""The two small things every Smara MCP tool server shares, kept apart from native_tools so importing them stays fast."""
from __future__ import annotations

MAX_BYTES = 1_000_000


def tool(name, description, properties=None, required=None):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": name in {"web_search", "fetch_url", "academic_search"}}}
