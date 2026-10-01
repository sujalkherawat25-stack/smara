"""Regression checks for honest completion and terminal test receipts."""
import pytest

from smara.completion_quality import final_answer_reports_unresolved_work
from smara.harness import verification_scope_for_command


@pytest.mark.parametrize("command", [
    "python -m unittest discover -v",
    "python3.12 -m pytest -q",
    "pytest tests/test_example.py",
    "& 'C:\\Program Files\\Python\\python.exe' -m pytest -q",
    '& "C:\\Program Files\\Python\\python.exe" -m unittest discover',
    "./.venv/bin/python -m pytest -q",
    "npm test", "cargo test", "go test ./...",
])
def test_recognizes_test_runners_including_quoted_python_paths(command):
    assert verification_scope_for_command(command) == "focused"


@pytest.mark.parametrize("command", [
    "echo pytest", "Write-Output 'pytest passed'", "python -c 'print(\"pytest\")'",
    "pytest -q; echo passed", "pytest -q || true", "pytest -q && echo passed",
    "python -m npm test", "python -m py_compile app.py", "", "echo test",
])
def test_shell_text_and_masked_exit_codes_are_not_test_receipts(command):
    assert verification_scope_for_command(command) == "none"


@pytest.mark.parametrize("answer", [
    "The feature was left unimplemented.", "The work remains unfinished.",
    "I could not complete the task.", "I need the approved policy before continuing.",
    "Missing requirements prevent completion.",
])
def test_explicit_unfinished_work_requires_attention(answer):
    assert final_answer_reports_unresolved_work(answer)


@pytest.mark.parametrize("answer", [
    "Implemented the feature and verified the result.",
    "Added validation for missing input and required policy errors.",
    "The documentation explains why users need information before choosing a policy.",
])
def test_completed_explanations_of_input_validation_are_not_blocked(answer):
    assert not final_answer_reports_unresolved_work(answer)
