"""Acceptance-runner mechanics only; no model or native outcome is mocked."""
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from scripts.run_native_coding_acceptance import BoundedCodingClient, cleanup_scratch, coding_prompt, parse_cli_output
from scripts.run_native_parallel_acceptance import ConcurrentProbeClient


def test_coding_request_bound_does_not_fabricate_output():
    observed = []
    sentinel = object()
    client = SimpleNamespace(stream=lambda *args, **kwargs: observed.append(kwargs) or sentinel)
    bounded = BoundedCodingClient(client, ceiling=1)
    payload = {"messages": [{"role": "tool", "tool_call_id": "fixture", "content": "actual fixture text"}], "max_tokens": 10000}
    assert bounded.stream("POST", "https://example.test", json=payload) is sentinel
    assert observed[0]["json"]["messages"] == payload["messages"]
    assert observed[0]["json"]["max_tokens"] == 2048
    assert bounded.history_replays == 1
    with pytest.raises(RuntimeError, match="ceiling"):
        bounded.stream("POST", "https://example.test", json=payload)
    assert len(observed) == 1


def test_native_cli_failure_is_not_a_completed_turn():
    result = SimpleNamespace(stdout='{"type":"turn.failed","error":{"message":"failure"}}\n')
    items, completed, failures = parse_cli_output(result)
    assert items == [] and not completed and len(failures) == 1


def test_task_does_not_prescribe_model_tool_calls_or_patch():
    prompt = coding_prompt("synthetic-python")
    assert "Do not change the tests" in prompt
    assert "synthetic-python" in prompt
    assert "apply_patch" not in prompt and "exec_command" not in prompt
    assert "min(value" not in prompt and "max(lower" not in prompt


def test_cleanup_refuses_non_scratch_targets(tmp_path):
    with pytest.raises(ValueError, match="Refusing cleanup"):
        cleanup_scratch(tmp_path, tmp_path)


def test_cleanup_handles_only_owned_readonly_fixture_files(tmp_path):
    import stat
    scratch = tmp_path / "native-coding-readonly"
    scratch.mkdir()
    protected = scratch / "synthetic-git-object"
    protected.write_text("synthetic data")
    protected.chmod(stat.S_IREAD)
    assert cleanup_scratch(scratch, tmp_path)["cleanup"] == "removed"
    assert not scratch.exists()


def test_parallel_probe_counts_real_context_lifetimes():
    @contextmanager
    def response(*_args, **_kwargs):
        yield object()
    bounded = ConcurrentProbeClient(SimpleNamespace(stream=response))
    payload = {"messages": []}
    with bounded.stream("POST", "https://example.test", json=dict(payload)):
        with bounded.stream("POST", "https://example.test", json=dict(payload)):
            assert bounded.active_streams == 2
            assert bounded.peak_concurrent_streams == 2
        assert bounded.active_streams == 1
    assert bounded.active_streams == 0 and bounded.requests == 2
