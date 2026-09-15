"""Тесты батчинга upsert и классификации ошибок Qdrant (инцидент 03.09.2026).

Проверяют:
  - index_concepts/index_chunks режут большой набор точек на батчи <= 256
    (монолитный upsert 5667 концептов давал ~120 МБ JSON → 400 от Qdrant);
  - сбойный батч после ретраев не убивает остальные, но index_* поднимает
    VectorStoreError → пайплайн ставит paused (resume доотправит);
  - _qdrant_call различает 4xx живого Qdrant (дефект запроса, код и текст
    в сообщении) и сетевые сбои («недоступен»).
"""
from __future__ import annotations

import httpx
import pytest
import grpc
from qdrant_client.http.exceptions import UnexpectedResponse

import app.services.vector_store as vs_module
from app.config import Settings
from app.models.schemas import OkfDocument
from app.services.errors import VectorStoreError
from app.services.vector_store import VectorStore, concept_point_id


class _FakeQdrant:
    """Фейк: батчи, чей первый point_id в fail_ids, ВСЕГДА падают (любой ретрай).

    Прочие батчи проходят с первого раза. Позволяет моделировать
    перманентный сбой одного батча среди успешных остальных.
    """

    def __init__(self, fail_ids: set[str] | None = None, error: Exception | None = None):
        self.calls: list[list] = []
        self.fail_ids = fail_ids or set()
        self.error = error or httpx.ConnectError("down")

    def upsert(self, *, collection_name, points):
        self.calls.append(list(points))
        if str(points[0].id) in self.fail_ids:
            raise self.error


def _okf_docs(n: int) -> list[OkfDocument]:
    return [
        OkfDocument(
            filepath=f"/bundle/doc/concept-{i}.md",
            metadata={"title": f"Концепт {i}", "type": "concept", "tags": [], "relations": []},
            content=f"текст концепта {i}",
            markdown="",
        )
        for i in range(n)
    ]


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, embedding_dimensions=8)
    monkeypatch.setattr(vs_module, "get_settings", lambda: settings)
    # Без sleeps в ретраях — тесты мгновенны.
    monkeypatch.setattr(vs_module.time, "sleep", lambda s: None)
    vs = VectorStore()
    return vs


class TestUpsertBatching:
    def test_large_set_split_into_batches(self, store, monkeypatch):
        fake = _FakeQdrant()
        monkeypatch.setattr(store, "client", fake)

        docs = _okf_docs(600)
        vectors = [[0.1] * 8 for _ in docs]
        ids = store.index_concepts("a1b2c3d4e5f60718", docs, vectors)

        assert len(ids) == 600
        assert [len(c) for c in fake.calls] == [256, 256, 88]
        assert sum(len(c) for c in fake.calls) == 600

    def test_single_batch_for_small_set(self, store, monkeypatch):
        fake = _FakeQdrant()
        monkeypatch.setattr(store, "client", fake)

        docs = _okf_docs(3)
        vectors = [[0.1] * 8 for _ in docs]
        store.index_concepts("a1b2c3d4e5f60718", docs, vectors)

        assert len(fake.calls) == 1

    def test_failed_batches_raise_after_retrying(self, store, monkeypatch):
        """Сетевой сбой ретраится (3 попытки), затем батч помечается упавшим;
        остальные уходят; в конце index_concepts поднимает VectorStoreError
        с итогами — документ встанет в paused, resume доотправит."""
        docs = _okf_docs(300)
        vectors = [[0.1] * 8 for _ in docs]
        from pathlib import Path

        first_batch_id = concept_point_id("a1b2c3d4e5f60718", Path(docs[0].filepath).stem)
        fake = _FakeQdrant(fail_ids={first_batch_id}, error=httpx.ConnectError("down"))
        monkeypatch.setattr(store, "client", fake)

        with pytest.raises(VectorStoreError, match="44 из 300"):
            store.index_concepts("a1b2c3d4e5f60718", docs, vectors)
        # Первый батч — 3 попытки (сеть ретраится), второй — 1: всего 4 вызова.
        assert len(fake.calls) == 4

    def test_validation_error_fails_fast_without_retry(self, store, monkeypatch):
        """422 живого Qdrant — дефект данных, ретраи бессмысленны."""
        docs = _okf_docs(300)
        vectors = [[0.1] * 8 for _ in docs]
        from pathlib import Path

        first_batch_id = concept_point_id("a1b2c3d4e5f60718", Path(docs[0].filepath).stem)
        err = UnexpectedResponse(
            status_code=422,
            reason_phrase="Unprocessable Entity",
            content=b'{"status":{"error":"must be unique"}}',
            headers=httpx.Headers(),
        )
        fake = _FakeQdrant(fail_ids={first_batch_id}, error=err)
        monkeypatch.setattr(store, "client", fake)

        with pytest.raises(VectorStoreError, match="44 из 300"):
            store.index_concepts("a1b2c3d4e5f60718", docs, vectors)
        # Первый батч — 1 попытка (4xx не ретраится), второй — 1.
        assert len(fake.calls) == 2

    def test_server_error_5xx_is_retried(self, store, monkeypatch):
        """503 (storage not ready при оптимизации Qdrant) — ретраится."""
        err = UnexpectedResponse(
            status_code=503,
            reason_phrase="Service Unavailable",
            content=b"storage not ready",
            headers=httpx.Headers(),
        )
        calls: list[list] = []

        def flaky_upsert(*, collection_name, points):
            calls.append(list(points))
            if len(calls) == 1:
                raise err

        monkeypatch.setattr(store, "client", type("C", (), {"upsert": staticmethod(flaky_upsert)})())

        docs = _okf_docs(10)
        vectors = [[0.1] * 8 for _ in docs]
        ids = store.index_concepts("a1b2c3d4e5f60718", docs, vectors)
        assert len(ids) == 10
        # 1 батч: попытка 1 (503) + попытка 2 (успех) = 2 вызова.
        assert len(calls) == 2

    def test_index_chunks_batches(self, store, monkeypatch):
        fake = _FakeQdrant()
        monkeypatch.setattr(store, "client", fake)

        texts = [f"Текст чанка {i}" for i in range(300)]
        vectors = [[0.1] * 8 for _ in texts]
        ids = store.index_chunks("a1b2c3d4e5f60718", "f.docx", texts, [], vectors)

        assert len(ids) == 300
        assert [len(c) for c in fake.calls] == [256, 44]


