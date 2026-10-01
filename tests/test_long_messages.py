import json
from pathlib import Path

import pytest

from smara.agent_tools import file_read, _truncate_output
from smara.desktop_executor import _read_file
from smara.local_agent_runtime import _messages_from_history, LocalModelConfig, OpenAICompatiblePlanner
from smara.local_conversation_memory import SQLiteConversationMemory
from smara.long_context import context_excerpt
from smara.models import ChatRequest
from smara.models import TaskCreate, ExecutorComplete
from smara.runtime_session import SQLiteRuntimeSessionStore


def test_large_unicode_turns_survive_storage_and_api(tmp_path):
    text = ('long input 🦊\r\n' * 8000) + 'TAIL_SENTINEL'
    assert ChatRequest(message=text).message == text
    assert TaskCreate(title='Long task', objective=text).objective == text
    assert ExecutorComplete(result=text).result == text
    sessions = SQLiteRuntimeSessionStore(tmp_path / 'sessions.sqlite3')
    session = sessions.create_or_get(request=text)
    assert sessions.get(session.session_id).request == text
    memory = SQLiteConversationMemory(tmp_path / 'memory.sqlite3')
    memory.append_exchange(conversation_id='large', user_message=text, assistant_message=text)
    assert all(item['content'] == text for item in memory.recent(workspace_id='default'))


def test_inputs_beyond_old_eight_thousand_limit_remain_inline(tmp_path):
    text = 'a' * 9000 + 'TAIL_SENTINEL'
    assert _messages_from_history([{'role': 'user', 'content': text}], tmp_path)[0]['content'] == text


def test_large_single_line_can_be_reconstructed_without_loss(tmp_path):
    text = 'BEGIN' + ('🦊\r\n' * 50000) + 'MIDDLE_SENTINEL' + 'z' * 70000 + 'END'
    preview = context_excerpt(text, tmp_path)
    assert 'not the full content' in preview
    path = next((tmp_path / '.smara' / 'long-context').glob('*.txt'))
    parts = []
    start = 0
    while True:
        chunk = json.loads(file_read(path, start_char=start, max_chars=32000))
        parts.append(chunk['content'])
        if chunk['next_start_char'] is None:
            break
        start = chunk['next_start_char']
    assert ''.join(parts) == text
    result = json.loads(_read_file({'path': str(path), 'operation': 'read_file', 'start_char': len(text)-3}, [tmp_path]))
    assert result['content'] == 'END' and result['next_start_char'] is None


def test_long_tool_output_is_preserved_with_exit_status(tmp_path):
    text = '[Exit Code: 0]\n' + 'x' * 30000 + 'TAIL_SENTINEL'
    preview = _truncate_output(text, workspace=tmp_path)
    assert preview.startswith('[Exit Code: 0]')
    assert 'TAIL_SENTINEL' in preview
    assert next((tmp_path / '.smara' / 'long-context').glob('*.txt')).read_text() == text


@pytest.mark.parametrize('always_length', [False, True])
def test_long_answers_continue_without_losing_chunks(tmp_path, monkeypatch, always_length):
    planner = OpenAICompatiblePlanner(LocalModelConfig(base_url='http://fixture.invalid/v1', model='fixture'), tmp_path)
    calls = []
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            number = len(calls)
            return {'choices': [{'finish_reason': 'length' if always_length or number < 3 else 'stop',
                                'message': {'content': f'part{number}|'}}]}
    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        return Response()
    monkeypatch.setattr(planner.client, 'post', post)
    try:
        result = planner([{'role': 'user', 'content': 'Write a long report'}])
    finally:
        planner.close()
    expected_calls = 9 if always_length else 3
    assert len(calls) == expected_calls
    assert result['answer'] == ''.join(f'part{i}|' for i in range(1, expected_calls+1))
    if always_length:
        assert result['completed'] is False
    assert all('tools' not in call for call in calls[1:])


def test_failed_continuation_keeps_the_partial_answer(tmp_path, monkeypatch):
    import httpx
    planner = OpenAICompatiblePlanner(LocalModelConfig(base_url='http://fixture.invalid/v1', model='fixture'), tmp_path)
    calls = []
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {'choices': [{'finish_reason': 'length', 'message': {'content': 'preserved first part'}}]}
    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise httpx.ReadTimeout('fixture')
        return Response()
    monkeypatch.setattr(planner.client, 'post', post)
    try:
        result = planner([{'role': 'user', 'content': 'Long answer'}])
    finally:
        planner.close()
    assert result['answer'] == 'preserved first part'
    assert result['completed'] is False
    assert result['failure_reason'] == 'output_continuation_failed'
