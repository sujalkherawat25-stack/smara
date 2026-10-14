"""Local wire adapter, not an agent loop or tool executor.

The copied Rust runtime owns tools, permissions, sessions and sandboxing. This
adapter only translates Responses wire items to a configured Chat Completions
provider. Unsupported hosted tools fail explicitly instead of disappearing.
Secrets remain in memory; a per-process bearer token protects the loopback port.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import httpx
from .native_limits import ResearchGuard, ResearchLimitError
from .native_limits import research_tool
from .native_admission import AdmissionError, peer_context
from .native_budget import BudgetLimitError


class UnsupportedWireFeature(ValueError):
    pass


SARVAM_CONTEXT_WINDOWS = {
    "glm5.3": 1_048_576,
    "deepseekv4.1-flash": 1_048_576,
    "gemma4": 131_072,
    "sarvam-105b": 131_072,
    "sarvam-105b-conversations": 32_768,
}
SARVAM_V2_MODELS = frozenset({"glm5.3", "deepseekv4.1-flash", "gemma4", "sarvam-105b"})
SARVAM_V1_MODELS = frozenset({"sarvam-105b", "sarvam-105b-conversations"})
SARVAM_REASONING_MODELS = frozenset({"glm5.3", "deepseekv4.1-flash"})
SARVAM_MODEL_ALIASES = {
    "glm5.3-flash": "glm5.3",
    "deepseekv4-flash": "deepseekv4.1-flash",
    "deepseek-v4-flash": "deepseekv4.1-flash",
}


@dataclass(frozen=True)
class ChatEndpoint:
    base_url: str
    model: str
    api_key: str
    auth_header: str = "authorization"
    context_window: int | None = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.hostname == "api.sarvam.ai":
            # Migrate IDs used by earlier Smara builds to Sarvam's current
            # exact IDs before calculating limits or sending the request.
            canonical = SARVAM_MODEL_ALIASES.get(self.model.strip().lower())
            if canonical:
                object.__setattr__(self, "model", canonical)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid configured model endpoint")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote model endpoints must use HTTPS")
        if parsed.query or parsed.fragment or not self.model.strip():
            raise ValueError("Invalid model endpoint or name")
        path = parsed.path.rstrip("/") or "/"
        if parsed.hostname == "api.sarvam.ai":
            if path.endswith("/responses"):
                raise ValueError("Smara's Codex-compatible adapter translates through Chat Completions. Set the base URL to https://api.sarvam.ai/v2, not /responses.")
            if path not in {"/v1", "/v1/chat/completions", "/v2", "/v2/chat/completions"}:
                raise ValueError("Use the exact Sarvam Chat Completions base URL: https://api.sarvam.ai/v1 or /v2.")
            route_version = "v1" if path.startswith("/v1") else "v2"
            if route_version == "v1" and self.model in SARVAM_V2_MODELS - {"sarvam-105b"}:
                raise ValueError("This Sarvam open-weight model requires the /v2 Chat Completions API. Set the base URL to https://api.sarvam.ai/v2.")
            if route_version == "v2" and self.model == "sarvam-105b-conversations":
                raise ValueError("Sarvam 105B Conversations is available on /v1 only. Set the base URL to https://api.sarvam.ai/v1.")
            allowed_models = SARVAM_V1_MODELS if route_version == "v1" else SARVAM_V2_MODELS
            if self.model not in allowed_models:
                route = "https://api.sarvam.ai/v1" if route_version == "v1" else "https://api.sarvam.ai/v2"
                accepted = ", ".join(sorted(allowed_models))
                raise ValueError(f"Sarvam model '{self.model}' is not available on /{route_version}. Use {route} with one of: {accepted}.")
        if self.auth_header.lower() not in {"authorization", "api-subscription-key"}:
            raise ValueError("Unsupported credential header")
        if self.context_window is not None and (isinstance(self.context_window, bool) or not isinstance(self.context_window, int) or self.context_window < 4096):
            raise ValueError("Invalid configured context window")

    @property
    def effective_context_window(self) -> int | None:
        provider_window = None
        if urlparse(self.base_url).hostname == "api.sarvam.ai":
            provider_window = SARVAM_CONTEXT_WINDOWS.get(self.model)
        if self.context_window is not None:
            return min(self.context_window, provider_window) if provider_window else self.context_window
        # Published provider capability metadata, not task/prompt routing.
        # https://docs.sarvam.ai/api/getting-started/models/openweight
        return provider_window  # Unknown is not a fabricated provider limit.

    @property
    def default_max_output_tokens(self) -> int | None:
        # Sarvam's /v2/chat/completions defaults max_tokens to 2,048. GLM-5.3
        # bills reasoning against that same small cap, while its Responses
        # endpoint has no default. Give this adapter a bounded, useful budget
        # instead of silently inheriting the chat endpoint's short default.
        parsed = urlparse(self.base_url)
        if parsed.hostname == "api.sarvam.ai" and parsed.path.startswith("/v2") and self.model in SARVAM_V2_MODELS:
            # Sarvam-105B has a documented Starter plan ceiling of 4,096;
            # use that safe floor even on its V2 route. The open-weight V2
            # model pages document larger token windows and support long turns.
            return 4_096 if self.model == "sarvam-105b" else 16_384
        if parsed.hostname == "api.sarvam.ai" and parsed.path.startswith("/v1") and self.model in SARVAM_V1_MODELS:
            return 4_096
        return None

    @property
    def is_sarvam(self) -> bool:
        return urlparse(self.base_url).hostname == "api.sarvam.ai"

    @property
    def url(self) -> str:
        base = self.base_url.rstrip("/")
        return base if base.endswith("/chat/completions") else base + "/chat/completions"

    def headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {self.auth_header: ("Bearer " + self.api_key) if self.auth_header.lower() == "authorization" else self.api_key}


def probe_endpoint(endpoint: ChatEndpoint, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Send one fixed, bounded request to verify a configured model endpoint.

    This is called only from the explicit Settings test action. Prompts, provider
    response bodies and credentials are never returned in diagnostics.
    """
    if not endpoint.api_key:
        raise ValueError("No readable key is saved for this model. Update it in Settings → Models.")
    owns_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False, trust_env=False)
    started = time.monotonic()
    try:
        response = client.post(
            endpoint.url,
            headers=endpoint.headers(),
            json={
                "model": endpoint.model,
                "messages": [{"role": "user", "content": "Reply exactly OK. Do not explain."}],
                "max_tokens": 256,
                **({"reasoning_effort": "low"} if endpoint.is_sarvam and endpoint.model in SARVAM_REASONING_MODELS else {}),
                "stream": False,
            },
        )
        if response.status_code != 200:
            raise ValueError(provider_status_message(response.status_code, endpoint))
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise ValueError("The model endpoint returned invalid JSON, not a compatible chat response.") from None
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("The model endpoint responded, but not with a compatible chat-completion result.")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str) or not message["content"].strip():
            raise ValueError("The endpoint is reachable, but the model returned no visible text within the small test budget. Check its reasoning and output-token limits.")
        return {
            "ok": True,
            "model": endpoint.model,
            "elapsed_ms": max(0, round((time.monotonic() - started) * 1000)),
        }
    except httpx.TimeoutException:
        raise ValueError("The model endpoint timed out. Check the URL and network, then retry.") from None
    except httpx.HTTPError:
        raise ValueError("The model endpoint is unreachable. Check the URL and network, then retry.") from None
    finally:
        if owns_client:
            client.close()


