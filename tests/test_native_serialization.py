"""Local adapter protocol checks: formatting cannot execute another tool."""
import json
from types import SimpleNamespace
import httpx
from smara.native_provider import ResponsesAdapter, ChatEndpoint


def test_final_formatting_has_no_tools_or_memory_side_effects():
    sent, memory = [], []
    def transport(request):
        sent.append(json.loads(request.content))
        # Deliberately noncompliant provider: unsolicited tool must not become
        # a usable native call after the formatting mapping was cleared.
        chunk = {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "synthetic-call", "function": {"name": "exec_command", "arguments": '{"cmd":"echo forbidden"}'}}]}, "finish_reason": "tool_calls"}]}
        return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1:1", "synthetic", "")) as adapter:
        adapter.client.close()
        adapter.client = httpx.Client(transport=httpx.MockTransport(transport))
        adapter.checkpoint_thread_id = "synthetic-root"
        adapter.memory = SimpleNamespace(augment=lambda *_args, **_kwargs: memory.append("augment"), capture=lambda *_args, **_kwargs: memory.append("capture"), close=lambda: None)
        request = {"stream": True, "input": "Serialize observed work only", "client_metadata": {"thread_id": "synthetic-root"},
                   "tools": [{"type": "function", "name": "exec_command", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}]}
        response = httpx.post(adapter.base_url + "/responses", headers={"Authorization": "Bearer " + adapter.token}, json=request)
    assert len(sent) == 1 and "tools" not in sent[0] and "tool_choice" not in sent[0]
    assert not memory
    assert "response.failed" in response.text and '"type": "function_call"' not in response.text


def test_unknown_formatting_thread_fails_before_provider():
    sent = []
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1:1", "synthetic", "")) as adapter:
        adapter.client.close()
        adapter.client = httpx.Client(transport=httpx.MockTransport(lambda request: sent.append(request)))
        adapter.checkpoint_thread_id = "synthetic-root"
        response = httpx.post(adapter.base_url + "/responses", headers={"Authorization": "Bearer " + adapter.token}, json={"stream": True, "input": "Synthetic checkpoint"})
    assert response.status_code == 400 and not sent


def test_last_budget_request_serializes_only_its_root_thread_without_tools():
    sent = []
    def transport(request):
        sent.append(json.loads(request.content))
        chunk = {"choices": [{"delta": {"content": "Synthetic checkpoint"}, "finish_reason": "stop"}]}
        return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")
    with ResponsesAdapter(ChatEndpoint("http://127.0.0.1:1", "synthetic", "")) as adapter:
        adapter.client.close()
        adapter.client = httpx.Client(transport=httpx.MockTransport(transport))
        adapter.checkpoint_required = lambda identity: identity == "root"
        for identity in ("root", "other"):
            response = httpx.post(adapter.base_url + "/responses", headers={"Authorization": "Bearer " + adapter.token},
                json={"stream": True, "input": "Synthetic input", "client_metadata": {"thread_id": identity},
                      "tools": [{"type": "function", "name": "read", "parameters": {"type": "object"}}]})
            assert "response.completed" in response.text
    assert "tools" not in sent[0] and "final allowed inference" in sent[0]["messages"][-1]["content"]
    assert "tools" in sent[1]
