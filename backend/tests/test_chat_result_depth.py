from types import SimpleNamespace

import pytest

from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services.fusion import Hit
from tests.test_chat import make_client


@pytest.fixture(autouse=True)
def isolated_admission_limits():
    from app.services.rate_limiter import get_rate_limiter
    get_rate_limiter().reset()
    yield
    get_rate_limiter().reset()


def corpus(monkeypatch, *, groups=8, noise=0):
    from app.api import chat as api

    hits = []
    with session_scope() as session:
        session.add(Document(id='depth-doc', filename='depth.docx'))
        for index in range(groups):
            text = f'{"Отпуск" if index < noise else "ЭЛН"} раздел {index}'
            slug = f'part-{index}'
            session.add(OkfConcept(doc_id='depth-doc', slug=slug, title=text,
                                   content=text, chunk_index=index))
            session.add(DocumentChunk(doc_id='depth-doc', chunk_index=index,
                                      content=text, char_count=len(text)))
            for kind in ('concept', 'chunk'):
                hits.append(Hit(f'{kind}-{index}', 1. / (index + 1), dict(
                    point_type=kind, doc_id='depth-doc', slug=slug if kind == 'concept' else None,
                    title=text, chunk_index=index)))
    calls = []

    def search(**kwargs):
        calls.append(kwargs['top_k'])
        kwargs['retrieval_status']['limit_reached'] = len(hits) >= kwargs['top_k']
        return hits[:kwargs['top_k']]

    monkeypatch.setattr(api, '_get_embedder', lambda: SimpleNamespace(embed=lambda _: [0.]))
    monkeypatch.setattr(api, '_get_vector_store', lambda: SimpleNamespace(search_composite=search))
    monkeypatch.setattr(api, '_answer_mode', lambda *a, **k: 'Ответ')
    return make_client(monkeypatch), calls


@pytest.mark.parametrize('mode', ['documents', 'fast', 'full'])
@pytest.mark.parametrize('noise', [0, 2])
def test_depth_counts_final_fragments_after_merge_and_filters(monkeypatch, mode, noise):
    client, calls = corpus(monkeypatch, noise=noise)
    response = client.post('/api/chat', json={
        'query': 'ЭЛН', 'response_mode': mode, 'search_depth': 4, 'use_glossary': False,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result['sources']) == 4
    assert [source['source_slug'] for source in result['sources']] == [
        f'part-{index}' for index in range(noise, noise + 4)]
    assert calls[0] == 4 and len(calls) > 1
    assert result['search_limit_reached'] is True


def test_exhausted_search_keeps_fewer_sources_without_padding(monkeypatch):
    client, calls = corpus(monkeypatch, groups=2)
    response = client.post('/api/chat', json={
        'query': 'ЭЛН', 'response_mode': 'documents', 'search_depth': 10, 'use_glossary': False,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result['sources']) == 2
    assert result['search_limit_reached'] is False
    assert calls == [10]
