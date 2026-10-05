from smara import research_chat
from smara.runtime_session import session_store_for_state


def test_chat_routes_selected_model_and_persists_real_review_contract(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    calls = []
    review = {"passed": False, "claims": [], "failures": [{"url": "https://source.test", "error": "timeout"}]}

    def run(prompt, workspace, **kwargs):
        calls.append((prompt, workspace, kwargs))
        kwargs["on_progress"]("tool_start", {"tool": "research_fetch", "args": {"url": "https://source.test"}})
        return {"session_id": "research-1", "status": "needs_input", "answer": "Source unavailable",
                "research_review": review, "research_mode": "quick", "unresolved_work": ["missing source"]}

    monkeypatch.setattr(research_chat, "run_canonical_task", run)
    events = []
    result = research_chat.run_research_chat(
        prompt="Research latest release", state_path=state, workspace=tmp_path,
        model={"base_url": "https://provider.test/v2", "model": "selected", "api_key": "secret-unit-test"},
        context=[{"role": "user", "content": "Earlier turn"}], conversation_id="chat-1",
        research_mode="quick", event_callback=events.append,
    )
    assert calls[0][2]["model_settings"]["model"] == "selected"
    assert calls[0][2]["context_history"][0]["content"] == "Earlier turn"
    assert result["research_review"] == review
    assert result["completed"] is False
    assert events[0]["type"] == "session_event"
    assert events[0]["event"]["kind"] == "turn.started"
    assert any(event["type"] == "tool_call" for event in events)
    snapshot = session_store_for_state(state).snapshot("chat-1")
    assert snapshot["session"]["result"]["research_review"] == review
    assert "secret-unit-test" not in str(snapshot)


def test_chat_records_failure_without_exception_secrets(tmp_path, monkeypatch):
    import pytest
    state = tmp_path / "state.json"
    def fail(*args, **kwargs):
        raise RuntimeError("provider error with secret-unit-test")
    monkeypatch.setattr(research_chat, "run_canonical_task", fail)
    with pytest.raises(RuntimeError):
        research_chat.run_research_chat(
            prompt="Research", state_path=state, workspace=tmp_path,
            model={"model": "selected"}, context=[], conversation_id="failed",
            research_mode="quick",
        )
    snapshot = session_store_for_state(state).snapshot("failed")
    assert snapshot["session"]["status"] == "failed"
    assert "secret-unit-test" not in str(snapshot)


def test_canonical_adapter_uses_desktop_model_without_persisting_key(tmp_path, monkeypatch):
    from smara import app_adapter
    from smara.harness import SessionEngine
    observed = {}
    def run(self, task, max_iterations, context_history):
        observed.update(model=self.model, key=self.api_key, context=context_history)
        self.session_engine.begin_incremental(task)
        return {"session": self.session_engine.finish_incremental("needs_input", "No network in unit test")}
    monkeypatch.setattr(app_adapter.SmaraAutonomousAgent, "run", run)
    result = app_adapter.run_canonical_task(
        "Research current release", tmp_path, session_id="desktop-config",
        model_settings={"model": "user-selected", "api_key": "secret-unit-test",
                        "base_url": "https://provider.test/v2", "auth_header": "api-subscription-key"},
        context_history=[{"role": "user", "content": "Previous question"}],
    )
    assert observed["model"] == "user-selected"
    assert observed["key"] == "secret-unit-test"
    assert observed["context"][0]["content"] == "Previous question"
    session = SessionEngine(tmp_path, result["session_id"])
    try:
        assert "secret-unit-test" not in str(session.inspect())
    finally:
        session.close()


def test_research_progress_exposes_provider_wait_answer_and_real_tool_failures(tmp_path, monkeypatch):
    events = []

    def run(prompt, workspace, **kwargs):
        progress = kwargs["on_progress"]
        progress("context_packed", {"input_tokens": 1200})
        progress("model_request", {"attempt": 1, "timeout_seconds": 90})
        progress("model_retry", {"attempt": 2})
        progress("thought", {"thought": "Private provider reasoning must not become a status message"})
        progress("tool_end", {"tool": "research_fetch", "observation": '{"status":"error"}'})
        progress("tool_end", {"tool": "research_validate", "observation": '{"status":"ok","passed":false}'})
        progress("tool_end", {"tool": "research_search", "observation": '{"status":"ok"}'})
        progress("answer", {"answer": "The real result from this deterministic test."})
        return {"session_id": "research-1", "status": "needs_input", "answer": "The real result from this deterministic test.",
                "research_review": None, "research_mode": "deep", "unresolved_work": ["No live provider in unit test"]}

    monkeypatch.setattr(research_chat, "run_canonical_task", run)
    research_chat.run_research_chat(prompt="Explain database history", state_path=tmp_path / "state.json",
        workspace=tmp_path, model={"model": "test"}, context=[], conversation_id="progress",
        research_mode="deep", event_callback=events.append)
    labels = [event.get("label") for event in events if event["type"] == "status"]
    assert "Waiting for model response" in labels
    assert "Retrying model request" in labels
    assert "Private provider reasoning" not in str(events)
    results = [event["ok"] for event in events if event["type"] == "tool_result"]
    assert results == [False, False, True]
    tokens = [event["text"] for event in events if event["type"] == "token"]
    assert tokens == ["The real result from this deterministic test."]


def test_chat_handles_budget_exhausted_status_gracefully(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    def run_budget_exhausted(prompt, workspace, **kwargs):
        return {
            "session_id": "exhausted-1", "status": "budget_exhausted",
            "answer": "Research stopped at budget limit.",
            "research_review": None, "research_mode": "quick",
            "unresolved_work": ["budget exhausted"],
        }
    monkeypatch.setattr(research_chat, "run_canonical_task", run_budget_exhausted)
    result = research_chat.run_research_chat(
        prompt="Research enterprise memory", state_path=state, workspace=tmp_path,
        model={"model": "test"}, context=[], conversation_id="budget-exhausted",
        research_mode="quick",
    )
    assert result["status"] == "failed"
    assert result["answer"] == "Research stopped at budget limit."
    snapshot = session_store_for_state(state).snapshot("budget-exhausted")
    assert snapshot["session"]["status"] == "failed"
