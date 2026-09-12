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

    VectorStore()

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

    VectorStore()

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
