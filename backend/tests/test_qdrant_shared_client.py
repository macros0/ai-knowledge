"""VectorStore() не плодит QdrantClient: один клиент на параметры подключения."""
from types import SimpleNamespace

import pytest

from app.services import vector_store
from app.services.vector_store import VectorStore, close_shared_qdrant_clients, shared_qdrant_client


class FakeClient:
    created = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._client = SimpleNamespace(closed=False)
        FakeClient.created.append(self)

    def close(self):
        self._client.closed = True


@pytest.fixture(autouse=True)
def fake_qdrant(monkeypatch):
    FakeClient.created = []
    monkeypatch.setattr(vector_store, "QdrantClient", FakeClient)
    monkeypatch.setattr(vector_store, "_shared_clients", {})
    yield
    close_shared_qdrant_clients()


def _settings(url="http://qdrant:6333", **extra):
    return SimpleNamespace(qdrant_url=url, qdrant_api_key=None, qdrant_prefer_grpc=False,
                           qdrant_grpc_port=None, qdrant_collection="c", **extra)


def test_instances_with_same_connection_share_one_client(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(vector_store, "get_settings", lambda: settings)

    first, second = VectorStore(), VectorStore()

    assert first.client is second.client
    assert len(FakeClient.created) == 1
    assert FakeClient.created[0].kwargs["grpc_port"] == 6334


def test_closed_shared_client_is_recreated(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(vector_store, "get_settings", lambda: settings)
    store = VectorStore()
    old = store.client

    old.close()

    assert store.client is not old
    assert not store.client._client.closed


def test_assigned_client_overrides_shared_one(monkeypatch):
    monkeypatch.setattr(vector_store, "get_settings", _settings)
    store = VectorStore()
    own = object()
    store.client = own
    assert store.client is own
    assert FakeClient.created == []


def test_different_connections_get_different_clients_and_shutdown_closes_all():
    a = shared_qdrant_client(_settings("http://a:6333"))
    b = shared_qdrant_client(_settings("http://b:6333"))
    assert a is not b

    close_shared_qdrant_clients()

    assert a._client.closed and b._client.closed
