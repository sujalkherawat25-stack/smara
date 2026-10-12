import json
import pytest
from smara.native_outcomes import review, test_command as is_test_command, validate_acceptance


def report(tmp_path, kind, items, **cp):
    return review({"workspace": str(tmp_path), "task_kind": kind}, {"outputs": [], **cp}, {"turns": [{"items": items}]})


def command(exit_code=0, command="python -m pytest"):
    return {"type": "commandExecution", "command": command, "exitCode": exit_code, "status": "completed"}


def source(url="https://docs.python.org/3/library/json.html", tool="fetch_url", status="completed"):
    return {"type": "mcpToolCall", "server": "smara_readers", "tool": tool, "status": status,
        "arguments": {"url": url}, "result": {"content": [{"type": "text", "text": json.dumps({"url": url, "text": "Decode a JSON document from a string and return the Python representation."})}]}}


@pytest.mark.parametrize("code,passed", [(0, True), (1, False), (None, False), (False, False)])
def test_only_actual_native_exit_codes_count(tmp_path, code, passed):
    assert report(tmp_path, "coding", [command(code)])["passed"] is passed


def test_latest_test_and_post_edit_requirement(tmp_path):
    assert report(tmp_path, "coding", [command(1), command(0)])["passed"]
    assert not report(tmp_path, "coding", [command(), {"type": "fileChange", "status": "completed"}])["passed"]
    assert not report(tmp_path, "coding", [{"type": "agentMessage", "text": "tests passed"}])["passed"]


def test_baseline_failure_does_not_block_a_different_post_edit_test_command(tmp_path):
    edit = {"type": "fileChange", "status": "completed"}
    assert report(tmp_path, "coding", [command(1, "pytest"), edit, command(0, "python -m pytest -q")])["passed"]
    assert not report(tmp_path, "coding", [edit, command(1, "pytest"), command(0, "python -m pytest -q")])["passed"]


@pytest.mark.parametrize("text", ["pytest; echo success", "pytest || true", "echo pytest", "python -c 'pytest()'", "npm test && echo done", "pytest --collect-only", "cargo test --no-run"])
def test_masking_commands_do_not_count(text):
    assert not is_test_command(text)


def test_powershell_quoted_python_invocation_is_not_a_compound_command():
    assert is_test_command('& "C:\\Program Files\\Python\\python.exe" -m pytest -q')
    assert not is_test_command('& "C:\\Python\\python.exe" -m pytest; echo passed')
    assert not is_test_command('& "C:\\Python\\python.exe" -m pytest &')


def test_research_requires_read_pages_and_matching_quotes(tmp_path):
    a, b = source(), source("https://docs.python.org/3/tutorial/inputoutput.html")
    claims = [{"claim": "JSON decoding returns Python data", "url": a["arguments"]["url"], "quote": "Decode a JSON document from a string"}]
    assert report(tmp_path, "research", [a, b], claims=claims)["passed"]
    assert not report(tmp_path, "research", [source(tool="web_search"), b], claims=claims)["passed"]
    claims[0]["quote"] = "A made up supporting quote not in the document"
    assert not report(tmp_path, "research", [a, b], claims=claims)["passed"]


def test_mixed_needs_both_and_failed_reads_are_visible(tmp_path):
    result = report(tmp_path, "mixed", [command(), source(status="failed")])
    assert not result["passed"] and result["failed_pages"]


def test_changed_output_invalidates_acceptance(tmp_path):
    path = tmp_path / "answer.txt"
    path.write_text("Synthetic output")
    result = report(tmp_path, "coding", [command()], outputs=[path.name])
    goal = {"spec": {"workspace": str(tmp_path), "task_kind": "coding"}, "checkpoint": {"outcome": result}}
    validate_acceptance(goal)
    path.write_text("Changed after check")
    with pytest.raises(ValueError, match="changed"):
        validate_acceptance(goal)


def test_sensitive_outputs_and_missing_clock(tmp_path):
    (tmp_path / ".env.local").write_text("synthetic")
    with pytest.raises(ValueError):
        report(tmp_path, "general", [], outputs=[".env.local"])
    result = review({"workspace": str(tmp_path), "task_kind": "research", "time_sensitive": True}, {"outputs": []}, {"turns": []})
    assert any(c["name"] == "Current date" and not c["passed"] for c in result["checks"])


def test_prior_cycle_clock_cannot_satisfy_current_date(tmp_path):
    clock = source(tool="current_time")
    result = review({"workspace": str(tmp_path), "task_kind": "research", "time_sensitive": True},
        {"outputs": []}, {"turns": [{"items": [clock]}, {"items": []}]})
    assert any(c["name"] == "Current date" and not c["passed"] for c in result["checks"])
