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


class UnsupportedWireFeature(ValueError):
    pass


@dataclass(frozen=True)
class ChatEndpoint:
    base_url: str
    model: str
    api_key: str
    auth_header: str = "authorization"
    context_window: int | None = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid configured model endpoint")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote model endpoints must use HTTPS")
        if parsed.query or parsed.fragment or not self.model.strip():
            raise ValueError("Invalid model endpoint or name")
        if self.auth_header.lower() not in {"authorization", "api-subscription-key"}:
            raise ValueError("Unsupported credential header")
        if self.context_window is not None and (isinstance(self.context_window, bool) or not isinstance(self.context_window, int) or self.context_window < 4096):
            raise ValueError("Invalid configured context window")

    @property
    def effective_context_window(self) -> int | None:
        if self.context_window is not None:
            return self.context_window
        # Published provider capability metadata, not task/prompt routing.
        # https://docs.sarvam.ai/api/getting-started/models/openweight/glm-5-3
        if urlparse(self.base_url).hostname == "api.sarvam.ai" and self.model in {"glm5.3", "deepseekv4-flash"}:
            return 1_048_576
        return None  # Unknown is not a fabricated provider limit.

    @property
    def url(self) -> str:
        base = self.base_url.rstrip("/")
        return base if base.endswith("/chat/completions") else base + "/chat/completions"

    def headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {self.auth_header: ("Bearer " + self.api_key) if self.auth_header.lower() == "authorization" else self.api_key}


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


def translate_request(request: dict[str, Any], model: str) -> tuple[dict[str, Any], dict[str, dict]]:
    if request.get("previous_response_id"):
        raise UnsupportedWireFeature("This adapter requires full input history, not previous_response_id")
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
        functions.append({"type": "function", "function": {"name": alias, "description": description, "parameters": parameters}})

    for spec in request.get("tools", []):
        tool(spec)
    messages = []
    if request.get("instructions"):
        messages.append({"role": "system", "content": request["instructions"]})
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
    if request.get("max_output_tokens"):
        payload["max_tokens"] = request["max_output_tokens"]
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
        self.token = secrets.token_urlsafe(32)
        self.client = httpx.Client(timeout=httpx.Timeout(300, connect=15), follow_redirects=False, trust_env=False)
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
                    payload, mapping = translate_request(request, owner.endpoint.model)
                except (ValueError, KeyError, TypeError) as exc:
                    self._json_error(400, str(exc))
                    return
                try:
                    with owner.client.stream("POST", owner.endpoint.url, headers=owner.endpoint.headers(), json=payload) as upstream:
                        if upstream.status_code != 200:
                            self._json_error(upstream.status_code, "Configured provider rejected inference; its response body is withheld to protect secrets")
                            return
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        stream_started = True
                        self.close_connection = True
                        owner._stream(upstream, self, mapping)
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

    def __exit__(self, *_args):
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
        if not saw_done or finish not in {"stop", "tool_calls", "length"}:
            raise UnsupportedWireFeature("Provider stream ended without a valid terminal event")
        if finish == "length":
            # Incomplete tool arguments must never reach the executor.
            response["status"] = "incomplete"
            response["incomplete_details"] = {"reason": "max_output_tokens"}
            handler._event({"type": "response.incomplete", "response": response})
            return
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
