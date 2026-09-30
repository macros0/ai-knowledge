from types import SimpleNamespace

from qdrant_client.http import models as qm

from app.services.vector_store import VectorStore


def test_vector_store_can_prefer_configured_qdrant_grpc_transport(monkeypatch):
    import app.services.vector_store as vector_store_module

    captured = {}

    class _Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    settings = SimpleNamespace(
        qdrant_url="http://127.0.0.1:16333",
        qdrant_api_key=None,
        qdrant_prefer_grpc=True,
        qdrant_grpc_port=16334,
    )
    monkeypatch.setattr(vector_store_module, "QdrantClient", _Client)
    monkeypatch.setattr(vector_store_module, "get_settings", lambda: settings)
    monkeypatch.setattr(vector_store_module, "_shared_clients", {})

    VectorStore().client  # клиент создаётся при первом обращении и общий для процесса

    assert captured["prefer_grpc"] is True
    assert captured["grpc_port"] == 16334


def test_vector_store_keeps_http_transport_when_grpc_is_not_configured(monkeypatch):
    import app.services.vector_store as vector_store_module

    captured = {}

    class _Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    settings = SimpleNamespace(
        qdrant_url="http://qdrant.internal:6333",
        qdrant_api_key="secret",
    )
    monkeypatch.setattr(vector_store_module, "QdrantClient", _Client)
    monkeypatch.setattr(vector_store_module, "get_settings", lambda: settings)
    monkeypatch.setattr(vector_store_module, "_shared_clients", {})

    VectorStore().client  # клиент создаётся при первом обращении и общий для процесса

    assert captured["prefer_grpc"] is False
    assert captured["grpc_port"] == 6334


class _QueryClient:
    def __init__(self):
        self.kwargs = None

    def query_points(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            points=[SimpleNamespace(id="point-1", score=0.7, payload={"doc_id": "doc-1"})]
        )


def test_bm25_search_requests_only_retrieval_payload_fields():
    store = VectorStore.__new__(VectorStore)
    store.settings = SimpleNamespace(qdrant_collection="test")
    store.client = _QueryClient()

    hits = store.search_bm25(
        qm.SparseVector(indices=[1], values=[1.0]),
        query_filter=None,
        top_k=5,
    )

    selector = store.client.kwargs["with_payload"]
    assert isinstance(selector, qm.PayloadSelectorInclude)
    assert "content" not in selector.include
    assert "title" in selector.include
    assert "relations" in selector.include
    assert hits[0].point_id == "point-1"
    assert hits[0].payload == {"doc_id": "doc-1"}


def test_dense_search_uses_the_same_payload_projection():
    store = VectorStore.__new__(VectorStore)
    store.settings = SimpleNamespace(qdrant_collection="test")
    store.client = _QueryClient()

    hits = store.search_dense([0.1], query_filter=None, top_k=5)

    selector = store.client.kwargs["with_payload"]
    assert isinstance(selector, qm.PayloadSelectorInclude)
    assert "content" not in selector.include
    assert hits[0].point_id == "point-1"


import pytest
from app.config import Settings
from app.services.fusion import Hit


@pytest.mark.parametrize('depth', [10, 100, 500])
def test_request_depth_controls_each_branch_and_fused_results_without_global_changes(depth):
    store = VectorStore.__new__(VectorStore)
    store.settings = Settings(_env_file=None, search_per_branch_top_k=40,
                              search_graph_expansion_enabled=False)
    calls = []

    def retrieve(vec, query_filter, top_k):
        calls.append(top_k)
        return [Hit(str(i), 1.0, {}, i) for i in range(top_k)]

    store.search_dense = retrieve
    store.search_bm25 = retrieve
    status = {}
    hits = store.search_composite(
        dense_vec=[1], sparse_vec=qm.SparseVector(indices=[1], values=[1]),
        tags=None, branches={'dense', 'bm25'}, top_k=depth,
        per_branch_top_k=depth, retrieval_status=status,
    )
    assert calls == [depth, depth]
    assert len(hits) == depth
    assert status['limit_reached'] is True
    assert store.settings.search_per_branch_top_k == 40


