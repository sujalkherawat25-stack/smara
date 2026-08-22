import asyncio

from smara.agent_runtime import SmaraAgentRuntime
from smara import agent_events, llm_errors
import httpx

from smara.cli import _request, build_parser
from smara.syntarus_adapter import SyntarusMemory


class FakeProvider:
    def __init__(self):
        self.system = ""
        self.message = ""

    async def complete(self, *, system: str, message: str) -> str:
        self.system, self.message = system, message
        return "A bounded direct response."


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


class FakeSyntarus:
    async def search(self, query, **kwargs):
        self.query, self.kwargs = query, kwargs
        return {"context": "Sujal prefers concise reports."}

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


def test_runtime_chat_promotes_the_bounded_read_only_tool_loop():
    runtime = SmaraAgentRuntime(ToolProvider())
    turn = asyncio.run(runtime.chat_with_tools(
        account_id="acct_1", workspace_id="work", message="Calculate 2+2"
    ))
    assert turn.message == "The result is 4."
    assert turn.tools_used == 1


def test_cli_has_only_api_control_commands():
    parser = build_parser()
    assert parser.parse_args(["tasks"]).command == "tasks"
    assert parser.parse_args(["tasks", "list"]).tasks_command == "list"
    assert parser.parse_args(["desktop", "pair", "--capability", "local_file_read"]).desktop_command == "pair"
    assert parser.parse_args(["tool", "calculate"]).name == "calculate"
    args = parser.parse_args(["run", "Prepare a report", "--title", "Report"])
    assert args.objective == "Prepare a report"
    assert args.title == "Report"


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


def test_stream_events_do_not_expose_reasoning_or_raw_errors():
    assert '"type": "phase"' in agent_events.phase("retrieve")
    assert '"type": "token"' in agent_events.token("A final answer")
    error = agent_events.error("safe message", kind="timeout")
    assert '"kind": "timeout"' in error
    assert "reasoning" not in error
