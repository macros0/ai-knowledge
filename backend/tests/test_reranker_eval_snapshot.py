import json
from copy import deepcopy
from types import SimpleNamespace

import pytest


def case(**updates):
    return dict(id='c1', family_id='f1', split='development', group='codes', query='ЭЛН',
                request={'mode': 'bm25', 'response_mode': 'fast', 'search_depth': 2,
                         'use_glossary': False}, judgments={}, mandatory_sources=[],
                required_facts=[], forbidden_claims=[], answerable=True, **updates)


def test_family_cannot_cross_splits(tmp_path):
    from test_scripts.reranker_eval.cases import load_cases
    first = case()
    second = {**case(), 'id': 'c2', 'split': 'holdout'}
    path = tmp_path / 'cases.json'
    path.write_text(json.dumps([first, second]), encoding='utf-8')
    with pytest.raises(ValueError, match='family'):
        load_cases(path)


def test_unjudged_pool_is_rejected():
    from test_scripts.reranker_eval.cases import validate_judgments
    with pytest.raises(ValueError, match='unjudged'):
        validate_judgments(case(), ['a'])
    validate_judgments({**case(), 'judgments': {'a': 2}}, ['a'])


def test_source_key_requires_canonical_evidence_and_preserves_distinctions():
    from test_scripts.reranker_eval.snapshot import source_key
    base = {'title': 'same', 'doc_id': 'd', 'content': 'x', 'kind': 'concept',
            '_evidence': {'doc_id': 'd', 'point_type': 'concept', 'slug': 'a',
                          'generation_id': 'g', 'digest': 'abc', 'start': 0, 'length': 1}}
    other = deepcopy(base)
    other['_evidence']['slug'] = 'b'
    assert source_key(base) != source_key(other)
    with pytest.raises(ValueError, match='identity'):
        source_key({'title': 'same', 'doc_id': 'd', 'content': 'x'})


def test_generation_change_invalidates_snapshot(monkeypatch):
    from test_scripts.reranker_eval import snapshot
    states = iter(['a', 'b'])
    monkeypatch.setattr(snapshot, 'corpus_fingerprint', lambda: next(states))
    monkeypatch.setattr(snapshot, '_capture', lambda *a, **k: {'final': []})
    with pytest.raises(ValueError, match='changed'):
        snapshot.capture_case(case(), user=SimpleNamespace(), settings=SimpleNamespace())


def test_capture_matches_api_final_order(monkeypatch):
    from app.api import chat as api
    from app.auth.models import User
    from app.config import Settings
    from app.models.schemas import ChatRequest
    from test_scripts.reranker_eval.snapshot import capture_case
    from tests.test_chat_result_depth import corpus
    client, _ = corpus(monkeypatch, groups=4)
    request = {**case()['request'], 'query': 'ЭЛН'}
    settings = Settings(_env_file=None, glossary_query_expansion_enabled=False)
    captured = capture_case(case(), user=User(), settings=settings)
    response = client.post('/api/chat', json=request)
    assert response.status_code == 200
    assert [b['source_slug'] for b in captured['final']] == [
        s['source_slug'] for s in response.json()['sources']]
    assert len(captured['raw']) >= len(captured['final'])
    assert ChatRequest(**request).search_depth == 2
    assert api._get_vector_store() is not None


def test_scope_mail_locale_are_preserved(monkeypatch):
    from app.auth.models import User
    from app.config import Settings
    from test_scripts.reranker_eval.snapshot import capture_case
    from tests.test_chat_result_depth import corpus
    _, calls = corpus(monkeypatch, groups=4)
    scoped = case()
    scoped['request'].update(search_doc_ids=['depth-doc'], mail_mode='exclude',
                             source_locales=['ru'])
    result = capture_case(scoped, user=User(), settings=Settings(_env_file=None))
    assert result['request']['search_doc_ids'] == ['depth-doc']
    assert result['request']['mail_mode'] == 'exclude'
    assert result['request']['source_locales'] == ['ru']
    assert all(b['doc_id'] == 'depth-doc' for b in result['final'])
    assert calls
def test_working_tree_fingerprint_hashes_file_contents(tmp_path):
    import subprocess
    from test_scripts.reranker_eval.snapshot import working_tree_fingerprint
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    code = tmp_path / 'code.py'
    code.write_text('version = 1')
    before = working_tree_fingerprint(tmp_path)
    code.write_text('version = 2')
    assert before != working_tree_fingerprint(tmp_path)
