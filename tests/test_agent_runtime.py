import asyncio

from smara.agent_runtime import OpenAICompatibleProvider, SmaraAgentRuntime
from smara import agent_events, llm_errors
import httpx

from smara.cli import _request, build_parser
from smara.syntarus_adapter import SyntarusMemory


def test_provider_uses_sarvam_subscription_header(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return FakeResponse()

    monkeypatch.setattr("smara.agent_runtime.httpx.AsyncClient", lambda **_kwargs: FakeClient())

    async def exercise():
        return await OpenAICompatibleProvider(
            base_url="https://api.sarvam.ai/v2",
            api_key="sarvam-secret",
            model="glm5.2",
            auth_header="api-subscription-key",
        ).complete(system="system", message="hello")

    assert asyncio.run(exercise()) == "ok"
    assert captured["url"] == "https://api.sarvam.ai/v2/chat/completions"
    assert captured["headers"] == {"api-subscription-key": "sarvam-secret"}


def test_provider_retries_one_transient_non_stream_failure(monkeypatch):
    statuses = [503, 200]

    class FakeResponse:
        headers = {}

        def __init__(self, status_code):
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
                response = httpx.Response(self.status_code, request=request)
                raise httpx.HTTPStatusError("provider unavailable", request=request, response=response)

        def json(self):
            return {"choices": [{"message": {"content": "recovered"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            return FakeResponse(statuses.pop(0))

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr("smara.agent_runtime.httpx.AsyncClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr("smara.agent_runtime.asyncio.sleep", no_wait)

    async def exercise():
        return await OpenAICompatibleProvider(
            base_url="https://llm.example/v1", api_key="key", model="model"
        ).complete(system="system", message="hello")

    assert asyncio.run(exercise()) == "recovered"
    assert statuses == []


class FakeProvider:
    def __init__(self):
        self.system = ""
        self.message = ""

    async def complete(self, *, system: str, message: str) -> str:
        self.system, self.message = system, message
        return "A bounded direct response."


class CompleteOnlyProvider(FakeProvider):
    """Provider shape used by an adapter that does not expose streaming."""

    stream_complete = None


class FailingDirectStreamProvider(FakeProvider):
    async def stream_complete(self, *, system: str, message: str):
        if False:
            yield ""
        raise RuntimeError("temporary stream failure")


class ToolProvider:
    _base_url = "https://llm.example/v1"
    _api_key = "test-key"
    _model = "small-model"

    def __init__(self):
        self.calls = 0

    async def complete(self, *, system: str, message: str) -> str:
        self.calls += 1
        if self.calls == 1:
            return '{"action":"tool","name":"calculate","arguments":{"expression":"2+2"}}'
        return '{"action":"final","answer":"The result is 4."}'


class IntegrationToolProvider:
    _base_url = "https://llm.example/v1"
    _api_key = "test-key"
    _model = "small-model"

    def __init__(self):
        self.calls = 0

    async def complete(self, *, system: str, message: str) -> str:
        self.calls += 1
        if self.calls == 1:
            return '{"action":"tool","name":"integration.gmail.search","arguments":{"query":"from:alice@example.com","limit":3}}'
        return '{"action":"final","answer":"I found the connected messages."}'


class FakeSyntarus:
    async def search(self, query, **kwargs):
        self.query, self.kwargs = query, kwargs
        return {"context": "Sujal prefers concise reports."}

    async def add(self, **kwargs):
        return {"ok": True}


class LegacySyntarus:
    async def search(self, query, *, user_id, top_k=10):
        return {"context": f"legacy context for {user_id}"}

    async def add(self, **kwargs):
        return {"ok": True}


def test_runtime_reuses_syntarus_only_through_memory_port():
    provider, sdk = FakeProvider(), FakeSyntarus()
    runtime = SmaraAgentRuntime(provider, SyntarusMemory(sdk))
    turn = asyncio.run(runtime.chat(
        account_id="acct_1", workspace_id="work", message="What style should my report use?"
    ))
    assert turn.message == "A bounded direct response."
    assert turn.memory_used is True
    assert sdk.kwargs["user_id"] == "acct_1"
    assert sdk.kwargs["filters"]["workspace_id"] == "work"
    assert "Sujal prefers concise reports." in provider.system
    assert "external action" in provider.system


def test_runtime_falls_back_for_sdk_without_metadata_filters():
    runtime = SmaraAgentRuntime(FakeProvider(), SyntarusMemory(LegacySyntarus()))
    turn = asyncio.run(runtime.chat(
        account_id="acct_legacy", workspace_id="work", message="Recall my context"
    ))
    assert turn.memory_used is True
    assert "legacy context" in turn.message or turn.message == "A bounded direct response."


def test_runtime_chat_promotes_the_bounded_read_only_tool_loop():
    runtime = SmaraAgentRuntime(ToolProvider())
    turn = asyncio.run(runtime.chat_with_tools(
        account_id="acct_1", workspace_id="work", message="Calculate 2+2"
    ))
    assert turn.message == "The result is 4."
    assert turn.tools_used == 1


def test_deterministic_deep_research_runs_writer_pass_and_preserves_citations(monkeypatch):
    from smara.tool_registry import ToolResult

    class Provider:
        _model = "writer"
        calls = []

        async def complete(self, *, system, message):
            self.calls.append((system, message))
            assert "Labelled research evidence" in message
            return "A sourced answer [1]."

    class Registry:
        async def invoke(self, name, arguments, context):
            assert name == "research.deep"
            return ToolResult(
                True,
                "[RESEARCH_CONTEXT]\n[1] Official source\nURL: https://example.com\nEVIDENCE: verified fact",
                citations=["https://example.com"],
                meta={"queries": ["primary", "independent"], "sources": 1, "fetched": 1, "failed": 0},
            )

    registry = Registry()

    class RegistryFactory:
        def restrict(self, _names):
            return registry

    monkeypatch.setattr("smara.agent_runtime.default_tool_registry", lambda *_args, **_kwargs: RegistryFactory())
    events = []
    provider = Provider()
    turn = asyncio.run(SmaraAgentRuntime(provider).chat_with_tools(
        account_id="acct_1",
        workspace_id="default",
        message="Give me a detailed analysis with citations",
        event_hook=lambda name, payload: events.append((name, payload)),
    ))
    assert turn.message == "A sourced answer [1]."
    assert turn.tools_used == 1
    completed = [payload for name, payload in events if name == "agent.tool_completed"]
    assert completed and completed[0]["citations"] == ["https://example.com"]
    assert any(
        name == "agent.status" and payload.get("label") == "Researching multiple sources"
        for name, payload in events
    )
    assert any(name == "agent.phase" and payload.get("phase") == "answer" for name, payload in events)


def test_targeted_research_expands_short_draft_before_streaming(monkeypatch):
    from smara.tool_registry import ToolResult

    class Provider:
        _model = "writer"

        def __init__(self):
            self.calls = []

        async def complete(self, *, system, message):
            self.calls.append(message)
            if len(self.calls) == 1:
                return "Short sourced draft [1]."
            return "The evidence confirms this documented finding [1]. " * 180

    class Registry:
        async def invoke(self, name, arguments, context):
            return ToolResult(
                True,
                "[RESEARCH_CONTEXT]\n[1] Official source\nURL: https://example.com\nEVIDENCE: verified fact",
                citations=["https://example.com"],
                meta={"queries": ["primary"], "sources": 1, "fetched": 1, "failed": 0},
            )

    class RegistryFactory:
        def restrict(self, _names):
            return Registry()

    monkeypatch.setattr("smara.agent_runtime.default_tool_registry", lambda *_args, **_kwargs: RegistryFactory())
    streamed: list[str] = []
    provider = Provider()
    turn = asyncio.run(SmaraAgentRuntime(provider).chat_with_tools(
        account_id="acct_1",
        workspace_id="default",
        message="Give me a detailed analysis with at least 1200 words and citations",
        token_hook=streamed.append,
    ))
    assert len(provider.calls) == 2
    assert len(turn.message.split()) >= 1200
    assert streamed and "Short sourced draft" not in "".join(streamed)


def test_runtime_passes_connected_integration_runner_to_tool_selection():
    calls = []

    async def runner(provider, action, payload):
        calls.append((provider, action, payload))
        return "Gmail search returned 2 message references."

    runtime = SmaraAgentRuntime(IntegrationToolProvider())
    turn = asyncio.run(runtime.chat_with_tools(
        account_id="acct_1",
        workspace_id="work",
        message="Search my Gmail for Alice",
        integration_runner=runner,
    ))
    assert turn.message == "I found the connected messages."
    assert turn.tools_used == 1
    assert calls == [("gmail", "gmail.search", {"query": "from:alice@example.com", "limit": 3})]


def test_runtime_triage_short_circuits_greetings_without_planner_round_trip():
    provider = FakeProvider()
    events = []
    runtime = SmaraAgentRuntime(provider)
    turn = asyncio.run(runtime.chat_with_tools(
        account_id="acct_1", workspace_id="work", message="hi",
        event_hook=lambda name, payload: events.append((name, payload)),
    ))
    assert turn.message == "A bounded direct response."
    assert events == [
        ("agent.phase", {"phase": "triage", "intent": "chitchat", "complexity": 1}),
        ("agent.phase", {"phase": "retrieve"}),
        ("agent.phase", {"phase": "answer"}),
    ]


def test_direct_sse_fallback_emits_answer_when_provider_has_no_stream():
    emitted = []
    runtime = SmaraAgentRuntime(CompleteOnlyProvider())
    turn = asyncio.run(runtime.chat_with_tools(
        account_id="acct_1", workspace_id="work", message="hello",
        token_hook=emitted.append,
    ))
    assert turn.message == "A bounded direct response."
    assert emitted == ["A bounded direct response."]


def test_direct_sse_falls_back_when_stream_fails_before_first_token():
    emitted = []
    runtime = SmaraAgentRuntime(FailingDirectStreamProvider())
    turn = asyncio.run(runtime.chat_with_tools(
        account_id="acct_1", workspace_id="work", message="hello",
        token_hook=emitted.append,
    ))
    assert turn.message == "A bounded direct response."
    assert emitted == ["A bounded direct response."]


def test_runtime_puts_attachment_text_in_explicit_user_turn():
    provider = FakeProvider()
    runtime = SmaraAgentRuntime(provider)
    asyncio.run(runtime.chat_with_tools(
        account_id="acct_1", workspace_id="work", message="Repeat the file text",
        attachment_context="Attachment: notes.txt\nUNIQUE_ATTACHMENT_PHRASE",
    ))
    assert "UNIQUE_ATTACHMENT_PHRASE" in provider.message


def test_runtime_emits_memento_style_phases_for_tool_turn():
    events = []
    runtime = SmaraAgentRuntime(ToolProvider())
    asyncio.run(runtime.chat_with_tools(
        account_id="acct_1", workspace_id="work", message="calculate 2+2",
        event_hook=lambda name, payload: events.append((name, payload)),
    ))
    assert [payload["phase"] for name, payload in events if name == "agent.phase"] == [
        "triage", "retrieve", "reason_act",
    ]


def test_cli_has_only_api_control_commands():
    parser = build_parser()
    assert parser.parse_args(["tasks"]).command == "tasks"
    assert parser.parse_args(["tasks", "list"]).tasks_command == "list"
    assert parser.parse_args(["desktop", "pair", "--capability", "local_file_read"]).desktop_command == "pair"
    assert parser.parse_args(["desktop", "revoke", "desktop_1"]).desktop_command == "revoke"
    assert parser.parse_args(["tool", "calculate"]).name == "calculate"
    args = parser.parse_args(["run", "Prepare a report", "--title", "Report"])
    assert args.objective == "Prepare a report"
    assert args.title == "Report"
    assert build_parser().parse_args(["login"]).code is None
    assert build_parser().parse_args(["login", "smara_legacy_code"]).code == "smara_legacy_code"
    assert build_parser().parse_args(["approvals"]).command == "approvals"
    assert build_parser().parse_args(["devices"]).device_command is None
    assert build_parser().parse_args(["devices", "revoke", "cli_1234567890abcdef"]).device_id == "cli_1234567890abcdef"


def test_cli_request_is_a_thin_http_client():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"], seen["path"] = request.method, request.url.path
        return httpx.Response(201, json={"id": "task_1"})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://smara.test") as client:
        assert _request(client, "POST", "/v1/tasks", json={"title": "Test"}) == {"id": "task_1"}
    assert seen == {"method": "POST", "path": "/v1/tasks"}


def test_cli_token_storage_can_be_saved_and_removed(tmp_path, monkeypatch):
    from smara import cli
    token_file = tmp_path / "token.json"
    monkeypatch.setenv("SMARA_TOKEN_FILE", str(token_file))
    cli._save_token({"access_token": "token-value", "expires_in": 60})
    assert cli._load_token() == "token-value"
    cli._clear_token()
    assert cli._load_token() == ""


def test_provider_errors_have_stable_safe_client_contract():
    error = type("ProviderError", (Exception,), {"status_code": 429})("rate limit reached")
    kind, message = llm_errors.describe(error, provider="Test Provider")
    assert kind == "rate_limit"
    assert "Test Provider" in message
    assert "rate limit reached" not in message
    kind, message = llm_errors.describe(RuntimeError("No Smara chat provider is configured."))
    assert kind == "not_configured"
    assert "not configured" in message
    kind, message = llm_errors.describe(RuntimeError("Smara agent model provider is not configured."))
    assert kind == "not_configured"
    assert "not configured" in message


def test_sarvam_beta_endpoint_is_reported_as_model_unavailable():
    request = httpx.Request("POST", "https://api.sarvam.ai/v2/chat/completions")
    response = httpx.Response(400, request=request, text='{"error":{"message":"This endpoint is currently in beta and not available."}}')
    error = httpx.HTTPStatusError("Client error", request=request, response=response)
    kind, _ = llm_errors.describe(error, provider="Sarvam")
    assert kind == "model_unavailable"


def test_httpx_status_and_disabled_key_are_classified_without_leaking_detail():
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx.Response(403, request=request)
    error = httpx.HTTPStatusError("API key secret-value is disabled", request=request, response=response)
    kind, message = llm_errors.describe(error, provider="Test Provider")
    assert kind == "invalid_key"
    assert "rejected" in message
    assert "secret-value" not in message


def test_stream_events_do_not_expose_reasoning_or_raw_errors():
    assert '"type": "phase"' in agent_events.phase("retrieve")
    assert '"type": "token"' in agent_events.token("A final answer")
    error = agent_events.error("safe message", kind="timeout")
    assert '"kind": "timeout"' in error
    assert "reasoning" not in error
