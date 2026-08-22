import asyncio

from smara.agent_runtime import SmaraAgentRuntime
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


def test_cli_has_only_api_control_commands():
    parser = build_parser()
    assert parser.parse_args(["tasks"]).command == "tasks"
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
