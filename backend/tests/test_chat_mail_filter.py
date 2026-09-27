"""Strict chat mail modes must constrain actual context, sources and authorship."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest
from app.config import Settings
from app.db.models import Document, DocumentChunk, DocumentSource, ChatMessage
from app.db.session import session_scope
from app.models.schemas import ChatRequest
from app.services.fusion import Hit
from tests.test_chat import make_client

RU_EMPTY = 'Источники не найдены с учётом выбранных фильтров. Измените фильтры и повторите запрос.'
EN_EMPTY = 'No sources match the selected filters. Change the filters and try again.'


def _corpus():
    with session_scope() as session:
        session.add(Document(id='chat-scope', filename='archive.docx'))
        session.add_all([
            DocumentSource(doc_id='chat-scope', source_id='root', kind='document', display_name='archive.docx'),
            DocumentSource(doc_id='chat-scope', source_id='mail', parent_source_id='root', kind='mail', display_name='letter.eml'),
            DocumentSource(doc_id='chat-scope', source_id='attachment', parent_source_id='mail', kind='document', display_name='attached.docx'),
        ])
        for index, source, marker in [(0, 'root', 'DOC_MARKER'), (1, 'mail', 'MAIL_MARKER'),
                                      (2, 'attachment', 'ATTACHMENT_MARKER'), (3, None, 'UNKNOWN_MARKER')]:
            session.add(DocumentChunk(doc_id='chat-scope', chunk_index=index, source_id=source,
                                      section_title='topic', content='topic ' + marker))
    return [Hit(str(i), 1 - i / 10, dict(point_type='chunk', doc_id='chat-scope', chunk_index=i,
                                        tags=[], mail_scope='document', mail_scope_version=1)) for i in range(4)]


def _mock_api(monkeypatch, hits):
    from app.api import chat as api
    captured = dict(search=[], llm=[])
    def search(**kwargs):
        captured['search'].append(kwargs)
        return deepcopy(hits)
    def llm(system, user):
        captured['llm'].append((system, user))
        return 'Answer [1]'
    monkeypatch.setattr(api, 'get_settings', lambda: Settings(_env_file=None, glossary_query_expansion_enabled=False))
    monkeypatch.setattr(api, '_get_vector_store', lambda: SimpleNamespace(search_composite=search))
    monkeypatch.setattr(api, '_get_embedder', lambda: SimpleNamespace(embed=lambda _: [0.]))
    monkeypatch.setattr(api, '_get_llm', lambda: SimpleNamespace(chat=llm))
    return make_client(monkeypatch), captured


@pytest.mark.parametrize('mode,markers', [('all', ['DOC_MARKER', 'MAIL_MARKER', 'ATTACHMENT_MARKER', 'UNKNOWN_MARKER']),
                                         ('exclude', ['DOC_MARKER']), ('only', ['MAIL_MARKER', 'ATTACHMENT_MARKER'])])
def test_mail_mode_reaches_context_sources_and_history(monkeypatch, mode, markers):
    client, captured = _mock_api(monkeypatch, _corpus())
    response = client.post('/api/chat', json=dict(query='topic', mail_mode=mode, top_k=10, use_glossary=False))
    assert response.status_code == 200, response.text
    assert captured['search'][0]['mail_mode'] == mode
    assert len(captured['search']) == 1
    assert len(captured['llm']) == 1
    prompt = captured['llm'][0][1]
    serialized = json.dumps(response.json()['sources'])
    for marker in ['DOC_MARKER', 'MAIL_MARKER', 'ATTACHMENT_MARKER', 'UNKNOWN_MARKER']:
        assert (marker in prompt) is (marker in markers)
        assert (marker in serialized) is (marker in markers)
    assert '_canonical_verified' not in prompt and '_mail_components' not in serialized
    assert 'mail_scope' not in prompt
    with session_scope() as session:
        saved = session.query(ChatMessage).filter_by(role='assistant').one()
        assert saved.retrieval_metadata['mail_mode'] == mode
        assert saved.retrieval_metadata['schema_version'] == 1


@pytest.mark.parametrize('value', [None, 1, False, [], {}, 'invalid', 'ONLY'])
def test_invalid_mail_mode_422_before_dependencies(monkeypatch, value):
    client, captured = _mock_api(monkeypatch, [])
    response = client.post('/api/chat', json=dict(query='topic', mail_mode=value))
    assert response.status_code == 422
    assert captured['search'] == [] and captured['llm'] == []


def test_old_api_request_defaults_all():
    assert ChatRequest(query='topic').mail_mode == 'all'


@pytest.mark.parametrize('locale,answer', [('ru', RU_EMPTY), ('en', EN_EMPTY)])
@pytest.mark.parametrize('stage', ['hydration', 'final_guard'])
def test_strict_empty_after_either_gate_has_no_llm_or_all_retry(monkeypatch, locale, answer, stage):
    from app.api import chat as api
    client, captured = _mock_api(monkeypatch, _corpus()[1:2] if stage == 'hydration' else _corpus()[:1])
    if stage == 'final_guard':
        original = api.merge_and_format
        def forged(*args, **kwargs):
            blocks = original(*args, **kwargs)
            for block in blocks:
                block['_canonical_verified'] = False
            return blocks
        monkeypatch.setattr(api, 'merge_and_format', forged)
    response = client.post('/api/chat', json=dict(query='topic', mail_mode='exclude', locale=locale, use_glossary=False))
    assert response.status_code == 200, response.text
    assert response.json()['sources'] == []
    assert response.json()['answer'] == answer
    assert captured['llm'] == []
    assert [call['mail_mode'] for call in captured['search']] == ['exclude']


def test_only_attachment_context_does_not_substitute_parent_text(monkeypatch):
    client, captured = _mock_api(monkeypatch, _corpus()[2:3])
    response = client.post('/api/chat', json=dict(query='topic', mail_mode='only', use_glossary=False))
    assert response.status_code == 200
    assert 'ATTACHMENT_MARKER' in captured['llm'][0][1]
    assert 'MAIL_MARKER' not in captured['llm'][0][1]
    assert response.json()['sources'][0]['source_id'] == 'attachment'


def _verified_hit(pid, source, scope='document', kind='chunk', **extras):
    return Hit(pid, 1., dict(point_type=kind, doc_id='doc', chunk_index=0, source_id=source,
                            mail_scope=scope, generation_id='g', _canonical_verified=True,
                            content='topic ' + pid, section_title='topic', tags=[], **extras))


def test_strict_grouping_does_not_merge_different_source_ids_or_review_tags():
    from app.services.context_builder import merge_and_format
    hits = [_verified_hit('allowed', 'root'), _verified_hit('second', 'ordinary'),
            _verified_hit('forbidden', 'mail', 'mail', 'concept', tags_override=True)]
    hits[-1].payload.update(tags=['review', 'private-tag'], title='private', slug='private')
    blocks = merge_and_format(hits, Settings(_env_file=None), mail_mode='exclude')
    assert len(blocks) == 2
    assert {b['source_id'] for b in blocks} == {'root', 'ordinary'}
    assert 'forbidden' not in str(blocks) and 'private-tag' not in str(blocks)


@pytest.mark.parametrize('corruption', ['unverified', 'generation', 'scope', 'fragment', 'path'])
def test_final_guard_checks_each_component(corruption):
    from app.services.context_builder import merge_and_format, filter_mail_scope_blocks
    blocks = merge_and_format([_verified_hit('ok', 'root')], Settings(_env_file=None), mail_mode='exclude')
    block = blocks[0]
    if corruption == 'fragment':
        block['mail_fragment'] = {'content': 'FORBIDDEN'}
    elif corruption == 'path':
        block['source_path'] = [{'source_id': 'root', 'mail': True}]
    else:
        bad = dict(block['_mail_components'][0])
        bad[{'unverified': '_canonical_verified', 'generation': 'generation_id', 'scope': 'mail_scope'}[corruption]] = {
            'unverified': False, 'generation': 'new', 'scope': 'mail'}[corruption]
        block['_mail_components'].append(bad)
    assert filter_mail_scope_blocks(blocks, mail_mode='exclude') == []


def test_authorship_rechecks_generation_before_loading_text():
    from app.services.generation_store import begin_generation, mark_generation_ready, publish_generation
    from app.services.retrieval_hydration import load_visible_retrieval_hits
    from app.services.context_builder import merge_and_format
    from app.services.authorship_evidence import load_authorship_evidence
    hits = _corpus()[:1]
    with session_scope() as session:
        old = begin_generation(session, 'chat-scope').id
        mark_generation_ready(session, 'chat-scope', old)
        publish_generation(session, 'chat-scope', old)
    hits[0].payload['generation_id'] = old
    kept, _ = load_visible_retrieval_hits(hits, mail_mode='exclude')
    merged = merge_and_format(kept, Settings(_env_file=None), mail_mode='exclude')
    assert load_authorship_evidence(merged, 1000, mail_mode='exclude')
    with session_scope() as session:
        new = begin_generation(session, 'chat-scope').id
        mark_generation_ready(session, 'chat-scope', new)
        publish_generation(session, 'chat-scope', new)
        session.query(DocumentChunk).filter_by(doc_id='chat-scope', chunk_index=0).one().content = 'FOREIGN_NEW_TEXT'
    assert load_authorship_evidence(merged, 1000, mail_mode='exclude') == []


def test_only_guard_rejects_fragment_without_dedicated_identity_proof():
    from app.services.context_builder import merge_and_format, filter_mail_scope_blocks
    hits = [_verified_hit('one', 'mail', 'mail', 'concept', slug='one', title='topic'),
            _verified_hit('two', 'mail', 'mail', 'concept', slug='two', title='topic')]
    blocks = merge_and_format(hits, Settings(_env_file=None), mail_mode='only')
    blocks[0]['mail_fragment'] = {'content': 'UNVERIFIED_PARENT'}
    assert filter_mail_scope_blocks(blocks, mail_mode='only') == blocks[1:]


@pytest.mark.parametrize('mode,allowed,forbidden', [('exclude','DOC_MARKER','MAIL_MARKER'),
                                                 ('only','MAIL_MARKER','DOC_MARKER')])
def test_authorship_llm_arguments_contain_only_allowed_original_fragments(monkeypatch, mode, allowed, forbidden):
    client, captured = _mock_api(monkeypatch, _corpus())
    response = client.post('/api/chat', json=dict(query='Who wrote topic?', mail_mode=mode, use_glossary=False))
    assert response.status_code == 200, response.text
    assert captured['llm']
    sources = json.loads(captured['llm'][0][1])['sources']
    assert allowed in str(sources) and forbidden not in str(sources)
    assert 'UNKNOWN_MARKER' not in str(sources)


@pytest.mark.parametrize('change', ['deleted', 'source', 'scope'])
def test_authorship_rechecks_visibility_source_and_scope(change):
    from datetime import datetime, timezone
    from app.services.retrieval_hydration import load_visible_retrieval_hits
    from app.services.context_builder import merge_and_format
    from app.services.authorship_evidence import load_authorship_evidence
    hits = _corpus()[:1]
    kept, _ = load_visible_retrieval_hits(hits, mail_mode='exclude')
    merged = merge_and_format(kept, Settings(_env_file=None), mail_mode='exclude')
    with session_scope() as session:
        if change == 'deleted':
            session.get(Document, 'chat-scope').deleted_at = datetime.now(timezone.utc)
        elif change == 'source':
            session.query(DocumentChunk).filter_by(doc_id='chat-scope', chunk_index=0).one().source_id = 'mail'
        else:
            session.query(DocumentSource).filter_by(doc_id='chat-scope', source_id='root').one().kind = 'mail'
    assert load_authorship_evidence(merged, 1000, mail_mode='exclude') == []