def test_branch_limit_is_reported_even_when_deduplication_leaves_a_shorter_list():
    store = VectorStore.__new__(VectorStore)
    store.settings = Settings(_env_file=None, search_graph_expansion_enabled=False)
    store.search_dense = lambda *args: [Hit('same', 1.0, {}, i) for i in range(3)]
    status = {}
    hits = store.search_composite(dense_vec=[1], sparse_vec=None, tags=None,
                                  branches={'dense'}, top_k=3, per_branch_top_k=3,
                                  retrieval_status=status)
    assert len(hits) == 1
    assert status['limit_reached'] is True


def test_short_search_does_not_report_a_limit():
    store = VectorStore.__new__(VectorStore)
    store.settings = Settings(_env_file=None, search_graph_expansion_enabled=False)
    store.search_dense = lambda *args: [Hit('a', 1.0, {})]
    status = {}
    store.search_composite(dense_vec=[1], sparse_vec=None, tags=None,
                           branches={'dense'}, top_k=100, per_branch_top_k=100,
                           retrieval_status=status)
    assert status['limit_reached'] is False


def test_request_depth_controls_graph_expansion():
    store = VectorStore.__new__(VectorStore)
    store.settings = Settings(_env_file=None, search_graph_expansion_enabled=True)
    store.search_dense = lambda *args: [Hit('a', 1.0, {'point_type': 'concept', 'relations': ['b']})]
    calls = []

    def scroll(**kwargs):
        calls.append(kwargs['limit'])
        return [], None

    store.client = SimpleNamespace(scroll=scroll)
    store.search_composite(dense_vec=[1], sparse_vec=None, tags=None,
                           branches={'dense'}, top_k=100, per_branch_top_k=100)
    assert calls == [100]


@pytest.mark.parametrize('mail_mode,scope', [('all', None), ('exclude', 'document'), ('only', 'mail')])
@pytest.mark.parametrize('branches', [{'dense'}, {'bm25'}, {'dense', 'bm25'}])
def test_every_branch_uses_same_mail_tags_locale_filter(mail_mode, scope, branches):
    captured = {}
    store = VectorStore.__new__(VectorStore)
    store.settings = Settings(_env_file=None, search_graph_expansion_enabled=True)
    seed = Hit('seed', 1.0, {'point_type': 'concept', 'relations': ['neighbor']})
    def dense(vec, query_filter, top_k):
        captured['dense'] = query_filter
        return [seed]
    def sparse(vec, query_filter, top_k):
        captured['bm25'] = query_filter
        return [seed]
    def scroll(**kwargs):
        captured['graph'] = kwargs['scroll_filter']
        assert kwargs['limit'] == store.settings.search_per_branch_top_k
        return [], None
    store.search_dense = dense
    store.search_bm25 = sparse
    store.client = SimpleNamespace(scroll=scroll)
    store.search_composite(dense_vec=[1], sparse_vec=qm.SparseVector(indices=[1], values=[1]),
                           tags=['PT'], branches=branches, top_k=10, source_locales=['ru'],
                           include_unknown_source_locale=True, mail_mode=mail_mode)
    shared = captured[next(iter(branches))]
    for branch in branches:
        assert captured[branch] is shared
    assert captured['graph'].must[0] == shared
    assert captured['graph'].must[1].key == 'slug'
    fields = {c.key: c.match.value for c in shared.must if isinstance(c, qm.FieldCondition)}
    if scope:
        assert fields == {'mail_scope': scope, 'mail_scope_version': 1}
    else:
        assert fields == {}
    assert any(isinstance(c, qm.Filter) and c.should for c in shared.must)
    assert shared.must_not


@pytest.mark.parametrize('mode', ['ONLY', None, 'invalid', False])
def test_invalid_internal_mail_mode_is_rejected_with_empty_branches(mode):
    store = VectorStore.__new__(VectorStore)
    store.settings = Settings(_env_file=None)
    with pytest.raises(ValueError):
        store.search_composite(dense_vec=None, sparse_vec=None, tags=None, branches=set(),
                               top_k=1, mail_mode=mode)


def test_retrieval_payload_selector_has_mail_scope_fields():
    from app.services.vector_store import _retrieval_payload_selector
    assert {'mail_scope', 'mail_scope_version'} <= set(_retrieval_payload_selector().include)
