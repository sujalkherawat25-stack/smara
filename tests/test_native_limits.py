"""Offline regression tests for runaway research; no provider billing."""
from concurrent.futures import ThreadPoolExecutor
import copy
import pytest
from smara.native_limits import ResearchGuard, ResearchLimitError


def request(calls=(), *, thread="parent", turn="turn", root=None):
    metadata = {"thread_id": thread, "turn_id": turn}
    if root:
        metadata["root_turn_id"] = root
    return {"client_metadata": metadata, "input": [
        {"role": "user", "content": "synthetic research"},
        *[{"type": "function_call", "call_id": str(index), "name": name, "arguments": arguments}
          for index, (name, arguments) in enumerate(calls)]
    ]}


def admit(guard, req):
    payload = {"messages": [], "tools": [{"type": "function"}], "tool_choice": "auto"}
    mapping = {"fetch_url": {"name": "fetch_url"}}
    reason = guard.admit(req, payload, mapping)
    return reason, payload, mapping


def test_exact_failed_pdf_loop_gets_one_tool_free_synthesis_then_stops():
    guard = ResearchGuard()
    calls = [("fetch_url", '{"url":"https://example.com/rules.pdf"}')] * 3
    reason, payload, mapping = admit(guard, request(calls))
    assert "Repeated" in reason
    assert "tools" not in payload and "tool_choice" not in payload
    assert not mapping
    assert "partial" in payload["messages"][-1]["content"]
    with pytest.raises(ResearchLimitError, match="already used"):
        admit(guard, request(calls))


def test_history_replay_is_not_double_charged_and_arguments_are_canonical():
    guard = ResearchGuard(repeat_limit=2)
    first = request([("mcp__smara_readers__fetch_url", '{"url":"https://example.com/x","x":1}')])
    assert admit(guard, first)[0] is None
    assert admit(guard, first)[0] is None
    second = copy.deepcopy(first)
    second["input"].append({"type": "function_call", "call_id": "new", "name": "mcp__smara_readers__fetch_url", "arguments": '{"x":1, "url":"https://example.com/x"}'})
    assert "Repeated" in admit(guard, second)[0]


def test_source_offsets_are_distinct_and_coding_retries_are_untouched():
    guard = ResearchGuard(worker_calls=3)
    calls = [("source_page", '{"source_id":"synthetic","offset":%d}' % offset) for offset in (0, 8000)]
    assert admit(guard, request(calls))[0] is None
    assert admit(guard, request([("exec_command", '{"cmd":"pytest"}')] * 400, turn="coding"))[0] is None


def test_worker_tree_shares_budget_but_unrelated_turns_do_not():
    guard = ResearchGuard(shared_calls=3)
    assert admit(guard, request([("web_search", '{"query":"a"}')] * 2, thread="a", turn="a-turn", root="root"))[0] is None
    assert "budget" in admit(guard, request([("web_search", '{"query":"b"}')], thread="b", turn="b-turn", root="root"))[0]
    assert "budget" in admit(guard, request([], thread="parent", turn="root"))[0]
    assert admit(guard, request([("web_search", '{"query":"b"}')], thread="b", turn="next"))[0] is None


def test_compaction_cannot_reset_budget_and_time_expiry_allows_synthesis():
    now = [0]
    guard = ResearchGuard(clock=lambda: now[0], worker_seconds=10)
    req = request([("web_search", '{"query":"a"}')])
    assert admit(guard, req)[0] is None
    now[0] = 11
    assert "time budget" in admit(guard, request())[0]


def test_concurrent_synthesis_is_admitted_once():
    guard = ResearchGuard(worker_calls=1)
    req = request([("web_search", '{"query":"a"}')])
    def attempt(_):
        try:
            return bool(admit(guard, req)[0])
        except ResearchLimitError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(8))) == 1


def test_shared_deadline_and_request_ceiling_stop_unique_query_churn():
    guard = ResearchGuard(worker_requests=2)
    req = request([("web_search", '{"query":"a"}')])
    assert admit(guard, req)[0] is None
    assert "inference-request" in admit(guard, req)[0]
    now = [0]
    guard = ResearchGuard(clock=lambda: now[0], shared_seconds=5, worker_seconds=999)
    assert admit(guard, request([("web_search", '{"query":"a"}')], root="root"))[0] is None
    now[0] = 6
    assert "time budget" in admit(guard, request([], thread="child", turn="child", root="root"))[0]


def test_many_plain_coding_turns_do_not_fill_research_state():
    guard = ResearchGuard()
    for index in range(300):
        assert admit(guard, request([], turn=str(index)))[0] is None
    assert not guard.windows and not guard.groups
