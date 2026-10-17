"""Reuse pure derived work without weakening per-hit provenance or sharing payloads."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.services import table_retrieval as tr
from app.services.fusion import Hit

GEN = 'a' * 32
TABLE = 'b' * 32
REF = {'generation_id': GEN, 'source_id': 'root', 'table_id': TABLE,
       'row_number': 2, 'columns': [1, 2]}


def hydrate(monkeypatch, sources=('root', 'root'), generations=None):
    table = SimpleNamespace(doc_id='doc', generation_id=GEN, table_id=TABLE,
        source_id='root', quality={'complete': True},
        descriptor={'columns': [{'index': 1, 'header_path': ['Code']},
                                {'index': 2, 'header_path': ['Value']}], 'header_rows': [1]})
    class Session:
        def scalars(self, statement):
            return [table]
    encoded = json.dumps([REF])
    values = {('doc', 'concept', str(i)): SimpleNamespace(doc_id='doc', source_id=source,
        content='canonical', table_refs=encoded, table_refs_json=True)
        for i, source in enumerate(sources)}
    hits = [Hit(str(i), 1, {'point_type': 'concept', 'doc_id': 'doc', 'slug': str(i)})
            for i in range(len(sources))]
    monkeypatch.setattr(tr, '_read_identifier_rows', lambda session, batches: {})
    return tr.hydrate_table_fragments(hits, {'session': Session(),
        'active_generations': generations or {'doc': GEN}, 'canonical_values': values,
        'query': 'ordinary text'})


def test_identical_encoded_refs_validate_once_per_request_with_independent_output(monkeypatch):
    original = tr._validated_table_refs
    calls = []
    def validate(refs, **kwargs):
        calls.append(refs)
        return original(refs, **kwargs)
    monkeypatch.setattr(tr, '_validated_table_refs', validate)
    hits = hydrate(monkeypatch)
    assert len(hits) == 2 and len(calls) == 1
    expected = deepcopy(hits[1].payload['table_refs'])
    hits[0].payload['table_refs'][0]['columns'].append(99)
    assert hits[1].payload['table_refs'] == expected
    next_hits = hydrate(monkeypatch)
    assert len(calls) == 2 and next_hits[0].payload['table_refs'] == expected


@pytest.mark.parametrize('sources,generations,expected', [
    (('root', 'foreign'), None, ['0']),
    (('root', 'root'), {'doc': 'c' * 32}, []),
])
def test_reused_refs_still_validate_each_hits_canonical_source_and_generation(monkeypatch, sources, generations, expected):
    assert [hit.point_id for hit in hydrate(monkeypatch, sources, generations)] == expected


def test_field_tokens_reuse_pure_labels_and_do_not_share_mutable_results(monkeypatch):
    tr._cached_effective_field_tokens.cache_clear()
    original = tr._label_words
    calls = []
    def words(labels):
        calls.append(tuple(labels))
        return original(labels)
    monkeypatch.setattr(tr, '_label_words', words)
    paths = {1: ['Group', 'Code'], 2: ['Group', 'Code BE']}
    try:
        first = tr._effective_field_tokens(paths)
        assert first == {1: {'code'}, 2: {'be'}}
        first[1].add('forged')
        assert tr._effective_field_tokens(deepcopy(paths)) == {1: {'code'}, 2: {'be'}}
        assert len(calls) == 2
        changed = {1: ['Other', 'Code'], 2: ['Group', 'Code BE']}
        assert tr._effective_field_tokens(changed)[2] == {'code', 'be'}
        assert len(calls) == 4
    finally:
        tr._cached_effective_field_tokens.cache_clear()


@pytest.mark.parametrize('paths', [
    {i: ['Code'] for i in range(513)},
    {1: ['x' * 16385]},
    {1: [''] * 1025},
])
def test_oversized_header_metadata_does_not_enter_cache(paths):
    tr._cached_effective_field_tokens.cache_clear()
    try:
        tr._effective_field_tokens(paths)
        assert tr._cached_effective_field_tokens.cache_info().currsize == 0
    finally:
        tr._cached_effective_field_tokens.cache_clear()


def test_header_metadata_cache_has_a_fixed_entry_bound():
    tr._cached_effective_field_tokens.cache_clear()
    try:
        for i in range(70):
            tr._effective_field_tokens({1: [f'Field{i}']})
        info = tr._cached_effective_field_tokens.cache_info()
        assert info.maxsize == 64 and info.currsize == 64
    finally:
        tr._cached_effective_field_tokens.cache_clear()
