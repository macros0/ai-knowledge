"""Candidate diversity precedes fusion; ordinary queries keep their old path."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from qdrant_client import models as qm
from app.services.vector_store import VectorStore


def point(identity, score, group=None):
    payload = {'point_type': 'chunk', 'doc_id': identity, 'chunk_index': 0}
    if group is not None:
        payload.update(chunk_fragment={'version': 1}, chunk_fragment_group=group)
    return SimpleNamespace(id=identity, score=score, payload=payload)


@pytest.mark.parametrize('branch', ['dense', 'bm25'])
def test_best_per_chunk_precedes_extra_parts_with_same_budget(branch):
    store = VectorStore.__new__(VectorStore)
    store.settings = SimpleNamespace(qdrant_collection='test')
    top = [point('a1', 100, 'a'), point('a2', 99, 'a'), point('a3', 98, 'a'), point('a4', 97, 'a')]
    other = point('b1', 1, 'b')
    ordinary = point('legacy', 2)
    store.client = Mock()
    store.client.query_points.side_effect = [SimpleNamespace(points=top), SimpleNamespace(points=[ordinary])]
    store.client.query_points_groups.return_value = SimpleNamespace(groups=[
        SimpleNamespace(hits=[top[0]]), SimpleNamespace(hits=[other])])
    scope = qm.Filter(must=[qm.FieldCondition(key='doc_id', match=qm.MatchValue(value='scope'))])
    query = [1., 0.] if branch == 'dense' else qm.SparseVector(indices=[1], values=[1.])
    hits = getattr(store, 'search_' + branch)(query, scope, 4)
    assert [h.point_id for h in hits] == ['a1', 'legacy', 'b1', 'a2']
    assert [h.rank for h in hits] == [0, 1, 2, 3]
    calls = store.client.query_points.call_args_list + store.client.query_points_groups.call_args_list
    assert all(c.kwargs['limit'] == 4 for c in calls)
    assert store.client.query_points_groups.call_args.kwargs['query_filter'] == scope
    assert scope in store.client.query_points.call_args.kwargs['query_filter'].must


@pytest.mark.parametrize('branch', ['dense', 'bm25'])
def test_ordinary_search_keeps_one_query_and_original_order(branch):
    store = VectorStore.__new__(VectorStore)
    store.settings = SimpleNamespace(qdrant_collection='test')
    store.client = Mock()
    store.client.query_points.return_value = SimpleNamespace(points=[point('b', 1), point('a', 1)])
    query = [1., 0.] if branch == 'dense' else qm.SparseVector(indices=[1], values=[1.])
    hits = getattr(store, 'search_' + branch)(query, None, 2)
    assert [h.point_id for h in hits] == ['b', 'a']
    assert store.client.query_points.call_count == 1
    store.client.query_points_groups.assert_not_called()


def test_group_identity_includes_all_publication_scope_fields():
    from app.services.chunk_index_projection import chunk_fragment_group_id
    base = ('doc', 'generation', 'root', 0)
    variants = [base, ('other', *base[1:]), (base[0], 'other', *base[2:]),
                (*base[:2], 'other', 0), (*base[:3], 1), (*base[:2], None, 0)]
    assert len({chunk_fragment_group_id(*v) for v in variants}) == len(variants)
    assert chunk_fragment_group_id(*base) == chunk_fragment_group_id(*base)


def test_pre_grouping_points_remain_readable_without_claiming_diversity():
    store = VectorStore.__new__(VectorStore)
    store.settings = SimpleNamespace(qdrant_collection='test')
    old = point('old', 1, 'a')
    old.payload.pop('chunk_fragment_group')
    store.client = Mock()
    store.client.query_points.return_value = SimpleNamespace(points=[old])
    store.client.query_points_groups.return_value = SimpleNamespace(groups=[])
    assert [h.point_id for h in store.search_dense([1., 0.], None, 4)] == ['old']
    assert store.client.query_points.call_count == 1
