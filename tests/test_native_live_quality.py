from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts.run_native_desktop_candidate import candidate_directory, clock_fixture_response
from scripts.run_native_live_workers_browser import BudgetClient, PUBLIC_URL, browser_prompt, coding_prompt, scoped_decision, complete_ledger_usage


def test_desktop_fixtures_preserve_previous_evidence(tmp_path):
    first = candidate_directory(tmp_path, packaged=True, offline=True)
    evidence = first / "stop-report.json"
    evidence.write_text("previous failed report")
    second = candidate_directory(tmp_path, packaged=True, offline=True)
    assert first != second and evidence.read_text() == "previous failed report"
    assert second.is_relative_to(tmp_path / "build")


def test_offline_clock_fixture_requests_only_clock_and_never_grants_it():
    request = {"tools": [{"function": {"name": "mcp__smara_readers__current_time"}},
                         {"function": {"name": "exec_command"}}]}
    delta, finish, rejected = clock_fixture_response(request, 1)
    assert finish == "tool_calls" and not rejected
    assert delta["tool_calls"][0]["function"] == {"name": "mcp__smara_readers__current_time", "arguments": "{}"}
    with pytest.raises(ValueError):
        clock_fixture_response({"tools": []}, 1)
    with pytest.raises(ValueError):
        clock_fixture_response(request, 3)
    assert clock_fixture_response({"messages": [{"role": "tool", "content": "user rejected MCP tool call"}]}, 2)[2]
    assert not clock_fixture_response({"messages": [{"role": "tool", "content": '{"utc":"actual time"}'}]}, 2)[2]


def test_budget_is_shared_and_enforced_before_outbound_calls():
    calls = []
    @contextmanager
    def stream(*args, **kwargs):
        calls.append(kwargs["json"])
        yield object()
    client = BudgetClient(SimpleNamespace(stream=stream), ceiling=2)
    with client.stream("POST", "unused", json={"messages": [], "max_tokens": 9999}):
        with client.stream("POST", "unused", json={"messages": []}):
            assert client.peak_streams == 2
    with pytest.raises(RuntimeError, match="ceiling"):
        with client.stream("POST", "unused", json={"messages": []}):
            pass
    assert len(calls) == 2 and client.requests == 2 and client.active_streams == 0
    assert all(payload["max_tokens"] == 8192 for payload in calls)


def test_money_guard_blocks_without_calling_transport():
    def forbidden(*_args, **_kwargs):
        pytest.fail("Request reached provider after budget ceiling")
    client = BudgetClient(SimpleNamespace(stream=forbidden), rupees=.01)
    with pytest.raises(RuntimeError, match="ceiling"):
        with client.stream("POST", "unused", json={"messages": []}):
            pass
    assert client.requests == 0 and client.reserved_rupees == 0
    assert client.exhausted.is_set()


def test_continuation_carries_the_whole_budget_forward():
    client = BudgetClient(SimpleNamespace(), rupees=300)
    client.carry_forward({"requests": 14, "reserved_inr_estimate": 291.11, "peak_concurrent_response_streams": 3})
    assert client.requests == 14 and client.reserved_rupees > 291.11 and client.peak_streams == 3
    with pytest.raises(ValueError):
        client.carry_forward({"requests": 26, "reserved_inr_estimate": 0})


def test_native_session_checks_budget_event_before_waiting():
    import queue
    import threading
    from scripts.native_session import NativeSession
    session = NativeSession.__new__(NativeSession)
    session.timeout = 60
    session.messages = queue.Queue()
    session.abort_event = threading.Event()
    session.abort_event.set()
    with pytest.raises(RuntimeError, match="budget"):
        session.next()


@pytest.mark.parametrize("done", [False, True])
def test_usage_settlement_requires_complete_stream_and_preserves_lines(done):
    import json
    lines = ['data: ' + json.dumps({"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})]
    if done:
        lines.append('data: [DONE]')
    @contextmanager
    def stream(*_args, **_kwargs):
        yield SimpleNamespace(iter_lines=lambda: iter(lines))
    client = BudgetClient(SimpleNamespace(stream=stream))
    with client.stream("POST", "unused", json={"messages": []}) as response:
        reserved = client.reserved_rupees
        assert list(response.iter_lines()) == lines
    assert (client.reserved_rupees < reserved) == done
    assert client.reported_usage_requests == int(done)


def test_ledger_settlement_rejects_missing_requests_and_duplicate_totals(tmp_path):
    import json
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    record = {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": usage, "total_token_usage": usage}}}
    ledger = sessions / "synthetic.jsonl"
    ledger.write_text(json.dumps(record) + '\n')
    assert complete_ledger_usage(tmp_path, 1) == {"requests": 1, "tokens": 120}
    assert complete_ledger_usage(tmp_path, 2) is None
    ledger.write_text((json.dumps(record) + '\n') * 2)
    assert complete_ledger_usage(tmp_path, 2) is None


def test_prompts_do_not_prescribe_patches_or_answers():
    prompt = coding_prompt("synthetic-python")
    assert "leave ALL tests" in prompt and "worktree:true" in prompt
    assert "apply_patch" not in prompt and "math.ceil" not in prompt
    assert "synthetic-python" in prompt
    assert PUBLIC_URL in browser_prompt() and "returns 3" not in browser_prompt()


@pytest.mark.parametrize("tool,args,allowed", [
    ("browser_open", {"url": PUBLIC_URL}, True),
    ("browser_open", {"url": "https://docs.python.org/other"}, False),
    ("browser_act", {"ref": "1", "action": "click", "observation_id": "a"}, False),
    ("browser_text_page", {"observation_id": "a", "offset": -1}, False),
    ("browser_text_page", {"observation_id": "a", "offset": 8000}, True),
    ("browser_close", {}, True),
])
def test_test_client_grants_only_declared_public_read_scope(tool, args, allowed):
    request = {"method": "mcpServer/elicitation/request", "params": {"serverName": "smara_browser", "mode": "form", "requestedSchema": {"type": "object", "properties": {}}, "message": "Confirm " + tool, "_meta": {"tool_params": args, "codex_approval_kind": "mcp_tool_call"}}}
    result = scoped_decision(request, browser=True)
    assert (result["action"] == "accept") == allowed
    assert scoped_decision(request, browser=False)["action"] == "decline"
    assert result["_meta"] is None


def test_unexpected_escalations_are_denied():
    assert scoped_decision({"method": "item/commandExecution/requestApproval"}) == {"decision": "decline"}
    assert scoped_decision({"method": "item/permissions/requestApproval"})["permissions"] == {}
