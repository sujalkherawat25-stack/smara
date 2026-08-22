"""Provider-neutral, approval-aware tools for the Smara runtime.

The first registry contains only bounded read-only tools.  Integrations and
local executors must not be added here casually: side-effecting tools need a
durable task, an approval policy, and an audit record before registration.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import math
import operator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .research_tools import FetchUrlTool as ResearchFetchUrlTool
from .research_tools import ResearchToolError, WebSearchTool as ResearchWebSearchTool

MAX_TOOL_RESULT_CHARS = 4_000


class ToolError(RuntimeError):
    """A safe, user-actionable tool failure."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    side_effecting: bool = False
    requires_approval: bool = False


@dataclass(frozen=True)
class ToolContext:
    account_id: str
    workspace_id: str
    http_client: httpx.AsyncClient | None = None


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    citations: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    spec: ToolSpec

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult: ...


def _bounded(value: Any, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "\n[…truncated by Smara]"


class CurrentTimeTool:
    spec = ToolSpec(
        "current_time",
        "Return the current UTC time.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(True, dt.datetime.now(dt.timezone.utc).isoformat())


_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_number(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        if not math.isfinite(float(node.value)) or abs(float(node.value)) > 1_000_000_000_000:
            raise ToolError("Calculator values must be finite and bounded.")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_safe_number(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left, right = _safe_number(node.left), _safe_number(node.right)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > 12:
            raise ToolError("Calculator exponent is too large.")
        try:
            result = _BINARY_OPS[type(node.op)](left, right)
        except (ArithmeticError, OverflowError) as exc:
            raise ToolError("Calculator expression is not valid.") from exc
        if not isinstance(result, (int, float)) or not math.isfinite(float(result)) or abs(float(result)) > 1_000_000_000_000:
            raise ToolError("Calculator result is outside the safe limit.")
        return result
    raise ToolError("Only bounded numeric arithmetic is allowed.")


class CalculateTool:
    spec = ToolSpec(
        "calculate",
        "Evaluate a small numeric expression without executing code.",
        {
            "type": "object",
            "properties": {"expression": {"type": "string", "maxLength": 200}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        expression = arguments.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ToolError("Calculator needs a numeric expression.")
        try:
            tree = ast.parse(expression.strip()[:200], mode="eval")
            if sum(1 for _ in ast.walk(tree)) > 50:
                raise ToolError("Calculator expression is too complex.")
            result = _safe_number(tree.body)
        except (SyntaxError, TypeError, ValueError) as exc:
            raise ToolError("Calculator expression is not valid.") from exc
        return ToolResult(True, str(result))


class ResearchSearchTool:
    spec = ToolSpec(
        "research.web_search",
        "Search configured public research sources and return bounded source hits.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 500},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 5},
                "include_domains": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._tool = ResearchWebSearchTool(http_client)

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("Research search needs a non-empty query.")
        try:
            hits = await self._tool.search(query, max_results=arguments.get("max_results", 5), include_domains=arguments.get("include_domains"))
        except ResearchToolError as exc:
            raise ToolError(str(exc)) from exc
        data = [{"title": hit.title, "url": hit.url, "snippet": hit.snippet, "provider": hit.provider} for hit in hits]
        return ToolResult(True, _bounded(json.dumps(data, ensure_ascii=False)), citations=[hit.url for hit in hits])


class ResearchFetchTool:
    spec = ToolSpec(
        "research.fetch_url",
        "Fetch one public URL with SSRF, redirect, type, size, and excerpt limits.",
        {
            "type": "object",
            "properties": {"url": {"type": "string", "maxLength": 2_000}},
            "required": ["url"],
            "additionalProperties": False,
        },
    )

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._tool = ResearchFetchUrlTool(http_client)

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        url = arguments.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ToolError("URL retrieval needs a public HTTP(S) URL.")
        try:
            source = await self._tool.fetch(url.strip()[:2_000])
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolError(str(exc)) from exc
        data = {"title": source.title, "excerpt": source.excerpt, "retrieved_at": source.retrieved_at, "content_sha256": source.content_sha256}
        return ToolResult(True, _bounded(json.dumps(data, ensure_ascii=False)), citations=[url.strip()])


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        name = tool.spec.name.strip()
        if not name or name in self._tools:
            raise ValueError("Tool names must be unique and non-empty.")
        self._tools[name] = tool

    def describe(self) -> list[dict[str, Any]]:
        return [
            {"name": tool.spec.name, "description": tool.spec.description, "parameters": tool.spec.parameters, "side_effecting": tool.spec.side_effecting, "requires_approval": tool.spec.requires_approval}
            for tool in sorted(self._tools.values(), key=lambda item: item.spec.name)
        ]

    async def invoke(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError("Requested tool is not registered.")
        if tool.spec.side_effecting or tool.spec.requires_approval:
            raise ToolError("This tool requires a durable approved task and cannot run directly.")
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be an object.")
        properties = tool.spec.parameters.get("properties", {})
        unknown = set(arguments) - set(properties)
        if unknown or tool.spec.parameters.get("additionalProperties") is False:
            if unknown:
                raise ToolError("Tool arguments contain unsupported fields.")
        result = await tool.run(arguments, context)
        return ToolResult(result.ok, _bounded(result.content), list(result.citations)[:20], dict(result.meta))


def default_tool_registry(http_client: httpx.AsyncClient | None = None) -> ToolRegistry:
    return ToolRegistry([
        CurrentTimeTool(),
        CalculateTool(),
        ResearchSearchTool(http_client),
        ResearchFetchTool(http_client),
    ])
