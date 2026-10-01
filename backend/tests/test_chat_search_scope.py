from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from qdrant_client.http import models as qm

from app.config import Settings
from app.db.models import Document, OkfConcept
from app.db.session import session_scope
from app.services.fusion import Hit
from app.services.vector_store import VectorStore
from tests.test_chat import make_client


@pytest.fixture
def scoped_chat(monkeypatch):
    from app.api import chat as api

    with session_scope() as session:
        session.add_all([
            Document(id='scope-a', filename='A.docx'),
            Document(id='scope-b', filename='B.docx'),
            Document(id='scope-deleted', filename='Secret.docx', deleted_at=datetime.now(timezone.utc)),
        ])
        for doc_id, slug, index in [('scope-a', 'first', 0), ('scope-a', 'neighbor', 1), ('scope-b', 'outside', 0)]:
            session.add(OkfConcept(doc_id=doc_id, slug=slug, chunk_index=index,
                                   title=f'Данные {slug}', content=f'Данные раздела {slug}'))
    # The out-of-scope hit also probes the API's defensive scope check.
    hits = [Hit(slug, score, dict(point_type='concept', doc_id=doc_id, slug=slug, chunk_index=index))
            for doc_id, slug, index, score in [('scope-b', 'outside', 0, 1.),
                ('scope-a', 'first', 0, .9), ('scope-a', 'neighbor', 1, .8)]]
    calls = []
    monkeypatch.setattr(api, '_get_embedder', lambda: SimpleNamespace(embed=lambda _: [0.]))
    monkeypatch.setattr(api, '_get_vector_store', lambda: SimpleNamespace(
        search_composite=lambda **kwargs: calls.append(kwargs) or hits))
    monkeypatch.setattr(api.ChatTokenBudget, 'fits', lambda *a, **k: True)
    monkeypatch.setattr(api, '_get_llm', lambda: SimpleNamespace(model='test', chat=lambda *a: 'Ответ [1]'))
    return make_client(monkeypatch), calls


@pytest.mark.parametrize('mode', ['documents', 'fast', 'full'])
def test_search_scope_limits_every_mode_to_whole_documents(scoped_chat, mode):
    from app.services.chat_history import get_thread

    client, calls = scoped_chat
    response = client.post('/api/chat', json={'query': 'Данные', 'response_mode': mode,
        'session_id': str(uuid4()), 'search_doc_ids': ['scope-a', 'scope-a']})
    assert response.status_code == 200, response.text
    result = response.json()
    assert {source['doc_id'] for source in result['sources']} == {'scope-a'}
    assert {source['source_slug'] for source in result['sources']} == {'first', 'neighbor'}
    assert calls[0]['doc_ids'] == ['scope-a']
    saved = get_thread(result['session_id'], 'anonymous')['messages'][-1]
    assert saved['retrieval_metadata']['search_doc_ids'] == ['scope-a']


@pytest.mark.parametrize('ids,code', [([], 'chat_search_scope_empty'),
    (['scope-deleted', 'missing'], 'chat_search_scope_unavailable')])
def test_empty_or_unavailable_scope_never_searches_the_whole_database(scoped_chat, monkeypatch, ids, code):
    from app.api import chat as api

    client, calls = scoped_chat
    def forbidden():
        raise AssertionError('Empty scope must stop before embedding or retrieval')
    monkeypatch.setattr(api, '_get_embedder', forbidden)
    response = client.post('/api/chat', json={'query': 'Данные', 'response_mode': 'documents', 'search_doc_ids': ids})
    assert response.status_code in (409, 422), response.text
    assert response.json()['code'] == code
    assert calls == []


def test_scope_listing_preserves_missing_items_without_exposing_deleted_metadata(scoped_chat):
    client, _ = scoped_chat
    response = client.post('/api/chat/search-scope', json={'doc_ids': ['scope-a', 'scope-deleted', 'missing', 'scope-a']})
    assert response.status_code == 200, response.text
    assert response.json() == [
        {'doc_id': 'scope-a', 'filename': 'A.docx', 'available': True},
        {'doc_id': 'scope-deleted', 'filename': None, 'available': False},
        {'doc_id': 'missing', 'filename': None, 'available': False},
    ]


def test_scope_passes_only_live_document_ids_to_search(scoped_chat):
    client, calls = scoped_chat
    response = client.post('/api/chat', json={'query': 'Данные', 'response_mode': 'documents',
        'search_doc_ids': ['scope-a', 'scope-deleted', 'missing']})
    assert response.status_code == 200, response.text
    assert calls[0]['doc_ids'] == ['scope-a']


