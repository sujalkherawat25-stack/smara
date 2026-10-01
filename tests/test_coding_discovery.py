import pytest
import json

from smara.agent_tools import list_directory, search_files
from smara.autonomous_agent import SmaraAutonomousAgent
from smara.harness import Budget, SessionEngine
from test_h3_durable_agent_integration import Response, tool_response, final_response


@pytest.mark.parametrize('fallback', [False, True])
def test_project_search_excludes_generated_journals(tmp_path, monkeypatch, fallback):
    (tmp_path / '.smara').mkdir()
    (tmp_path / '.smara' / 'journal.md').write_text('fictional policy needle')
    (tmp_path / 'app.py').write_text('value = 1')
    if fallback:
        monkeypatch.setattr('smara.agent_tools.shutil.which', lambda _: None)
    assert '.smara' not in list_directory(str(tmp_path))
    assert search_files('needle', str(tmp_path)).startswith('No matches found')
    # Explicit diagnostic access remains possible, including hidden paths.
    assert 'journal.md' in list_directory(str(tmp_path / '.smara'))


@pytest.mark.parametrize('iterations', [4, 8])
def test_four_missing_lookups_stop_before_another_model_call(tmp_path, monkeypatch, iterations):
    requests = []
    def respond(*args, **kwargs):
        requests.append(1)
        return Response(tool_response(str(len(requests)), 'file_read', {'path': f'missing{len(requests)}.txt'}), 'fixture')
    monkeypatch.setattr('urllib.request.urlopen', respond)
    session = SessionEngine(tmp_path, 'empty', budget=Budget(60, 20, 10, 500_000, 1))
    agent = SmaraAutonomousAgent(api_key='fixture', toolset='coding', workspace_root=tmp_path, session_engine=session)
    result = agent.run('Implement the project requirements', max_iterations=iterations)
    assert len(requests) == 4
    assert result['status'] == result['session']['status'] == 'needs_input'
    assert result['answer'].startswith('needs_input')
    assert session.get('usage')['tool_calls'] == 4
    assert any(e['type'] == 'coding_discovery_stopped' for e in session.inspect()['events'])


def test_repeated_successful_reads_prompt_then_stop_before_more_model_calls(tmp_path, monkeypatch):
    (tmp_path / 'source.py').write_text('value = 1')
    requests = []
    def respond(*args, **kwargs):
        requests.append(json.loads(args[0].data))
        return Response(tool_response(str(len(requests)), 'file_read', {'path': 'source.py'}), 'fixture')
    monkeypatch.setattr('urllib.request.urlopen', respond)
    session = SessionEngine(tmp_path, 'stalled-reading', budget=Budget(120, 30, 20, 500_000, 1))
    agent = SmaraAutonomousAgent(api_key='fixture', toolset='coding', workspace_root=tmp_path, session_engine=session)
    result = agent.run('Inspect and improve source.py', max_iterations=20)
    assert len(requests) == 12  # The guard runs before another provider call.
    assert any('Coding progress guard' in str(message) for message in requests[8]['messages'])
    assert result['status'] == 'needs_input'
    assert result['answer'].startswith('needs_input')
    assert any(e['type'] == 'coding_progress_stopped' for e in session.inspect()['events'])


def test_mutation_or_focused_verification_resets_read_only_streak(tmp_path):
    agent = SmaraAutonomousAgent(api_key='fixture', toolset='coding', workspace_root=tmp_path)
    for _ in range(8): agent._record_coding_discovery('file_read', {'path': 'source.py'}, 'value = 1')
    assert agent._coding_read_only_streak == 8
    agent._record_coding_discovery('terminal', {'command': 'python -m pytest -q'}, '[Exit Code: 0]\n2 passed')
    assert agent._coding_read_only_streak == 0
    for _ in range(8): agent._record_coding_discovery('file_read', {'path': 'source.py'}, 'value = 1')
    agent._record_coding_discovery('patch', {}, 'Patch applied successfully')
    assert agent._coding_read_only_streak == 0


def test_distinct_review_observations_do_not_trigger_stall_guard(tmp_path):
    agent = SmaraAutonomousAgent(api_key='fixture', toolset='coding', workspace_root=tmp_path)
    for index in range(20):
        agent._record_coding_discovery('file_read', {'path': f'{index}.py'}, f'value = {index}')
    assert agent._coding_read_only_streak == 1


