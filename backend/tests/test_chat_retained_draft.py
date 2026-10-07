import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.errors import ApiError
from app.services import chat_history
from app.services.chat_stream import stream_chat
from app.services.llm_profiles import request_state

DRAFT = 'Расчёт профвзносов: удержание из заработной платы [1].'


def test_stream_validation_failure_retains_draft_in_owned_history(monkeypatch, tmp_path):
    from app.config import Settings
    monkeypatch.setattr('app.services.chat_stream.get_settings', lambda: Settings(_env_file=None, data_dir=tmp_path))
    user = SimpleNamespace(user_id='draft-owner', username='demo')
    ref = chat_history.begin_attempt(None, user, 'Расчёт профвзносов', str(uuid4()), 'fast')
    chat_history.save_attempt_sources(ref, user, [{'doc_id': 'source', 'source_index': 1}])

    def work():
        state = request_state()
        state['on_start'](ref)
        state['on_text'](DRAFT[:12])
        state['on_text'](DRAFT[12:])
        chat_history.finish_attempt(ref, user, status='failed', answer='')
        raise ApiError(status_code=422, code='chat_evidence_invalid', detail='PRIVATE_ERROR')

    async def collect():
        return [json.loads(line) async for line in stream_chat(work, current_user=user)]

    events = asyncio.run(collect())
    assert events[-1]['type'] == 'error'
    assert events[-1]['code'] == 'chat_evidence_invalid'
    assert 'PRIVATE_ERROR' not in json.dumps(events)
    message = chat_history.get_thread(ref.session_id, user.user_id)['messages'][-1]
    assert message['content'] == DRAFT
    assert message['sources'][0]['doc_id'] == 'source'
    assert message['retrieval_metadata']['answer_attempt']['status'] == 'failed'
    assert message['retrieval_metadata']['answer_attempt']['error_code'] == 'chat_evidence_invalid'
    assert not chat_history.finish_attempt(ref, user, status='completed', answer='late')


@pytest.mark.parametrize('status', ['incomplete', 'completed'])
def test_retained_draft_cannot_replace_active_or_completed_answer(status):
    user = SimpleNamespace(user_id='draft-owner', username='demo')
    ref = chat_history.begin_attempt(None, user, 'q', str(uuid4()), 'fast')
    if status == 'completed':
        chat_history.finish_attempt(ref, user, status=status, answer='verified')
    assert not chat_history.retain_attempt_draft(ref, user, DRAFT, error_code='chat_evidence_invalid')
    message = chat_history.get_thread(ref.session_id, user.user_id)['messages'][-1]
    assert message['content'] == ('verified' if status == 'completed' else '')


def test_retained_draft_enforces_owner_and_does_not_replace_existing_draft():
    user = SimpleNamespace(user_id='draft-owner', username='demo')
    ref = chat_history.begin_attempt(None, user, 'q', str(uuid4()), 'fast')
    chat_history.finish_attempt(ref, user, status='failed', answer='')
    with pytest.raises(chat_history.ChatOwnershipError):
        chat_history.retain_attempt_draft(ref, SimpleNamespace(user_id='other'), DRAFT)
    assert chat_history.retain_attempt_draft(ref, user, DRAFT, error_code='chat_evidence_invalid')
    assert not chat_history.retain_attempt_draft(ref, user, 'late')
    assert chat_history.get_thread(ref.session_id, user.user_id)['messages'][-1]['content'] == DRAFT