def provider_status_message(status: int, endpoint: ChatEndpoint) -> str:
    """Translate provider HTTP status into safe, actionable setup guidance."""
    if status in (401, 403):
        return "The model provider rejected the saved API key or this key lacks access to the selected model. Check Settings → Models and the provider's model access."
    if status == 402:
        return "The model provider reports that its credit or quota is exhausted. Check the provider account."
    if status == 404:
        return "The provider endpoint or exact model ID was not found. Check the configured API version and model ID."
    if status == 413:
        return "The request is larger than the provider accepts. Reduce the attached files or message size and retry."
    if status in (400, 422):
        if endpoint.is_sarvam:
            return "Sarvam rejected this request. Check the exact model ID, beta access for this API key, input/context size, and account token limits. Private provider details are withheld."
        return "The provider rejected this request. Check the model ID, input size, and model token/parameter limits. Private provider details are withheld."
    if status == 429:
        return "The model provider rate limit was reached. Wait briefly and retry."
    if status in (502, 503, 504):
        return "The model provider is temporarily unavailable or overloaded. Wait briefly and retry."
    return f"The model provider returned HTTP {status}. Check the endpoint and provider status; private response details are withheld."


def _alias(namespace: str | None, name: str) -> str:
    raw = f"{namespace}__{name}" if namespace else name
    if len(raw) <= 64 and all(char.isascii() and (char.isalnum() or char in "_-") for char in raw):
        return raw
    return "tool_" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def _content(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise UnsupportedWireFeature("Invalid message content")
    result = []
    for part in value:
        kind = part.get("type")
        if kind in {"input_text", "output_text", "text"}:
            result.append({"type": "text", "text": str(part.get("text", ""))})
        elif kind == "input_image":
            result.append({"type": "image_url", "image_url": {"url": part["image_url"]}})
        else:
            raise UnsupportedWireFeature(f"Unsupported content type: {kind}")
    return result


def _reasoning_effort(request: dict[str, Any], model: str) -> tuple[str | None, bool]:
    """Map Codex effort settings to Sarvam's smaller documented set.

    The boolean requests DeepSeek's documented no-thinking mode. GLM-5.3 has
    no documented off switch on chat, so `none` maps to its lowest effort.
    """
    reasoning = request.get("reasoning")
    effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
    if effort is None or model not in SARVAM_REASONING_MODELS:
        return None, False
    if not isinstance(effort, str):
        raise UnsupportedWireFeature("Sarvam reasoning effort must be a supported text value")
    value = effort.lower()
    if value in {"none", "minimal", "low"}:
        return "low", value == "none" and model == "deepseekv4.1-flash"
    if value in {"medium", "high"}:
        return "high", False
    if value in {"xhigh", "ultra", "persistent", "max"}:
        return "max", False
    raise UnsupportedWireFeature("This Sarvam model accepts low, medium, high, or max reasoning effort")


def _image_urls(request: dict[str, Any]):
    inputs = request.get("input", [])
    if isinstance(inputs, str):
        return []
    found = []
    for item in inputs:
        if not isinstance(item, dict) or item.get("type", "message") != "message":
            continue
        content = item.get("content", [])
        if isinstance(content, list):
            found.extend(part.get("image_url") for part in content if isinstance(part, dict) and part.get("type") == "input_image")
    return found


def _response_format(request: dict[str, Any]) -> dict[str, Any] | None:
    text = request.get("text")
    if not isinstance(text, dict):
        return None
    format_spec = text.get("format")
    if format_spec is None:
        return None
    if not isinstance(format_spec, dict) or format_spec.get("type") != "json_schema":
        raise UnsupportedWireFeature("Unsupported Responses text format")
    schema = format_spec.get("schema")
    name = format_spec.get("name", "codex_output_schema")
    strict = format_spec.get("strict", False)
    if not isinstance(schema, dict) or not isinstance(name, str) or not isinstance(strict, bool):
        raise UnsupportedWireFeature("Invalid Responses JSON schema format")
    return {"type": "json_schema", "json_schema": {"name": name, "strict": strict, "schema": schema}}


def translate_request(
    request: dict[str, Any], model: str, *, default_max_output_tokens: int | None = None,
    sarvam: bool = False,
) -> tuple[dict[str, Any], dict[str, dict]]:
    if request.get("previous_response_id"):
        raise UnsupportedWireFeature("This adapter requires full input history, not previous_response_id")
    image_urls = _image_urls(request)
    if sarvam and image_urls:
        if model != "gemma4":
            raise UnsupportedWireFeature("Sarvam image input requires Gemma 4; the selected Sarvam model accepts text only")
        if any(not isinstance(url, str) or not url.startswith("data:image/") or ";base64," not in url for url in image_urls):
            raise UnsupportedWireFeature("Sarvam accepts inline base64 images only; remote image URLs are not supported")
    mapping: dict[str, dict] = {}
    functions = []

    def tool(spec: dict, namespace: str | None = None) -> None:
        kind = spec.get("type")
        if kind == "namespace":
            for child in spec.get("tools", []):
                tool(child, spec["name"])
            return
        if kind not in {"function", "custom"}:
            raise UnsupportedWireFeature(f"Provider cannot execute hosted tool type: {kind}. Configure an MCP equivalent.")
        name = spec["name"]
        alias = _alias(namespace, name)
        if alias in mapping:
            raise UnsupportedWireFeature("Tool names collide after provider translation")
        mapping[alias] = {"type": kind, "name": name, "namespace": namespace}
        parameters = spec.get("parameters", {"type": "object", "properties": {}})
        description = str(spec.get("description", ""))
        if kind == "custom":
            parameters = {"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"], "additionalProperties": False}
            description += "\nReturn the exact freeform tool input in the input field."
            grammar = spec.get("format", {}).get("definition")
            if grammar:
                description += "\nTool input grammar:\n" + grammar
        function = {"name": alias, "description": description, "parameters": parameters}
        if kind == "function" and isinstance(spec.get("strict"), bool):
            function["strict"] = spec["strict"]
        functions.append({"type": "function", "function": function})

    for spec in request.get("tools", []):
        tool(spec)
    messages = []
    if request.get("instructions"):
        messages.append({"role": "system", "content": request["instructions"]})
    if any(name.endswith("pdf_inspect") for name in mapping):
        messages.append({"role": "system", "content":
            "For PDF deliverables: retain the editable generator, compile it before running, and write large documents in bounded sections. "
            "With ReportLab, explicitly transition from cover to body PageTemplate; Paragraph textColor overrides TableStyle TEXTCOLOR. "
            "Use pdf_inspect on every page range, repair warnings, and render page PNGs using its preview_command_argv via the native command sandbox. "
            "Text extraction and file hashes do not prove readable layout. Do not claim visual verification when no image review succeeded. "
            "For a text-only model, do not call view_image; image_analyze is a separate approval-gated upload, not an automatic fallback. "
            "Read current version/revision from source instead of carrying stale values from conversation history."})
    if any(research_tool(name) for name in mapping):
        messages.append({"role": "system", "content":
            "Research efficiently: use focused queries, reuse fetched source IDs and their next_offset for pagination, "
            "and never repeat a failed/unsupported URL without a changed retrieval method. Fetch_url reads text pages, not PDFs/images. "
            "For documents use document_ocr, and for image understanding use image_analyze, only if available and after native upload approval. "
            "Do not substitute a shell upload to evade approval. Finish when evidence is sufficient; report gaps rather than endlessly collecting. "
            "Research workers should return their evidence and limitations promptly so the parent can synthesize partial results."})
    inputs = request.get("input", [])
    if isinstance(inputs, str):
        inputs = [{"type": "message", "role": "user", "content": inputs}]
    for item in inputs:
        kind = item.get("type", "message")
        if kind == "message":
            role = item["role"]
            if role == "developer":
                role = "system"
            messages.append({"role": role, "content": _content(item.get("content", ""))})
        elif kind in {"function_call", "custom_tool_call"}:
            name = _alias(item.get("namespace"), item["name"])
            arguments = item.get("arguments", "{}") if kind == "function_call" else json.dumps({"input": item.get("input", "")})
            call = {"id": item["call_id"], "type": "function", "function": {"name": name, "arguments": arguments}}
            if messages and messages[-1]["role"] == "assistant" and "tool_calls" in messages[-1]:
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            output = item.get("output", "")
            messages.append({"role": "tool", "tool_call_id": item["call_id"], "content": output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)})
        elif kind == "reasoning":
            # Provider-specific encrypted Responses reasoning cannot be replayed
            # to a Chat endpoint. The actual tool/message history is retained.
            continue
        else:
            raise UnsupportedWireFeature(f"Unsupported input item: {kind}")
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
    if functions:
        payload["tools"] = functions
        choice = request.get("tool_choice", "auto")
        if isinstance(choice, dict):
            if choice.get("type") != "function":
                raise UnsupportedWireFeature("Unsupported tool_choice")
            choice = {"type": "function", "function": {"name": _alias(choice.get("namespace"), choice["name"])}}
        payload["tool_choice"] = choice
    for key in ("temperature", "parallel_tool_calls"):
        if key in request:
            payload[key] = request[key]
    response_format = _response_format(request)
    if response_format:
        payload["response_format"] = response_format
    if sarvam and model in SARVAM_REASONING_MODELS:
        effort, disable_thinking = _reasoning_effort(request, model)
        if effort:
            payload["reasoning_effort"] = effort
        if disable_thinking:
            payload["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        elif response_format and model == "deepseekv4.1-flash":
            # Sarvam documents that structured output with DeepSeek reasoning
            # enabled may return content=null. Disable only for this request.
            payload["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    max_output_tokens = request.get("max_output_tokens")
    if max_output_tokens is None:
        max_output_tokens = default_max_output_tokens
    if max_output_tokens is not None:
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise UnsupportedWireFeature("max_output_tokens must be a positive integer")
        payload["max_tokens"] = max_output_tokens
    return payload, mapping


def response_tool(call: dict, mapping: dict[str, dict]) -> dict:
    function = call["function"]
    spec = mapping.get(function["name"])
    if spec is None:
        raise UnsupportedWireFeature("Provider returned an unregistered tool")
    item = {"type": "function_call" if spec["type"] == "function" else "custom_tool_call", "id": "fc_" + uuid.uuid4().hex, "call_id": call["id"], "name": spec["name"], "status": "completed"}
    if spec["namespace"]:
        item["namespace"] = spec["namespace"]
    arguments = str(function.get("arguments", ""))
    parsed = json.loads(arguments)
    if not isinstance(parsed, dict):
        raise UnsupportedWireFeature("Tool arguments must be an object")
    if spec["type"] == "custom":
        if set(parsed) != {"input"} or not isinstance(parsed["input"], str):
            raise UnsupportedWireFeature("Invalid freeform tool input")
        item["input"] = parsed["input"]
    else:
        item["arguments"] = arguments
    return item


class ResponsesAdapter:
    def __init__(self, endpoint: ChatEndpoint):
        self.endpoint = endpoint
        self.research_guard = ResearchGuard()
        self.token = secrets.token_urlsafe(32)
        from .native_admission import AdmissionClient, provider_admission
        self.client = AdmissionClient(httpx.Client(timeout=httpx.Timeout(300, connect=15), follow_redirects=False, trust_env=False), provider_admission(endpoint))
        self.memory = None
        self.checkpoint_thread_id = None
        self.checkpoint_required = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass  # Never log prompts, provider errors or credentials.

            def do_POST(self):
                stream_started = False
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + owner.token):
                    self._reject_post(401, "Loopback authentication required")
                    return
                if self.path != "/v1/responses":
                    self._reject_post(404, "Unknown adapter route")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 64 * 1024 * 1024:
                        raise ValueError("Invalid transport body size")
                    request = json.loads(self.rfile.read(length))
                    if not isinstance(request, dict) or request.get("stream") is not True:
                        raise ValueError("This adapter requires streamed Responses requests")
                    payload, mapping = translate_request(
                        request,
                        owner.endpoint.model,
                        default_max_output_tokens=owner.endpoint.default_max_output_tokens,
                        sarvam=owner.endpoint.is_sarvam,
                    )
                    thread_id = (request.get("client_metadata") or {}).get("thread_id") or self.headers.get("thread-id")
                    budget_checkpoint = owner.checkpoint_required is not None and owner.checkpoint_required(thread_id)
                    if owner.checkpoint_thread_id is not None and not thread_id:
                        raise UnsupportedWireFeature("Checkpoint serialization requires a native thread identity")
                    is_checkpoint = budget_checkpoint or owner.checkpoint_thread_id is not None and thread_id == owner.checkpoint_thread_id
                    if is_checkpoint:
                        # A final serialization pass, not another work turn.
                        # Clear the mapping too: even an unsolicited tool call
                        # cannot reach the native executor.
                        payload.pop("tools", None); payload.pop("tool_choice", None); mapping.clear()
                        payload["messages"].append({"role": "system", "content": "This is the final allowed inference request for this bounded cycle. Return only the structured progress checkpoint specified by the goal instructions, including coverage links, from observed history. Tools are unavailable. Report missing work honestly; use continue or blocked rather than falsely declaring readiness. Do not invent completed actions or evidence."})
                    memory_ticket = None
                    if owner.memory is not None and not is_checkpoint:
                        try:
                            memory_ticket = owner.memory.augment(request, payload, self.headers, provider_secret=owner.endpoint.api_key)
                        except Exception:
                            # Memory cannot take down the native execution path.
                            # SDK errors/prompt contents are never exposed here.
                            pass
                    owner.research_guard.admit(request, payload, mapping, self.headers)
                except ResearchLimitError as exc:
                    self._json_error(400, str(exc))
                    return
                except (ValueError, KeyError, TypeError) as exc:
                    self._json_error(400, str(exc))
                    return
                try:
                    with peer_context(self.connection), owner.client.stream("POST", owner.endpoint.url, headers=owner.endpoint.headers(), json=payload) as upstream:
                        if upstream.status_code != 200:
                            self._json_error(upstream.status_code, provider_status_message(upstream.status_code, owner.endpoint))
                            return
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        stream_started = True
                        self.close_connection = True
                        response = owner._stream(upstream, self, mapping)
                        if owner.memory is not None and not is_checkpoint:
                            try:
                                owner.memory.capture(memory_ticket, response, provider_secret=owner.endpoint.api_key)
                            except Exception:
                                # Preserve a completed answer if the local queue
                                # is unavailable. Never emit a second completion.
                                pass
                except BudgetLimitError:
                    # A native 4xx ends this turn rather than retrying a 502.
                    # Never expose a guard's exception text or private payload.
                    self._json_error(400, "Approved inference budget reached; extend its limits before resuming")
                except AdmissionError as exc:
                    self._json_error(429, str(exc))
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception:
                    # Once streaming has begun, never invent a successful finish.
                    if stream_started:
                        self._event({"type": "response.failed", "response": {"error": {"code": "provider_stream_error", "message": "Provider stream failed or emitted invalid tool arguments"}}})
                    else:
                        self._json_error(502, "Provider connection failed")

            def _reject_post(self, status: int, message: str):
                # Closing a Windows socket with unread POST bytes can reset it
                # before the client receives the 401/404. Drain only small,
                # length-framed bodies, without parsing or forwarding them.
                # Larger/chunked/slow unauthenticated requests remain rejected;
                # they cannot force an unbounded read or reach the provider.
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not self.headers.get("Transfer-Encoding") and 0 < length <= 64 * 1024:
                        self.connection.settimeout(2)
                        self.rfile.read(length)
                except (OSError, ValueError):
                    pass
                self._json_error(status, message)

            def _json_error(self, status: int, message: str):
                data = json.dumps({"error": {"message": message, "type": "invalid_request_error"}}).encode()
                self.close_connection = True
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()

            def _event(self, event: dict):
                self.wfile.write(("data: " + json.dumps(event, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="smara-provider-wire")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def configure_memory(self, workspace):
        from .native_memory import ConversationMemory, settings
        if self.memory is None and settings()["enabled"]:
            self.memory = ConversationMemory(workspace)

    def __exit__(self, *_args):
        if self.memory is not None:
            self.memory.close()
        self.server.shutdown()
        # Stop in-flight provider streams before closing the HTTP server. A
        # disconnected native session must not hold the bootstrap open for the
        # provider's full read timeout.
        self.client.close()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _stream(self, upstream, handler, mapping):
        response = {"id": "resp_" + uuid.uuid4().hex, "object": "response", "created_at": int(time.time()), "status": "in_progress", "model": self.endpoint.model, "output": []}
        handler._event({"type": "response.created", "response": response})
        text = ""
        message_id = "msg_" + uuid.uuid4().hex
        calls: dict[int, dict] = {}
        usage = {}
        finish = None
        saw_done = False
        for line in upstream.iter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                saw_done = True
                break
            chunk = json.loads(raw)
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                delta = choice.get("delta", {})
                # Preserve an actual provider refusal as a visible answer,
                # rather than disguising it as a network/argument failure.
                fragment = delta.get("refusal") or delta.get("content")
                if fragment:
                    if not text:
                        handler._event({"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": message_id, "role": "assistant", "content": [], "status": "in_progress"}})
                    text += fragment
                    handler._event({"type": "response.output_text.delta", "item_id": message_id, "output_index": 0, "content_index": 0, "delta": fragment})
                for call in delta.get("tool_calls", []):
                    current = calls.setdefault(call["index"], {"id": "", "function": {"name": "", "arguments": ""}})
                    if call.get("id"):
                        current["id"] = call["id"]
                    for key in ("name", "arguments"):
                        current["function"][key] += call.get("function", {}).get(key, "") or ""
                finish = choice.get("finish_reason") or finish
        if not saw_done or finish not in {"stop", "tool_calls", "length", "content_filter"}:
            raise UnsupportedWireFeature("Provider stream ended without a valid terminal event")
        if finish == "content_filter":
            response["status"] = "incomplete"
            response["incomplete_details"] = {"reason": "content_filter"}
            handler._event({"type": "response.incomplete", "response": response})
            return response
        if finish == "length":
            # Incomplete tool arguments must never reach the executor.
            response["status"] = "incomplete"
            response["incomplete_details"] = {"reason": "max_output_tokens"}
            handler._event({"type": "response.incomplete", "response": response})
            return response
        items = []
        if text:
            items.append({"type": "message", "id": message_id, "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]})
        for index in sorted(calls):
            if not calls[index]["id"]:
                raise UnsupportedWireFeature("Provider tool call has no identity")
            items.append(response_tool(calls[index], mapping))
        if not items:
            raise UnsupportedWireFeature("Provider returned no message or tool call")
        for index, item in enumerate(items):
            if item["type"] != "message":
                field = "input" if item["type"] == "custom_tool_call" else "arguments"
                pending = dict(item, status="in_progress")
                pending[field] = ""
                handler._event({"type": "response.output_item.added", "output_index": index, "item": pending})
                kind = "response.custom_tool_call_input.delta" if field == "input" else "response.function_call_arguments.delta"
                handler._event({"type": kind, "item_id": item["id"], "call_id": item["call_id"], "output_index": index, "delta": item[field]})
            handler._event({"type": "response.output_item.done", "output_index": index, "item": item})
        # Missing provider usage is unknown, not a claim of zero cost/tokens.
        reported_usage = None
        if all(isinstance(usage.get(key), int) and usage[key] >= 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")):
            reported_usage = {"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"], "total_tokens": usage["total_tokens"]}
        response.update(status="completed", output=items, usage=reported_usage)
        handler._event({"type": "response.completed", "response": response})
        return response
