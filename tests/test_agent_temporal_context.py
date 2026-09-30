from datetime import datetime
from zoneinfo import ZoneInfo

from smara.autonomous_agent import (
    BASE_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    _answer_requested_current_date,
    _application_clock,
    _model_output_token_limit,
    _should_enter_research_recovery,
)


def test_explicit_local_date_is_preserved_with_compound_research_answer():
    question = "What is today's date in Asia/Kolkata, and what is the latest stable Python release?"
    result = _answer_requested_current_date(question, "Latest Python 3 Release - Python 3.14.7")
    expected = datetime.now(ZoneInfo("Asia/Kolkata"))

    assert "Latest Python 3 Release - Python 3.14.7" in result
    assert f"Current date in Asia/Kolkata: {expected.date().isoformat()}" in result
    assert expected.strftime("%A, %d %B %Y") in result


def test_current_date_completion_does_not_duplicate_a_date_or_change_historical_request():
    today = _application_clock("Asia/Kolkata").date().isoformat()
    question = "What is today's date in Asia/Kolkata?"
    existing = f"Today is {today}."
    assert _answer_requested_current_date(question, existing) == existing

    historical = "As of 2025-03-31, Python 3.13.2 was the latest release."
    assert _answer_requested_current_date("As of 2025-03-31, what was latest?", historical) == historical


def test_agent_temporal_guidance_distinguishes_current_and_historical_intent():
    assert "Application Clock" in RESEARCH_SYSTEM_PROMPT
    assert "historical 'as of' requests" in RESEARCH_SYSTEM_PROMPT
    assert "requested cutoff" in BASE_SYSTEM_PROMPT


def test_failed_fetch_and_blocked_dependencies_keep_retrieval_available():
    assert not _should_enter_research_recovery("research_fetch", 0, '{"status":"error","error":"404 not found"}')
    assert not _should_enter_research_recovery("research_gather", 0, '{"status":"blocked"}')
    assert not _should_enter_research_recovery("research_fetch", 2, '{"status":"error","error":"404 not found"}')
    assert _should_enter_research_recovery("research_gather", 2, '{"status":"ok"}')


def test_research_turns_use_budget_proportional_output_reservations():
    assert _model_output_token_limit("research_web") == 8192
    assert _model_output_token_limit("research") == 8192
    assert _model_output_token_limit("full") == 16384