def test_coding_context_retains_source_middle_when_within_capacity(tmp_path, monkeypatch):
    from smara.autonomous_agent import _offload_massive_result
    source = 'first\n' + ('x' * 7000) + '\nimportant_middle_function\n' + ('y' * 7000) + '\nlast'
    assert _offload_massive_result(source, 'read', max_chars=32000) == source
    requests = []
    def respond(request, timeout):
        requests.append(json.loads(request.data))
        return Response(final_response('Reviewed source.'), 'fixture')
    monkeypatch.setattr('urllib.request.urlopen', respond)
    agent = SmaraAutonomousAgent(api_key='fixture', toolset='coding', workspace_root=tmp_path)
    agent._call_model_api([
        {'role': 'system', 'content': 'Inspect code'},
        {'role': 'user', 'content': 'Review source'},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'read', 'type': 'function', 'function': {'name': 'file_read', 'arguments': '{}'}}]},
        {'role': 'tool', 'tool_call_id': 'read', 'content': source},
    ])
    assert requests[0]['messages'][-1]['content'] == source


def test_coding_uses_bounded_sarvam_reasoning(tmp_path, monkeypatch):
    requests = []
    def respond(request, timeout):
        requests.append(json.loads(request.data))
        return Response(final_response('Reviewed source.'), 'fixture')
    monkeypatch.setattr('urllib.request.urlopen', respond)
    monkeypatch.delenv('SMARA_CODING_REASONING_EFFORT', raising=False)
    agent = SmaraAutonomousAgent(api_key='fixture', model='glm5.3', base_url='https://api.sarvam.ai/v2', toolset='coding', workspace_root=tmp_path)
    agent.run('Review source', max_iterations=1)
    assert requests[0]['reasoning_effort'] == 'low'
    assert "Smara's coding assistant" in requests[0]['messages'][0]['content']
    assert 'Ciphers & Decryption' not in requests[0]['messages'][0]['content']


def test_successful_read_and_mutation_reset_discovery(tmp_path):
    agent = SmaraAutonomousAgent(api_key='fixture', toolset='coding', workspace_root=tmp_path)
    for _ in range(3): agent._record_coding_discovery('search_files', {'query': 'x'}, 'No matches found')
    agent._record_coding_discovery('file_read', {}, 'real source')
    assert not agent._empty_coding_lookups
    agent._record_coding_discovery('file_read', {'path': 'missing'}, 'Error: File not found')
    agent._record_coding_discovery('patch', {}, 'Successfully patched source')
    assert not agent._empty_coding_lookups


def test_research_is_not_stopped_by_coding_guard(tmp_path):
    agent = SmaraAutonomousAgent(api_key='fixture', toolset='research_web', workspace_root=tmp_path)
    for _ in range(8): agent._record_coding_discovery('search_files', {'query': 'x'}, 'No matches found')
    assert not agent._empty_coding_lookups


def test_source_error_words_do_not_create_failed_read_receipts(tmp_path):
    (tmp_path / 'app.py').write_text('def example():\n    raise Exception("failed error traceback")\n')
    session = SessionEngine(tmp_path, 'read-source', budget=Budget(60, 10, 10, 100_000, 1))
    session.begin_incremental('Read code')
    agent = SmaraAutonomousAgent(api_key='fixture', toolset='coding', workspace_root=tmp_path, session_engine=session)
    agent.execute_tool('file_read', {'path': 'app.py'}, call_id='source')
    agent.execute_tool('file_read', {'path': 'absent.py'}, call_id='missing')
    receipts = session.inspect()['calls']
    assert receipts[0]['result']['status'] == 'ok'
    assert receipts[1]['result']['status'] == 'error'


def test_provider_timeout_uses_remaining_session_wall_budget(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr('smara.harness.time.time', lambda: now[0])
    session = SessionEngine(tmp_path, 'deadline', budget=Budget(60, 10, 10, 500_000, 1))
    session.begin_incremental('Read project')
    now[0] += 49
    timeouts = []
    def respond(request, timeout):
        timeouts.append(timeout)
        return Response(final_response('Reviewed project.'), 'fixture')
    monkeypatch.setattr('urllib.request.urlopen', respond)
    SmaraAutonomousAgent(api_key='fixture', workspace_root=tmp_path, session_engine=session).run('Read project', max_iterations=2)
    assert len(timeouts) == 1 and 0 < timeouts[0] <= 11


def test_runtime_state_is_not_exposed_as_top_level_project_context(tmp_path):
    (tmp_path / '.smara').mkdir()
    (tmp_path / 'app.py').write_text('value = 1')
    agent = SmaraAutonomousAgent(api_key='fixture', workspace_root=tmp_path)
    context = agent._build_dynamic_context()
    assert '.smara/' not in context
    assert 'app.py' in context