def test_disabled_scope_keeps_global_search(scoped_chat):
    client, calls = scoped_chat
    response = client.post('/api/chat', json={'query': 'Данные', 'response_mode': 'documents'})
    assert response.status_code == 200, response.text
    assert {source['doc_id'] for source in response.json()['sources']} == {'scope-a', 'scope-b'}
    assert calls[0].get('doc_ids') is None


def test_no_matches_in_scope_does_not_retry_global_search(scoped_chat, monkeypatch):
    from app.api import chat as api

    client, calls = scoped_chat
    monkeypatch.setattr(api, '_get_vector_store', lambda: SimpleNamespace(
        search_composite=lambda **kwargs: calls.append(kwargs) or []))
    def no_llm():
        raise AssertionError('An empty scoped result must not call the LLM')
    monkeypatch.setattr(api, '_get_llm', no_llm)
    response = client.post('/api/chat', json={'query': 'Данные', 'response_mode': 'fast', 'search_doc_ids': ['scope-a']})
    assert response.status_code == 200, response.text
    assert response.json()['sources'] == []
    assert 'В области поиска' in response.json()['answer']
    assert len(calls) == 1
    assert calls[0]['doc_ids'] == ['scope-a']


@pytest.mark.parametrize('ids', [[' '], [False], ['a'] * 501])
def test_scope_requests_validate_ids_and_capacity(scoped_chat, ids):
    client, calls = scoped_chat
    for path, body in [('/api/chat', {'query': 'Данные', 'search_doc_ids': ids}),
                       ('/api/chat/search-scope', {'doc_ids': ids})]:
        response = client.post(path, json=body)
        assert response.status_code == 422, response.text
    assert calls == []


def test_fragment_selection_cannot_be_combined_with_document_scope(scoped_chat):
    client, calls = scoped_chat
    response = client.post('/api/chat', json={'query': 'Данные', 'response_mode': 'full',
        'session_id': str(uuid4()), 'search_doc_ids': ['scope-a'],
        'source_selection': {'attempt_id': str(uuid4()), 'indexes': [1]}})
    assert response.status_code == 422, response.text
    assert calls == []


def test_failed_attempt_keeps_the_requested_scope_in_history(scoped_chat):
    from app.services.chat_history import get_thread

    client, _ = scoped_chat
    session_id = str(uuid4())
    response = client.post('/api/chat', json={'query': 'Данные', 'response_mode': 'documents',
        'session_id': session_id, 'search_doc_ids': ['scope-deleted']})
    assert response.status_code == 409, response.text
    saved = get_thread(session_id, 'anonymous')['messages'][-1]['retrieval_metadata']
    assert saved['answer_attempt']['status'] == 'failed'
    assert saved['search_doc_ids'] == ['scope-deleted']


def test_scope_filter_reaches_both_branches_and_graph_with_other_filters():
    store = VectorStore.__new__(VectorStore)
    store.settings = Settings(_env_file=None, search_graph_expansion_enabled=True)
    filters = []
    def retrieve(vector, query_filter, top_k):
        filters.append(query_filter)
        return [Hit('inside', 1., {'doc_id': 'scope-a'})]
    store.search_dense = retrieve
    store.search_bm25 = retrieve
    def graph(ranked_lists, *, search_filter, per_branch_top_k):
        filters.append(search_filter)
        return []
    store._graph_expansion = graph
    result = store.search_composite(dense_vec=[0.], sparse_vec=qm.SparseVector(indices=[1], values=[1.]),
        tags=['tag'], source_locales=['ru'], mail_mode='exclude', branches={'dense', 'bm25'},
        top_k=1, doc_ids=['scope-a', 'scope-b'])
    assert len(result) == 1
    assert len(filters) == 3
    for query_filter in filters:
        conditions = {condition.key: condition for condition in query_filter.must if isinstance(condition, qm.FieldCondition)}
        assert conditions['doc_id'].match.any == ['scope-a', 'scope-b']
        assert conditions['mail_scope'].match.value == 'document'
        assert 'source_locale' in str(query_filter)
        assert 'tag' in str(query_filter)


def test_vector_search_with_empty_scope_makes_no_qdrant_requests():
    store = VectorStore.__new__(VectorStore)
    store.settings = Settings(_env_file=None)
    def forbidden(*args, **kwargs):
        raise AssertionError('Empty scope must not query Qdrant')
    store.search_dense = store.search_bm25 = store._graph_expansion = forbidden
    status = {}
    assert store.search_composite(dense_vec=[0.], sparse_vec=None, tags=None,
        branches={'dense'}, top_k=5, doc_ids=[], retrieval_status=status) == []
    assert status['limit_reached'] is False
