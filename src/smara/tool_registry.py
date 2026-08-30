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
from typing import Any, Awaitable, Callable, Protocol

import httpx

from .research import source_quality
from .research_tools import FetchUrlTool as ResearchFetchUrlTool
from .research_tools import ResearchToolError, WebSearchTool as ResearchWebSearchTool
from .workflow import validate_workflow, workflow_summary

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
    integration_runner: Callable[[str, str, dict[str, Any]], Awaitable[str]] | None = None
    integration_requester: Callable[[str, str, str, str, dict[str, Any]], dict[str, Any]] | None = None
    desktop_requester: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None
    desktop_workflow_requester: Callable[[str, list[dict[str, Any]]], dict[str, Any]] | None = None


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
        data = {
            "results": [
                {
                    "title": hit.title,
                    "url": hit.url,
                    "snippet": hit.snippet,
                    "provider": hit.provider,
                    "source_quality": hit.quality,
                    "quality_flags": list(hit.quality_flags),
                }
                for hit in hits
            ],
            "citation_policy": (
                "Search results are discovery leads. For factual claims, fetch the "
                "source URL and cite retrieved page content. Prefer primary sources; "
                "discovery_only sources require independent confirmation."
            ),
        }
        # Search URLs are leads, not verified evidence. Do not label them as
        # citations until research.fetch_url has retrieved the page.
        return ToolResult(True, _bounded(json.dumps(data, ensure_ascii=False)), citations=[])


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
        quality, flags = source_quality(url.strip(), source.title, source.excerpt)
        data = {
            "title": source.title,
            "excerpt": source.excerpt,
            "retrieved_at": source.retrieved_at,
            "published_at": source.published_at,
            "content_sha256": source.content_sha256,
            "source_quality": quality,
            "quality_flags": flags,
        }
        return ToolResult(True, _bounded(json.dumps(data, ensure_ascii=False)), citations=[url.strip()])


class IntegrationReadTool:
    """Read-only integration adapter exposed to bounded model tool selection.

    External writes never enter this registry.  They remain durable action
    intents in the integration ledger and require the existing approval UI.
    """

    def __init__(self, name: str, description: str, provider: str, action: str, properties: dict[str, Any] | None = None):
        self._provider = provider
        self._action = action
        self.spec = ToolSpec(
            name,
            description,
            {"type": "object", "properties": properties or {}, "additionalProperties": False},
            side_effecting=False,
            requires_approval=False,
        )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        if context.integration_runner is None:
            raise ToolError("This integration is not configured for the agent worker.")
        try:
            result = await context.integration_runner(self._provider, self._action, arguments)
        except Exception as exc:
            raise ToolError(f"Integration read failed: {str(exc)[:300]}") from exc
        return ToolResult(True, _bounded(result))


class IntegrationApprovalRequestTool:
    """Create an approval-gated intent without touching the external provider."""

    spec = ToolSpec(
        "integration.request_approval",
        "Create a durable external-action preview for the user to approve; never performs the action.",
        {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["gmail", "calendar", "telegram", "github", "drive"]},
                "action": {"type": "string", "maxLength": 120},
                "preview": {"type": "string", "minLength": 1, "maxLength": 2_000},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                "payload": {"type": "object", "maxProperties": 30},
            },
            "required": ["provider", "action", "preview", "idempotency_key"],
            "additionalProperties": False,
        },
    )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        if context.integration_requester is None:
            raise ToolError("Approval requests are available only inside a hosted task.")
        provider = arguments.get("provider")
        action = arguments.get("action")
        preview = arguments.get("preview")
        key = arguments.get("idempotency_key")
        if not all(isinstance(item, str) and item.strip() for item in (provider, action, preview, key)):
            raise ToolError("An integration approval request needs provider, action, preview, and idempotency_key.")
        if not isinstance(arguments.get("payload", {}), dict):
            raise ToolError("Integration approval payload must be an object.")
        try:
            result = context.integration_requester(provider, action, preview, key, arguments.get("payload", {}))
        except Exception as exc:
            raise ToolError(f"Approval request failed: {str(exc)[:300]}") from exc
        return ToolResult(True, _bounded(json.dumps({"approval_required": result.get("status") == "awaiting_approval", "status": result.get("status"), "action_id": result.get("id")}, ensure_ascii=False)))


