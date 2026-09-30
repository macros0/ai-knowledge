from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services.fusion import Hit
from tests.test_chat import make_client


@pytest.fixture
def found(monkeypatch):
    from app.api import chat as api
    with session_scope() as s:
        s.add(Document(id='selected-doc', filename='reference.docx'))
        for index in range(3):
            s.add(OkfConcept(doc_id='selected-doc', slug=f'part-{index}',
                             title=f'Источник {index}', content=f'Доказательство {index}', chunk_index=index))
    hits = [Hit(str(i), 1 - i * .1, dict(point_type='concept', doc_id='selected-doc',
                slug=f'part-{i}', title=f'Источник {i}', chunk_index=i)) for i in range(3)]
    monkeypatch.setattr(api, '_get_embedder', lambda: SimpleNamespace(embed=lambda _: [0.]))
    monkeypatch.setattr(api, '_get_vector_store', lambda: SimpleNamespace(search_composite=lambda **_: hits))
    monkeypatch.setattr(api.ChatTokenBudget, 'fits', lambda *a, **k: True)
    prompts = []
    monkeypatch.setattr(api, '_get_llm', lambda: SimpleNamespace(model='test', chat=lambda sys, user: prompts.append(user) or 'Ответ [1]'))
    client = make_client(monkeypatch)
    search = client.post('/api/chat', json={'query': 'Доказательство', 'response_mode': 'documents',
                                         'attempt_id': str(uuid4()), 'session_id': str(uuid4())}).json()
    assert len(search['sources']) == 3
    def no_search():
        raise AssertionError('Selected answer must not retrieve again')
    monkeypatch.setattr(api, '_get_embedder', no_search)
    monkeypatch.setattr(api, '_get_vector_store', no_search)
    return client, search, prompts


def selected_request(search, indexes):
    return {'query': search['query'], 'response_mode': 'full', 'session_id': search['session_id'],
            'source_selection': {'attempt_id': search['attempt_id'], 'indexes': indexes}}


def test_answer_uses_only_selected_found_fragments(found):
    client, search, prompts = found
    response = client.post('/api/chat', json=selected_request(search, [3, 1, 3]))
    assert response.status_code == 200, response.text
    assert [s['title'] for s in response.json()['sources']] == ['Источник 0', 'Источник 2']
    assert 'Доказательство 0' in prompts[0] and 'Доказательство 2' in prompts[0]
    assert 'Доказательство 1' not in prompts[0]
    assert len(prompts) == 1
    from app.services.chat_history import get_thread
    messages = get_thread(search['session_id'], 'anonymous')['messages']
    assert len(messages[1]['sources']) == 3
    assert len(messages[-1]['sources']) == 2


@pytest.mark.parametrize('indexes', [[], [4], [True], [1.5]])
def test_selection_rejects_empty_invalid_or_forged_indices(found, indexes):
    client, search, prompts = found
    response = client.post('/api/chat', json=selected_request(search, indexes))
    assert response.status_code in (422, 409), response.text
    assert prompts == []


def test_selection_rejects_changed_source_before_llm(found):
    client, search, prompts = found
    with session_scope() as s:
        s.query(OkfConcept).filter_by(slug='part-1').one().content = 'Новая версия'
    response = client.post('/api/chat', json=selected_request(search, [2]))
    assert response.status_code == 409, response.text
    assert response.json()['code'] == 'chat_sources_changed'
    assert prompts == []


def test_selection_cannot_use_another_users_history(found):
    client, search, prompts = found
    from app.db.models import ChatSession
    with session_scope() as s:
        s.get(ChatSession, search['session_id']).user_id = 'somebody-else'
    response = client.post('/api/chat', json=selected_request(search, [1]))
    assert response.status_code == 403
    assert prompts == []


@pytest.mark.parametrize('change', ['delete', 'republish', 'remove_concept'])
def test_selection_rejects_unavailable_canonical_source(found, change):
    from datetime import datetime, timezone
    from app.db.models import DocumentGenerationState
    client, search, prompts = found
    with session_scope() as s:
        if change == 'delete':
            s.get(Document, 'selected-doc').deleted_at = datetime.now(timezone.utc)
        elif change == 'republish':
            s.add(DocumentGenerationState(doc_id='selected-doc', active_generation_id='new'))
        else:
            s.delete(s.query(OkfConcept).filter_by(slug='part-0').one())
    response = client.post('/api/chat', json=selected_request(search, [1]))
    assert response.status_code == 409, response.text
    assert prompts == []


def test_selected_sources_are_all_processed_across_batches(found, monkeypatch):
    import json
    import re
    from app.api import chat as api
    from app.config import Settings
    client, search, prompts = found
    monkeypatch.setattr(api, 'get_settings', lambda: Settings(_env_file=None, auth_provider='disabled', chat_max_context_chars=380))
    def complete(system, user):
        prompts.append(user)
        if system == api._FACT_INSTRUCTIONS:
            context = json.loads(user)['context']
            index = int(re.search(r'context_block id="(\d+)"', context)[1])
            quote = re.search(r'Доказательство \d', context)[0]
            return json.dumps({'facts': [{'source': index, 'quote': quote, 'text': quote}]})
        return 'Ответ [1] [2] [3]'
    monkeypatch.setattr(api, '_get_llm', lambda: SimpleNamespace(model='test', chat=complete))
    response = client.post('/api/chat', json=selected_request(search, [1, 2, 3]))
    assert response.status_code == 200, response.text
    assert len(prompts) == 4  # Three bounded extraction calls and one synthesis.
    assert all(source['completed_parts'] == source['parts_total'] == 1 for source in response.json()['sources'])
    assert all(source['cited'] for source in response.json()['sources'])


