"""Token-budgeted, protocol-safe context packing for all local agent loops."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class ModelContextProfile:
    tokenizer_id: str
    input_capacity: int
    output_reserve: int
    safety_margin: int = 256
    protocol_overhead: int = 32


@dataclass(frozen=True)
class PackedContext:
    messages: tuple[dict[str, Any], ...]
    input_tokens: int
    accounting_quality: str
    omitted_messages: int


class ContextOverflow(ValueError):
    pass


def conservative_tokens(value: Any) -> int:
    """UTF-8 byte upper bound: safe for unknown tokenizers, explicitly coarse."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _groups(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            group = [message]; ids = {item.get("id") for item in message.get("tool_calls", [])}
            index += 1
            while index < len(messages) and messages[index].get("role") == "tool" and messages[index].get("tool_call_id") in ids:
                group.append(messages[index]); index += 1
            groups.append(group); continue
        groups.append([message]); index += 1
    return groups


def pack_messages(messages: Iterable[dict[str, Any]], profile: ModelContextProfile, *, tools: Any=(), attachments: Any=(), tokenizer: Callable[[str], int] | None=None) -> PackedContext:
    source = [dict(item) for item in messages]
    count = (lambda value: tokenizer(json.dumps(value, ensure_ascii=False, separators=(",", ":")))) if tokenizer else conservative_tokens
    quality = "exact" if tokenizer else "conservative_utf8_upper_bound"
    available = profile.input_capacity - profile.output_reserve - profile.safety_margin
    overhead = count({"tools": tools, "attachments": attachments}) + profile.protocol_overhead
    if available <= overhead: raise ContextOverflow("tool schemas and protocol overhead exceed model input capacity")
    groups = _groups(source)
    mandatory_indices = {0, len(groups)-1} if groups else set()
    for index, group in enumerate(groups):
        if any(item.get("_smara_mandatory") for item in group): mandatory_indices.add(index)
    selected_indices = set(mandatory_indices)
    if overhead + count([groups[index] for index in sorted(selected_indices)]) > available: raise ContextOverflow("mandatory task state does not fit the selected model context")
    remaining = [index for index in range(len(groups)) if index not in mandatory_indices]
    for index in reversed(remaining):
        candidate = [groups[i] for i in sorted(selected_indices | {index})]
        if overhead + count(candidate) <= available: selected_indices.add(index)
    packed = tuple(item for index in sorted(selected_indices) for item in groups[index])
    tokens = overhead + count(list(packed))
    if tokens > available: raise AssertionError("packer emitted an oversized request")
    return PackedContext(packed, tokens, quality, len(source)-len(packed))
