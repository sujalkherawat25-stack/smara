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
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

import httpx

from .research import source_quality
from .research_tools import DeepResearchTool as DeepResearchEngine
from .research_tools import FetchUrlTool as ResearchFetchUrlTool
from .research_tools import ResearchToolError, WebSearchTool as ResearchWebSearchTool
from .workflow import validate_workflow, workflow_summary
from .workspace_contract import validate_workspace_job

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

SAFE_MATH_FUNCTIONS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
    "min": min,
    "max": max,
    "sum": sum,
    "pow": pow,
}

SAFE_MATH_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}


def _safe_number(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        if not math.isfinite(float(node.value)) or abs(float(node.value)) > 1_000_000_000_000:
            raise ToolError("Calculator values must be finite and bounded.")
        return node.value
    if isinstance(node, ast.Name):
        name = node.id.lower()
        if name in SAFE_MATH_CONSTANTS:
            return SAFE_MATH_CONSTANTS[name]
    if isinstance(node, ast.Call):
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id.lower()
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id.lower() == "math":
            func_name = node.func.attr.lower()
        if func_name and func_name in SAFE_MATH_FUNCTIONS:
            args = [_safe_number(arg) for arg in node.args]
            try:
                result = SAFE_MATH_FUNCTIONS[func_name](*args)
                if not isinstance(result, (int, float)) or not math.isfinite(float(result)) or abs(float(result)) > 1_000_000_000_000:
                    raise ToolError("Calculator result is outside safe limits.")
                return result
            except Exception as exc:
                raise ToolError(f"Math function error: {exc}") from exc
        raise ToolError("Only bounded numeric arithmetic is allowed.")
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
        "Evaluate a numeric expression or math formula (supports basic ops, sqrt, trig, log, powers, round, abs, min, max, pi, e).",
        {
            "type": "object",
            "properties": {"expression": {"type": "string", "maxLength": 500}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        expression = arguments.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ToolError("Calculator needs a numeric expression.")
        try:
            tree = ast.parse(expression.strip()[:500], mode="eval")
            if sum(1 for _ in ast.walk(tree)) > 100:
                raise ToolError("Calculator expression is too complex.")
            result = _safe_number(tree.body)
        except (SyntaxError, TypeError, ValueError) as exc:
            raise ToolError("Calculator expression is not valid.") from exc
        return ToolResult(True, str(result))


# The local Python helper is deliberately an expression evaluator, not a
# Python interpreter.  Keeping the evaluator here (rather than using exec
# with a reduced ``__builtins__`` mapping) closes the usual object-escape
# paths such as ``().__class__.__mro__`` and imports while retaining useful
# calculations and bounded data transforms.
_SAFE_PYTHON_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "float": float, "int": int, "len": len, "list": list,
    "max": max, "min": min, "round": round, "set": set,
    "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "log": math.log, "log10": math.log10,
    "log2": math.log2, "exp": math.exp, "floor": math.floor,
    "ceil": math.ceil, "pow": pow,
}
_SAFE_PYTHON_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_SAFE_PYTHON_CMPOPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}


def _validate_python_value(value: Any) -> Any:
    """Ensure an expression result is finite, bounded, and printable."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        if isinstance(value, str) and len(value) > 8_000:
            raise ToolError("Python result is too large.")
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)) or abs(float(value)) > 1_000_000_000_000:
            raise ToolError("Python values must be finite and bounded.")
        return value
    if isinstance(value, (list, tuple, set)):
        if len(value) > 200:
            raise ToolError("Python collections are too large.")
        return type(value)(_validate_python_value(item) for item in value)
    if isinstance(value, dict):
        if len(value) > 200:
            raise ToolError("Python mappings are too large.")
        return {
            _validate_python_value(key): _validate_python_value(item)
            for key, item in value.items()
        }
    raise ToolError("Python expression returned an unsupported value.")


def _eval_restricted_python(expression: str) -> Any:
    if not isinstance(expression, str) or not expression.strip():
        raise ToolError("Python execution requires a non-empty expression.")
    try:
        tree = ast.parse(expression[:8_000].strip(), mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ToolError("Only a single safe Python expression is allowed.") from exc
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ToolError("Python expression is too complex.")

    def evaluate(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (str, int, float, bool)) or node.value is None:
                return _validate_python_value(node.value)
            raise ToolError("Unsupported Python constant.")
        if isinstance(node, ast.Name):
            name = node.id.lower()
            if name in _SAFE_PYTHON_FUNCTIONS:
                return _SAFE_PYTHON_FUNCTIONS[name]
            if name in SAFE_MATH_CONSTANTS:
                return SAFE_MATH_CONSTANTS[name]
            raise ToolError(f"Unknown or unsafe Python name: {node.id}.")
        if isinstance(node, ast.List):
            return _validate_python_value([evaluate(item) for item in node.elts])
        if isinstance(node, ast.Tuple):
            return _validate_python_value(tuple(evaluate(item) for item in node.elts))
        if isinstance(node, ast.Set):
            return _validate_python_value({evaluate(item) for item in node.elts})
        if isinstance(node, ast.Dict):
            if len(node.keys) > 200:
                raise ToolError("Python mappings are too large.")
            values = {}
            for key, value in zip(node.keys, node.values):
                if key is None:
                    raise ToolError("Dictionary unpacking is not supported.")
                values[evaluate(key)] = evaluate(value)
            return _validate_python_value(values)
        if isinstance(node, ast.Attribute):
            # The only attribute namespace is the explicit, immutable math
            # module surface.  No object attributes or dunder names pass.
            if isinstance(node.value, ast.Name) and node.value.id == "math" and node.attr in (set(SAFE_MATH_FUNCTIONS) | set(SAFE_MATH_CONSTANTS)):
                return SAFE_MATH_FUNCTIONS.get(node.attr, SAFE_MATH_CONSTANTS.get(node.attr))
            raise ToolError("Unsafe Python functions and object attribute access are not allowed.")
        if isinstance(node, ast.Call):
            if node.keywords or len(node.args) > 20:
                raise ToolError("Only bounded positional Python calls are allowed.")
            fn = evaluate(node.func)
            if fn not in _SAFE_PYTHON_FUNCTIONS.values() and fn not in SAFE_MATH_FUNCTIONS.values():
                raise ToolError("Python function is not in the safe allowlist.")
            try:
                return _validate_python_value(fn(*(evaluate(arg) for arg in node.args)))
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(f"Python expression failed: {type(exc).__name__}.") from exc
        if isinstance(node, ast.UnaryOp) and type(node.op) in {ast.UAdd, ast.USub, ast.Not}:
            operand = evaluate(node.operand)
            if isinstance(node.op, ast.Not):
                return not operand
            return _validate_python_value(operator.pos(operand) if isinstance(node.op, ast.UAdd) else operator.neg(operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_PYTHON_BINOPS:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Pow) and isinstance(right, (int, float)) and abs(float(right)) > 12:
                raise ToolError("Python exponent is too large.")
            try:
                return _validate_python_value(_SAFE_PYTHON_BINOPS[type(node.op)](left, right))
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(f"Python arithmetic failed: {type(exc).__name__}.") from exc
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [evaluate(value) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = evaluate(node.left)
            result = True
            for op, comparator in zip(node.ops, node.comparators):
                if type(op) not in _SAFE_PYTHON_CMPOPS:
                    raise ToolError("Unsupported Python comparison.")
                right = evaluate(comparator)
                result = result and bool(_SAFE_PYTHON_CMPOPS[type(op)](left, right))
                left = right
            return result
        if isinstance(node, ast.Subscript):
            value, index = evaluate(node.value), evaluate(node.slice)
            if isinstance(index, slice):
                raise ToolError("Python slices are not supported.")
            if not isinstance(index, int):
                raise ToolError("Python indexes must be integers.")
            try:
                return _validate_python_value(value[index])
            except Exception as exc:
                raise ToolError("Python index is outside the bounded value.") from exc
        raise ToolError("Only safe Python expressions are supported; statements and imports are blocked.")

    return evaluate(tree)


class ExecutePythonTool:
    spec = ToolSpec(
        "execute_python",
        "Safely execute a Python snippet for complex calculation, data transformation, or text processing.",
        {
            "type": "object",
            "properties": {"code": {"type": "string", "maxLength": 8_000}},
            "required": ["code"],
            "additionalProperties": False,
        },
    )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ToolError("Python execution requires a non-empty expression.")
        try:
            value = _eval_restricted_python(code)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"Python execution failed: {type(exc).__name__}.") from exc
        return ToolResult(True, _bounded(str(value)))



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


class ResearchDeepTool:
    """Deterministic multi-source research for explicit research questions."""

    spec = ToolSpec(
        "research.deep",
        "Run a bounded research pass: search several distinct angles, deduplicate and diversify sources, fetch readable pages concurrently, and return labelled evidence with citations. Use this for detailed analysis, current events, comparisons, or requests for sources/citations.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 2_000},
                "subqueries": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": 3},
                "max_sources": {"type": "integer", "minimum": 2, "maximum": 6},
                "include_domains": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 10},
                "exclude_domains": {"type": "array", "items": {"type": "string", "maxLength": 120}, "maxItems": 10},
                "focus": {"type": "string", "maxLength": 500},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._tool = DeepResearchEngine(http_client)

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("Research needs a non-empty question.")
        try:
            result = await self._tool.run(
                query,
                subqueries=arguments.get("subqueries"),
                max_sources=arguments.get("max_sources", 5),
                include_domains=arguments.get("include_domains"),
                exclude_domains=arguments.get("exclude_domains"),
                focus=arguments.get("focus", ""),
            )
        except ResearchToolError as exc:
            raise ToolError(str(exc)) from exc
        return ToolResult(
            True,
            _bounded(result.content, 16_000),
            citations=result.citations,
            meta={
                "provider": ", ".join(result.providers),
                "providers": result.providers,
                "queries": result.queries,
                "sources": result.sources,
                "fetched": result.fetched,
                "failed": result.failed,
            },
        )


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
            raise ToolError(f"Integration read failed safely ({type(exc).__name__}). Please retry shortly.") from exc
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
            raise ToolError(f"Approval request failed safely ({type(exc).__name__}). Please retry shortly.") from exc
        return ToolResult(True, _bounded(json.dumps({"approval_required": result.get("status") == "awaiting_approval", "status": result.get("status"), "action_id": result.get("id")}, ensure_ascii=False)))


class DesktopActionRequestTool:
    """Create an approved desktop step; never executes on the worker host."""

    spec = ToolSpec(
        "desktop.request_action",
        "Create a durable approval-gated task for a paired desktop capability. The local action is not run by this chat step. local_file_read supports read_file, list_tree, search_text, find_files, git_summary, and metadata-only workspace_snapshot. local_file_write supports preview_only planning, bounded text edits, document creation/editing, and prepare_workspace isolation: DOCX reports, XLSX workbooks, PPTX briefings, PDF reports, PDF merge, and PDF page extraction. Every mutation returns a preview and local undo id.",
        {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "enum": ["local_file_read", "local_file_write", "local_terminal", "local_browser", "local_integration"]},
                "preview": {"type": "string", "minLength": 1, "maxLength": 1_000},
                "payload": {
                    "type": "object",
                    "maxProperties": 30,
                    "description": "Capability payload. local_file_read supports read_file, list_tree, literal search_text, find_files, git_summary, and metadata-only workspace_snapshot. local_file_write supports preview_only, write/append/patch/rename/move/delete/undo, prepare_workspace isolation, create_docx and edit_docx (title/sections or exact text replacement), create_xlsx and edit_xlsx (bounded sheets, rows, cells, safe aggregate formulas), create_pptx and edit_pptx (briefing slides), create_pdf, merge_pdf, and extract_pdf_pages. All document output stays under an approved folder, is capped at 8 MB, and returns a readable preview plus local undo id. local_terminal uses argv and cwd. local_browser supports open, explicit isolated connector handoff ({operation:handoff, provider:github|google}), inspect_text, inspect_dom, or download with an approved HTTP(S) url; handoff uses a separate local Chromium profile and never returns cookies, paths, codes, or passkeys, inspect_dom accepts a simple tag/#id/.class selector and bounded max_elements, while download requires a destination inside an approved folder and is capped at 50 MB. local_integration supports only approval-gated local Tavily search ({provider:tavily, operation:search, query}) and GitHub repository listing ({provider:github, operation:list_repositories, limit}); their credentials stay in the desktop vault.",
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
        if "workspace_job" in payload:
            try:
                job = validate_workspace_job(payload["workspace_job"])
            except RuntimeError as exc:
                raise ToolError(str(exc)) from exc
            if capability not in job.allowed_capabilities:
                raise ToolError("workspace_job does not allow the requested desktop capability.")
        try:
            result = context.desktop_requester(capability, preview[:1_000], payload)
        except Exception as exc:
            raise ToolError(
                f"Desktop task request failed safely ({type(exc).__name__}). "
                "Check that the paired Desktop is online, then retry."
            ) from exc
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
            raise ToolError(
                f"Desktop workflow request failed safely ({type(exc).__name__}). "
                "Check that the paired Desktop is online, then retry."
            ) from exc
        if not isinstance(result, dict):
            raise ToolError("Desktop workflow request returned an invalid result.")
        response = dict(result)
        # These fields are protocol guarantees, not callback-controlled data.
        response["approval_required"] = True
        response["workflow"] = workflow_summary(stages)
        return ToolResult(True, _bounded(json.dumps(response, ensure_ascii=False)))


class GraphInspectSymbolTool:
    spec = ToolSpec(
        "graph.inspect_symbol",
        "Inspect a code symbol (function, class, method) across the workspace using AST indexing to view its exact signature, docstring, callers, and callees.",
        {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Name of the class, function, or method to inspect"}
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    )

    def __init__(self, workspace_root: str | Path | None = None):
        if workspace_root is None:
            raise ValueError("An explicit approved local workspace root is required for graph tools.")
        self._root = Path(workspace_root).resolve()
        self._graph: Any = None

    def _get_graph(self):
        if self._graph is None:
            from .code_graph import CodePropertyGraph
            self._graph = CodePropertyGraph(self._root)
        return self._graph

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        symbol = arguments.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ToolError("graph.find_references requires a non-empty symbol name.")
        graph = self._get_graph()
        refs = graph.find_references(symbol.strip())
        return ToolResult(True, _bounded(json.dumps(refs, ensure_ascii=False, indent=2)))

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        symbol = arguments.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ToolError("graph.inspect_symbol requires a non-empty symbol name.")
        graph = self._get_graph()
        info = graph.inspect_symbol(symbol.strip())
        if not info:
            raise ToolError(f"Symbol '{symbol}' was not found in the workspace code graph.")
        return ToolResult(True, _bounded(json.dumps(info, ensure_ascii=False, indent=2)))


class GraphBlastRadiusTool:
    spec = ToolSpec(
        "graph.blast_radius",
        "Pre-calculate the blast radius and downstream impact (dependent files, callers, and associated test suites) of editing a symbol or file.",
        {
            "type": "object",
            "properties": {
                "target": {"type": "string", "minLength": 1, "maxLength": 300, "description": "File path (e.g. 'src/smara/auth.py') or symbol name to analyze"}
            },
            "required": ["target"],
            "additionalProperties": False,
        },
    )

    def __init__(self, workspace_root: str | Path | None = None):
        if workspace_root is None:
            raise ValueError("An explicit approved local workspace root is required for graph tools.")
        self._root = Path(workspace_root).resolve()
        self._graph: Any = None

    def _get_graph(self):
        if self._graph is None:
            from .code_graph import CodePropertyGraph
            self._graph = CodePropertyGraph(self._root)
        return self._graph

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        target = arguments.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ToolError("graph.blast_radius requires a target file or symbol name.")
        graph = self._get_graph()
        radius = graph.blast_radius(target.strip())
        return ToolResult(True, _bounded(json.dumps(radius, ensure_ascii=False, indent=2)))


class GraphFindReferencesTool:
    spec = ToolSpec(
        "graph.find_references",
        "Find all definitions and call references of a symbol across the workspace graph.",
        {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "minLength": 1, "maxLength": 200, "description": "Symbol name to find references for"}
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    )

    def __init__(self, workspace_root: str | Path | None = None):
        if workspace_root is None:
            raise ValueError("An explicit approved local workspace root is required for graph tools.")
        self._root = Path(workspace_root).resolve()
        self._graph: Any = None

    def _get_graph(self):
        if self._graph is None:
            from .code_graph import CodePropertyGraph
            self._graph = CodePropertyGraph(self._root)
        return self._graph

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        symbol = arguments.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ToolError("graph.find_references requires a non-empty symbol name.")
        graph = self._get_graph()
        refs = graph.find_references(symbol.strip())
        return ToolResult(True, _bounded(json.dumps(refs, ensure_ascii=False, indent=2)))

class DesktopActionRequestTool:
    """Create an approved desktop step; never executes on the worker host."""

    spec = ToolSpec(
        "desktop.request_action",
        "Create a durable approval-gated task for a paired desktop capability. The local action is not run by this chat step. local_file_read supports read_file, list_tree, search_text, find_files, git_summary, and metadata-only workspace_snapshot. local_file_write supports preview_only planning, bounded text edits, document creation/editing, and prepare_workspace isolation: DOCX reports, XLSX workbooks, PPTX briefings, PDF reports, PDF merge, and PDF page extraction. Every mutation returns a preview and local undo id.",
        {
            "type": "object",
            "properties": {
                "capability": {"type": "string", "enum": ["local_file_read", "local_file_write", "local_terminal", "local_browser", "local_integration"]},
                "preview": {"type": "string", "minLength": 1, "maxLength": 1_000},
                "payload": {
                    "type": "object",
                    "maxProperties": 30,
                    "description": "Capability payload. local_file_read supports read_file, list_tree, literal search_text, find_files, git_summary, and metadata-only workspace_snapshot. local_file_write supports preview_only, write/append/patch/rename/move/delete/undo, prepare_workspace isolation, create_docx and edit_docx (title/sections or exact text replacement), create_xlsx and edit_xlsx (bounded sheets, rows, cells, safe aggregate formulas), create_pptx and edit_pptx (briefing slides), create_pdf, merge_pdf, and extract_pdf_pages. All document output stays under an approved folder, is capped at 8 MB, and returns a readable preview plus local undo id. local_terminal uses argv and cwd. local_browser supports open, explicit isolated connector handoff ({operation:handoff, provider:github|google}), inspect_text, inspect_dom, or download with an approved HTTP(S) url; handoff uses a separate local Chromium profile and never returns cookies, paths, codes, or passkeys, inspect_dom accepts a simple tag/#id/.class selector and bounded max_elements, while download requires a destination inside an approved folder and is capped at 50 MB. local_integration supports only approval-gated local Tavily search ({provider:tavily, operation:search, query}) and GitHub repository listing ({provider:github, operation:list_repositories, limit}); their credentials stay in the desktop vault.",
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
        if "workspace_job" in payload:
            try:
                job = validate_workspace_job(payload["workspace_job"])
            except RuntimeError as exc:
                raise ToolError(str(exc)) from exc
            if capability not in job.allowed_capabilities:
                raise ToolError("workspace_job does not allow the requested desktop capability.")
        try:
            result = context.desktop_requester(capability, preview[:1_000], payload)
        except Exception as exc:
            raise ToolError(
                f"Desktop task request failed safely ({type(exc).__name__}). "
                "Check that the paired Desktop is online, then retry."
            ) from exc
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
            raise ToolError(
                f"Desktop workflow request failed safely ({type(exc).__name__}). "
                "Check that the paired Desktop is online, then retry."
            ) from exc
        if not isinstance(result, dict):
            raise ToolError("Desktop workflow request returned an invalid result.")
        response = dict(result)
        # These fields are protocol guarantees, not callback-controlled data.
        response["approval_required"] = True
        response["workflow"] = workflow_summary(stages)
        return ToolResult(True, _bounded(json.dumps(response, ensure_ascii=False)))


class SemanticSearchTool:
    spec = ToolSpec(
        "semantic_search",
        "Search codebase offline using natural language intent and dense embeddings. Useful for discovering functions, classes, and logic without knowing exact symbol names.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language query or intent (e.g. 'where do we handle session tokens or encryption?')"},
                "limit": {"type": "integer", "description": "Maximum number of results to return", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            from .semantic_search import SemanticCodeSearcher
        except ImportError:
            from semantic_search import SemanticCodeSearcher
        query = str(arguments.get("query") or "").strip()
        limit = int(arguments.get("limit") or 5)
        searcher = SemanticCodeSearcher()
        results = searcher.search(query, limit=limit)
        if not results:
            return ToolResult(True, f"No code symbols matched semantic query: '{query}'")
        parts = [f"Found {len(results)} matching code symbols for '{query}':\n"]
        for r in results:
            parts.append(f"- **{r.symbol_name}** (`{r.file_path}:{r.start_line}`) • {r.percentage}% match ({r.match_type.upper()})\n  Doc: {r.docstring or 'None'}\n")
        return ToolResult(True, "\n".join(parts), meta={"results_count": len(results)})


class GitWorkspaceTool:
    spec = ToolSpec(
        "git_workspace",
        "Inspect git status, commits, and branch information for the workspace.",
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["status", "branches", "log", "conflicts"], "description": "Git operation"},
                "limit": {"type": "integer", "description": "Limit for commit log", "minimum": 1, "maximum": 20},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
    )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            from .git_agent import GitWorkspaceManager
        except ImportError:
            from git_agent import GitWorkspaceManager
        mgr = GitWorkspaceManager()
        op = arguments.get("operation", "status")
        if op == "status":
            st = mgr.get_status()
            return ToolResult(True, f"Branch: {st.branch} | Clean: {st.is_clean} | Modified: {len(st.modified_files)} | Staged: {len(st.staged_files)} | Untracked: {len(st.untracked_files)}")
        elif op == "branches":
            branches = mgr.get_branches()
            return ToolResult(True, f"Branches: {', '.join(branches)}")
        elif op == "log":
            limit = int(arguments.get("limit") or 5)
            commits = mgr.get_commit_log(limit=limit)
            return ToolResult(True, "\n".join(f"- `{c.hash}` {c.message} ({c.author_name})" for c in commits))
        elif op == "conflicts":
            conflicts = mgr.detect_conflicts()
            return ToolResult(True, f"Detected {len(conflicts)} conflict(s).")
        return ToolResult(False, f"Unsupported operation: {op}")


class BrowserAutomationTool:
    spec = ToolSpec(
        "browser_automation",
        "Headless browser scraping, PNG screenshot capture, and E2E DOM verification.",
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["scrape", "screenshot"], "description": "Browser action to execute"},
                "url": {"type": "string", "description": "Target HTTP/HTTPS URL or file path to inspect"},
            },
            "required": ["operation", "url"],
            "additionalProperties": False,
        },
    )

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            from .browser_sidecar import BrowserSidecarEngine
        except ImportError:
            from browser_sidecar import BrowserSidecarEngine
        engine = BrowserSidecarEngine()
        op = arguments.get("operation", "scrape")
        url = str(arguments.get("url") or "").strip()
        # Security SSRF check: block cloud metadata
        if any(bad in url.lower() for bad in ["169.254.169.254", "metadata.google.internal", "instance-data"]):
            return ToolResult(False, "Security Guardrail: Access to cloud metadata endpoints is prohibited.")
        if op == "scrape":
            res = engine.scrape_url(url)
            if res["success"]:
                headings = ", ".join(res.get("headings", [])[:4])
                return ToolResult(True, f"Title: {res.get('title')}\nHeadings: {headings}\n\nContent:\n{res.get('content_snippet', '')[:1200]}")
            return ToolResult(False, f"Scrape failed: {res.get('error')}")
        elif op == "screenshot":
            res = engine.capture_screenshot(url)
            if res["success"]:
                return ToolResult(True, f"Screenshot successfully captured ({res.get('file_size')} bytes). File: {res.get('file_path')}")
            return ToolResult(False, "Screenshot capture failed.")
        return ToolResult(False, f"Unsupported operation: {op}")


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
            raise ToolError(f"Tool '{name}' is not registered.")
        if tool.spec.side_effecting or tool.spec.requires_approval:
            raise ToolError("This tool requires a durable approved task and cannot run directly.")
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be an object.")
        properties = tool.spec.parameters.get("properties", {})
        unknown = set(arguments) - set(properties)
        if unknown and tool.spec.parameters.get("additionalProperties") is False:
            raise ToolError("Tool arguments contain unsupported fields.")
        result = await tool.run(arguments, context)
        limit = 16_000 if name == "research.deep" else MAX_TOOL_RESULT_CHARS
        return ToolResult(result.ok, _bounded(result.content, limit), list(result.citations)[:20], dict(result.meta))


def default_tool_registry(
    http_client: httpx.AsyncClient | None = None,
    *,
    integration_runner: Callable[[str, str, dict[str, Any]], Awaitable[str]] | None = None,
    integration_requester: Callable[[str, str, str, str, dict[str, Any]], dict[str, Any]] | None = None,
    desktop_requester: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
    desktop_workflow_requester: Callable[[str, list[dict[str, Any]]], dict[str, Any]] | None = None,
    include_user_integrations: bool = True,
    include_python: bool = False,
    include_graph: bool = False,
    graph_workspace_root: str | Path | None = None,
    include_workspace_tools: bool = False,
) -> ToolRegistry:
    registry = ToolRegistry([
        CurrentTimeTool(),
        CalculateTool(),
        ResearchDeepTool(http_client),
        ResearchSearchTool(http_client),
        ResearchFetchTool(http_client),
    ])
    if include_workspace_tools:
        registry.register(SemanticSearchTool())
        registry.register(GitWorkspaceTool())
        registry.register(BrowserAutomationTool())
    if include_python:
        registry.register(ExecutePythonTool())
    if include_graph:
        if graph_workspace_root is None:
            raise ValueError("An explicit approved local workspace root is required for graph tools.")
        registry.register(GraphInspectSymbolTool(graph_workspace_root))
        registry.register(GraphBlastRadiusTool(graph_workspace_root))
        registry.register(GraphFindReferencesTool(graph_workspace_root))
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