def test_mail_original_is_restored_even_for_multitopic_chunk():
    from app.db.models import DocumentSource
    from app.services.context_builder import merge_and_format
    from app.services.retrieval_hydration import load_visible_retrieval_hits
    from app.services.chat_source_selection import snapshot_blocks, restore_blocks
    with session_scope() as s:
        s.add(Document(id='selected-mail', filename='mail.eml'))
        s.add(DocumentSource(doc_id='selected-mail', source_id='root', ordinal=0, kind='mail'))
        s.add(DocumentChunk(doc_id='selected-mail', chunk_index=0, source_id='root', content='Оригинал письма', char_count=14))
        for slug in ['first', 'second']:
            s.add(OkfConcept(doc_id='selected-mail', slug=slug, title=slug, content=f'Концепт {slug}', chunk_index=0, source_id='root'))
    hits = [Hit(slug, 1., dict(point_type='concept', doc_id='selected-mail', slug=slug, title=slug, chunk_index=0)) for slug in ['first', 'second']]
    hits.append(Hit('chunk', .9, dict(point_type='chunk', doc_id='selected-mail', chunk_index=0)))
    hits, _ = load_visible_retrieval_hits(hits)
    block = merge_and_format(hits, limit_total_chars=False)[0]
    restored = restore_blocks(snapshot_blocks([block]), mail_mode='all')[0]
    assert restored['content'] == 'Концепт first'
    assert restored['mail_fragment']['content'] == 'Оригинал письма'


def test_combined_block_retains_original_chunk_not_unselected_sibling(monkeypatch):
    from app.services.context_builder import merge_and_format, drop_partial_title_matches
    from app.services.retrieval_hydration import load_visible_retrieval_hits
    from app.services.chat_source_selection import snapshot_blocks, restore_blocks
    with session_scope() as s:
        s.add(Document(id='combined-doc', filename='combined.docx'))
        s.add(OkfConcept(doc_id='combined-doc', slug='main', title='Точный объект', content='Выжимка', chunk_index=0))
        s.add(DocumentChunk(doc_id='combined-doc', chunk_index=0, content='Исходный полный фрагмент', char_count=24))
    hits, _ = load_visible_retrieval_hits([
        Hit('c', 1., dict(point_type='concept', doc_id='combined-doc', slug='main', title='Точный объект', chunk_index=0)),
        Hit('k', .9, dict(point_type='chunk', doc_id='combined-doc', chunk_index=0)),
    ])
    blocks = merge_and_format(hits, limit_total_chars=False)
    assert restore_blocks(snapshot_blocks(blocks), mail_mode='all')[0]['content'] == 'Исходный полный фрагмент'
    focused = drop_partial_title_matches(blocks, 'Точный объект')
    assert focused[0]['content'] == 'Выжимка'
    assert restore_blocks(snapshot_blocks(focused), mail_mode='all')[0]['content'] == 'Выжимка'


@pytest.mark.parametrize('kind', ['chunk', 'mail', 'digest'])
def test_selected_authorship_never_expands_to_entire_shared_chunk(monkeypatch, kind):
    from app.api import chat as api
    from app.config import Settings
    from app.models.schemas import ChatRequest, ChatSource
    request = ChatRequest(query='Кто автор?', response_mode='full', session_id=str(uuid4()),
                          source_selection={'attempt_id': str(uuid4()), 'indexes': [1]})
    block = dict(title='Концепт', content='Автор: Иван Петров' if kind == 'chunk' else 'Автор: Выдуманное имя',
                 filepath='doc/c.md', doc_id='doc', point_type='concept', tags=[],
                 _source_index=1, _evidence={'point_type': 'chunk' if kind == 'chunk' else 'concept'})
    if kind == 'mail':
        block['mail_fragment'] = {'content': 'Автор: Иван Петров', 'chunk_index': 0}
    prompts = []
    monkeypatch.setattr(api, '_get_llm', lambda: SimpleNamespace(model='test', chat=lambda sys, user: prompts.append(user) or '{"quotes":[]}'))
    monkeypatch.setattr(api.ChatTokenBudget, 'fits', lambda *a, **k: True)
    def no_expansion(*a, **k):
        raise AssertionError('Must not expand selected original excerpt to whole chunk')
    monkeypatch.setattr(api, 'load_authorship_evidence', no_expansion)
    answer = api._answer_mode(request, [block], [ChatSource(title='Концепт', filepath='doc/c.md', score=1.)],
                              Settings(_env_file=None), (), {}, {})
    assert 'Выдуманное имя' not in answer
    if kind == 'digest':
        assert prompts == []
    else:
        assert 'Иван Петров' in answer
        assert 'Выдуманное имя' not in prompts[0]