class TestQdrantCallClassification:
    def test_qdrant_disk_full_response_keeps_storage_full_classification(self):
        """A Qdrant volume full response must pause indexing rather than look unavailable."""
        from app.services.storage import StorageFullError
        from app.services.vector_store import _qdrant_call

        err = UnexpectedResponse(
            status_code=500,
            reason_phrase="Internal Server Error",
            content=b'{"status":{"error":"No space left on device"}}',
            headers=httpx.Headers(),
        )

        with pytest.raises(StorageFullError):
            _qdrant_call(lambda: (_ for _ in ()).throw(err))

    def test_qdrant_grpc_disk_full_keeps_storage_full_classification(self):
        """The production gRPC path must stop immediately and preserve ENOSPC."""
        from app.services.storage import StorageFullError
        from app.services.vector_store import _qdrant_call

        class DiskFullRpc(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.RESOURCE_EXHAUSTED

            def details(self):
                return "No space left on device"

            def __str__(self):
                return "RESOURCE_EXHAUSTED: No space left on device"

        with pytest.raises(StorageFullError):
            _qdrant_call(lambda: (_ for _ in ()).throw(DiskFullRpc()))

    def test_unexpected_response_becomes_http_error_message(self):
        from app.services.vector_store import _qdrant_call

        err = UnexpectedResponse(
            status_code=422,
            reason_phrase="Unprocessable Entity",
            content=b'{"status":{"error":"indices: must be unique"}}',
            headers=httpx.Headers(),
        )

        def boom():
            raise err

        with pytest.raises(VectorStoreError) as exc_info:
            _qdrant_call(boom)
        msg = exc_info.value.user_message
        assert "HTTP 422" in msg, msg
        assert "must be unique" in msg, msg
        assert "недоступна" not in msg, "живой Qdrant не «недоступен»"

    def test_network_error_keeps_unavailable_message(self, tmp_path, monkeypatch):
        from app.services.vector_store import _qdrant_call

        settings = Settings(_env_file=None, data_dir=tmp_path, qdrant_url="http://localhost:16333")
        monkeypatch.setattr(vs_module, "get_settings", lambda: settings)

        def boom():
            raise httpx.ConnectError("connection refused")

        with pytest.raises(VectorStoreError) as exc_info:
            _qdrant_call(boom)
        msg = exc_info.value.user_message
        assert "недоступна" in msg
        assert "16333" in msg