class DesktopActionRequestTool:
    """Create an approved desktop step; never executes on the worker host."""

    spec = ToolSpec(
        "desktop.request_action",
        "Create a durable approval-gated task for a paired desktop capability. The local action is not run by this chat step. local_file_read supports read_file, list_tree, search_text, find_files, and git_summary. local_file_write supports preview_only planning plus bounded write, append, patch, rename, move, delete, and undo operations; every mutation returns a diff and local undo id.",
        {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "enum": ["local_file_read", "local_file_write", "local_terminal", "local_browser", "local_integration"]},
                "preview": {"type": "string", "minLength": 1, "maxLength": 1_000},
                "payload": {
                    "type": "object",
                    "maxProperties": 30,
                    "description": "Capability payload. local_file_read supports read_file, list_tree, literal search_text, find_files, and git_summary. local_file_write supports preview_only, write/append/patch/rename/move/delete, and undo with a returned undo_id. local_terminal uses argv and cwd. local_browser supports open, inspect_text, inspect_dom, or download with an approved HTTP(S) url; inspect_dom accepts a simple tag/#id/.class selector and bounded max_elements, while download requires a destination inside an approved folder and is capped at 50 MB. local_integration supports only approval-gated local Tavily search ({provider:tavily, operation:search, query}) and GitHub repository listing ({provider:github, operation:list_repositories, limit}); their credentials stay in the desktop vault.",
                },
            },
            "required": ["capability", "preview", "payload"],
            "additionalProperties": False,
        },
    )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        if context.desktop_requester is None:
            raise ToolError("Desktop actions are available only inside an approved hosted task.")
        capability, preview, payload = arguments.get("capability"), arguments.get("preview"), arguments.get("payload")
        if not isinstance(capability, str) or capability not in set(self.spec.parameters["properties"]["capability"]["enum"]):
            raise ToolError("Desktop capability is not supported.")
        if not isinstance(preview, str) or not preview.strip() or not isinstance(payload, dict):
            raise ToolError("Desktop action needs a preview and object payload.")
        try:
            result = context.desktop_requester(capability, preview[:1_000], payload)
        except Exception as exc:
            raise ToolError(f"Desktop task request failed: {str(exc)[:300]}") from exc
        return ToolResult(True, _bounded(json.dumps({"approval_required": True, **result}, ensure_ascii=False)))


class DesktopWorkflowRequestTool:
    """Create one approval-gated, sequential local task graph."""

    spec = ToolSpec(
        "desktop.request_workflow",
        "Create one durable approval-gated local workflow. The hosted planner supplies explicit inspect, plan, edit, run, verify, and report stages; the paired desktop executes them in order and never runs a stage without approval.",
        {
            "type": "object",
            "properties": {
                "preview": {"type": "string", "minLength": 1, "maxLength": 1_000},
                "stages": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 6,
                    "items": {
                        "type": "object",
                        "properties": {
                            "stage": {"type": "string", "enum": ["inspect", "plan", "edit", "run", "verify", "report"]},
                            "capability": {"type": "string", "enum": ["local_file_read", "local_file_write", "local_terminal", "local_browser", "local_integration"]},
                            "payload": {"type": "object", "maxProperties": 30},
                        },
                        "required": ["stage", "capability", "payload"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["preview", "stages"],
            "additionalProperties": False,
        },
    )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        if context.desktop_workflow_requester is None:
            raise ToolError("Desktop workflows are available only inside an approved hosted task.")
        preview, raw_stages = arguments.get("preview"), arguments.get("stages")
        if not isinstance(preview, str) or not preview.strip():
            raise ToolError("Desktop workflow needs a preview.")
        try:
            stages = validate_workflow(raw_stages)
            result = context.desktop_workflow_requester(preview[:1_000], stages)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"Desktop workflow request failed: {str(exc)[:300]}") from exc
        if not isinstance(result, dict):
            raise ToolError("Desktop workflow request returned an invalid result.")
        response = dict(result)
        # These fields are protocol guarantees, not callback-controlled data.
        response["approval_required"] = True
        response["workflow"] = workflow_summary(stages)
        return ToolResult(True, _bounded(json.dumps(response, ensure_ascii=False)))


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

    def restrict(self, names: set[str] | None) -> "ToolRegistry":
        """Return a registry view containing only the requested read tools."""
        if names is None:
            return self
        selected = ToolRegistry()
        for name, tool in self._tools.items():
            if name in names:
                selected.register(tool)
        return selected

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


def default_tool_registry(http_client: httpx.AsyncClient | None = None, *, integration_runner: Callable[[str, str, dict[str, Any]], Awaitable[str]] | None = None, integration_requester: Callable[[str, str, str, str, dict[str, Any]], dict[str, Any]] | None = None, desktop_requester: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None, desktop_workflow_requester: Callable[[str, list[dict[str, Any]]], dict[str, Any]] | None = None, include_user_integrations: bool = True) -> ToolRegistry:
    registry = ToolRegistry([
        CurrentTimeTool(),
        CalculateTool(),
        ResearchSearchTool(http_client),
        ResearchFetchTool(http_client),
    ])
    if include_user_integrations:
        registry.register(IntegrationReadTool("integration.gmail.search", "Search connected Gmail messages (read-only).", "gmail", "gmail.search", {"query": {"type": "string", "maxLength": 200}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}))
        registry.register(IntegrationReadTool("integration.calendar.list", "List connected Calendar events (read-only).", "calendar", "calendar.list", {"limit": {"type": "integer", "minimum": 1, "maximum": 20}}))
        registry.register(IntegrationReadTool("integration.drive.search", "Search connected Drive file metadata (read-only).", "drive", "drive.search", {"query": {"type": "string", "maxLength": 200}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}))
        registry.register(IntegrationReadTool("integration.github.list", "List connected GitHub repositories (read-only).", "github", "github.list", {"limit": {"type": "integer", "minimum": 1, "maximum": 20}}))
    if integration_requester is not None and include_user_integrations:
        registry.register(IntegrationApprovalRequestTool())
    if desktop_requester is not None:
        registry.register(DesktopActionRequestTool())
    if desktop_workflow_requester is not None:
        registry.register(DesktopWorkflowRequestTool())
    return registry
