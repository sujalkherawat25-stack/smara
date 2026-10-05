"""Provider routing must not depend on installed third-party agent CLIs."""
from pathlib import Path
import sys
from types import ModuleType

import pytest
import smara.desktop_executor as executor


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "_load_local_state", lambda _: {
        "allowed_roots": [str(tmp_path)], "approval_mode": "ask", "capabilities": ["local_file_read"]})
    trap = ModuleType("smara.codex_harness")
    def unexpected(*args, **kwargs):
        raise AssertionError("The external Codex CLI must never be consulted")
    trap.is_codex_available = unexpected
    trap.run_codex_turn = unexpected
    monkeypatch.setitem(sys.modules, trap.__name__, trap)
    return tmp_path / "desktop.json"


def test_desktop_keeps_selected_provider_and_approval_mode(state, monkeypatch):
    calls = []
    monkeypatch.setattr(executor, "run_shared_local_turn", lambda **kwargs: calls.append(kwargs) or {
        "status": "needs_input", "completed": False, "answer": "Not completed"})
    result = executor._run_shared_local_agent_turn({"prompt": "hello", "model": {
        "base_url": "https://api.sarvam.ai/v2", "model": "glm5.3", "api_key": "test-only"}}, state)
    assert calls[0]["config"].model == "glm5.3"
    assert calls[0]["config"].base_url == "https://api.sarvam.ai/v2"
    assert calls[0]["approval_mode"] == "ask"
    assert not result["completed"] and result["status"] == "needs_input"


def test_desktop_research_uses_own_canonical_workflow(state, monkeypatch):
    import smara.research_chat as research
    calls = []
    monkeypatch.setattr(research, "run_research_chat", lambda **kwargs: calls.append(kwargs) or {
        "status": "needs_input", "completed": False, "answer": "Review unavailable"})
    result = executor._run_shared_local_agent_turn({"prompt": "Research databases", "research_mode": "deep",
        "model": {"base_url": "https://api.sarvam.ai/v2", "model": "glm5.3"}}, state)
    assert calls[0]["model"]["model"] == "glm5.3"
    assert calls[0]["research_mode"] == "deep" and calls[0]["approval_mode"] == "ask"
    assert result["status"] == "needs_input"


@pytest.mark.parametrize("model", [None, {}, {"base_url": "codex://isolated", "model": "anything"}])
def test_no_implicit_model_or_external_cli_fallback(state, model):
    with pytest.raises(RuntimeError, match="model configuration|endpoint|HTTP"):
        executor._run_shared_local_agent_turn({"prompt": "hello", "model": model}, state)


def test_cli_and_desktop_do_not_import_external_bridge():
    from smara.cli import LocalAutonomousEngine
    assert "codex_harness" not in LocalAutonomousEngine._run_shared_local_turn.__code__.co_names
    assert "codex_harness" not in executor._run_shared_local_agent_turn.__code__.co_names
    assert not (Path(executor.__file__).parent / "codex_harness.py").exists()
