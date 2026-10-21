"""Exercise the real hydration, merge, output, JSON history and navigation boundary."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth.models import User
from app.config import Settings
from app.db.models import Document, DocumentChunk, DocumentSource
from app.db.session import session_scope
from app.models.schemas import ChatRequest, SearchRequest
from app.services.fusion import Hit
from app.services.result_source_location import resolve_result_location

DOC = 'a123456789abcdef'
TEXT = 'General introduction.\n\nDisability status must be recorded in IT0004.\n\nFurther conditions.'


def setup(monkeypatch, module, glossary):
    with session_scope() as session:
        session.add(Document(id=DOC, filename='notes.pdf', status='done'))
        session.add(DocumentSource(doc_id=DOC, source_id='root', kind='document', display_name='notes.pdf'))
        session.add(DocumentChunk(doc_id=DOC, chunk_index=3, content=TEXT, section_title='Disability status', source_id='root'))
    settings = Settings(_env_file=None, auth_provider='disabled', glossary_query_expansion_enabled=glossary)
    monkeypatch.setattr(module, 'get_settings', lambda: settings)
    monkeypatch.setattr(module, '_get_embedder', lambda: SimpleNamespace(embed=lambda query: [1.0]))
    def retrieve(**kwargs):
        return [Hit('raw-point', 1.0, {'doc_id': DOC, 'point_type': 'chunk', 'chunk_index': 3})]
    monkeypatch.setattr(module, '_get_vector_store', lambda: SimpleNamespace(search_composite=retrieve))
    return settings


@pytest.mark.parametrize('mode', ['dense', 'bm25', 'hybrid'])
@pytest.mark.parametrize('response_mode', ['documents', 'fast', 'full'])
@pytest.mark.parametrize('glossary', [False, True])
def test_chat_sources_and_history_keep_the_same_resolvable_reference(monkeypatch, mode, response_mode, glossary):
    from app.api import chat
    from app.services import chat_history
    setup(monkeypatch, chat, glossary)
    monkeypatch.setattr(chat, '_get_llm', lambda: SimpleNamespace(model='test', chat=lambda *a, **kw: 'Answer [1]'))
    monkeypatch.setattr(chat, 'ChatTokenBudget', lambda *a: SimpleNamespace(fits=lambda *a, **kw: True))
    user = User(user_id='location-user')
    answer = chat.chat(ChatRequest(query='disability status', mode=mode, response_mode=response_mode,
                                   use_glossary=glossary, assess_sources=False), user)
    assert len(answer.sources) == 1
    reference = answer.sources[0].location_ref
    assert reference and reference.basis == 'match'
    location = resolve_result_location(DOC, reference)
    assert location.spans and 'Disability status' in location.spans[0].quote
    saved = chat_history.get_thread(answer.session_id, user_id=user.user_id)
    assert saved['messages'][-1]['sources'][0]['location_ref'] == reference.model_dump(mode='json')


@pytest.mark.parametrize('mode', ['dense', 'bm25', 'hybrid'])
def test_search_exposes_the_same_reference_contract(monkeypatch, mode):
    from app.api import search
    setup(monkeypatch, search, False)
    response = search.search(SearchRequest(query='disability', mode=mode), User())
    assert response.hits[0].doc_id == DOC
    ref = response.hits[0].location_ref
    assert ref and ref.basis == 'match'
    assert resolve_result_location(DOC, ref).spans


def test_document_location_endpoint_validates_reference_and_deleted_documents(monkeypatch):
    import json
    from app.api import search
    from app.main import create_app
    settings = setup(monkeypatch, search, False)
    monkeypatch.setattr('app.main.get_settings', lambda: settings)
    client = TestClient(create_app())
    ref = search.search(SearchRequest(query='disability', mode='dense'), User()).hits[0].location_ref
    response = client.get(f'/api/documents/{DOC}/source-location', params={'ref': ref.model_dump_json()})
    assert response.status_code == 200
    assert response.json()['basis'] == 'match'
    chunk = client.get(f'/api/documents/{DOC}/chunks/3/source-text').json()
    assert chunk['content'] == TEXT
    assert chunk['chunk_hash'] == response.json()['chunk_hash']
    assert chunk['generation_id'] == response.json()['generation_id']
    assert client.get(f'/api/documents/{DOC}/source-location', params={'ref': '{}'}).status_code == 422
    invalid = json.loads(ref.model_dump_json())
    invalid['spans'] = [{'start': -1, 'end': 4}]
    assert client.get(f'/api/documents/{DOC}/source-location', params={'ref': json.dumps(invalid)}).status_code == 422
    from datetime import datetime, timezone
    with session_scope() as session:
        session.get(Document, DOC).deleted_at = datetime.now(timezone.utc)
    assert client.get(f'/api/documents/{DOC}/source-location', params={'ref': ref.model_dump_json()}).status_code == 404
