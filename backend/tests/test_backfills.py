"""Тесты backfill-функций VectorStore (backfill_sparse / backfill_chunks).

Фейковый Qdrant-клиент: scroll отдаёт подготовленные записи, update_vectors/
upsert — записывают вызовы. Реальная БД (SQLite per-test из conftest) — источник
истины (okf_concepts / document_chunks, Этап 2b): backfill читает БД, а не FS.
"""
from __future__ import annotations


import httpx

from app.config import Settings
from app.services.sparse import to_sparse_vector
from app.services.vector_store import SPARSE_VECTOR_NAME, VectorStore, chunk_point_id, concept_point_id


def _matches(rec, scroll_filter) -> bool:
    """Минимальная модель серверной фильтрации: has_vector и point_type."""
    if scroll_filter is None:
        return True
    for cond in getattr(scroll_filter, "must_not", None) or []:
        name = getattr(cond, "has_vector", None)
        if name is not None and name in (rec.vector or {}):
            return False
    for cond in getattr(scroll_filter, "must", None) or []:
        key = getattr(cond, "key", None)
        match = getattr(cond, "match", None)
        if key is not None and match is not None:
            if (rec.payload or {}).get(key) != getattr(match, "value", None):
                return False
    return True


class _Rec:
    def __init__(self, id, vector=None, payload=None):
        self.id = id
        self.vector = vector
        self.payload = payload or {}


class FakeQdrant:
    """Фейковый Qdrant, моделирующий серверную фильтрацию scroll.

    Фильтр обязателен к моделированию: backfill_sparse полагается на то, что
    точки с уже построенным sparse отсеивает СЕРВЕР (must_not has_vector), а не
    клиент по выкачанным векторам. Фейк, игнорирующий фильтр, показывал бы
    зелёные тесты при неработающей фильтрации.
    """

    def __init__(self, records):
        self.records = records
        self.updated_vectors: list = []
        self.upserted: list = []
        self.scroll_calls: list[dict] = []
        self.payload_updates: list = []

    def scroll(
        self, *, collection_name, limit, with_payload, with_vectors, offset=None, scroll_filter=None
    ):
        self.scroll_calls.append({"with_vectors": with_vectors, "with_payload": with_payload})
        return [r for r in self.records if _matches(r, scroll_filter)], None

    def update_vectors(self, *, collection_name, points):
        self.updated_vectors.extend(points)

    def upsert(self, *, collection_name, points):
        self.upserted.extend(points)

    def set_payload(self, *, collection_name, payload, points):
        self.payload_updates.append((payload, list(points)))


class FakeEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []

    def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [[0.0] * 8 for _ in texts]


def _vs(tmp_path, monkeypatch, records) -> tuple[VectorStore, FakeQdrant]:
    settings = Settings(_env_file=None, data_dir=tmp_path, embedding_dimensions=8)
    monkeypatch.setattr("app.services.vector_store.get_settings", lambda: settings)
    vs = VectorStore()
    fake = FakeQdrant(records)
    monkeypatch.setattr(vs, "client", fake)
    return vs, fake


def _create_db_concept(
    doc_id: str = "a1b2c3d4e5f60718",
    slug: str = "concept",
    title: str = "Концепт 12410",
    content: str = "Тело концепта с деталями.",
) -> None:
    from app.db.models import Document, OkfConcept
    from app.db.session import session_scope

    with session_scope() as s:
        s.add(Document(id=doc_id, filename="report.docx", content_type="doc", size=1))
        s.add(OkfConcept(doc_id=doc_id, slug=slug, title=title, content=content))


def _concept_point_id(settings, doc_id: str, slug: str) -> str:
    return concept_point_id(doc_id, slug)


class TestBackfillSparse:
    def test_skips_points_with_existing_sparse(self, tmp_path, monkeypatch):
        settings = Settings(_env_file=None, data_dir=tmp_path)
        _create_db_concept()
        point_id = _concept_point_id(settings, "a1b2c3d4e5f60718", "concept")
        vs, fake = _vs(
            tmp_path, monkeypatch, records=[_Rec(point_id, vector={"": [0.1] * 8, SPARSE_VECTOR_NAME: None})]
        )

        assert vs.backfill_sparse() == 0
        assert fake.updated_vectors == []

    def test_backfills_missing_sparse_from_title_and_content_only(self, tmp_path, monkeypatch):
        """Sparse строится по формуле индексации (title + content), без frontmatter."""
        settings = Settings(_env_file=None, data_dir=tmp_path)
        _create_db_concept()
        point_id = _concept_point_id(settings, "a1b2c3d4e5f60718", "concept")
        vs, fake = _vs(tmp_path, monkeypatch, records=[_Rec(point_id, vector={"": [0.1] * 8})])

        assert vs.backfill_sparse() == 1
        assert len(fake.updated_vectors) == 1
        got = fake.updated_vectors[0].vector[SPARSE_VECTOR_NAME]
        expected = to_sparse_vector("Концепт 12410\nТело концепта с деталями.")
        assert got.indices == expected.indices

    def test_force_recomputes_even_with_sparse(self, tmp_path, monkeypatch):
        settings = Settings(_env_file=None, data_dir=tmp_path)
        _create_db_concept()
        point_id = _concept_point_id(settings, "a1b2c3d4e5f60718", "concept")
        vs, fake = _vs(
            tmp_path,
            monkeypatch,
            records=[_Rec(point_id, vector={"": [0.1] * 8, SPARSE_VECTOR_NAME: None})],
        )

        assert vs.backfill_sparse(force=True) == 1
        assert len(fake.updated_vectors) == 1

    def test_skips_points_not_in_db(self, tmp_path, monkeypatch):
        _create_db_concept()  # концепт в БД есть, но точки в Qdrant нет
        vs, fake = _vs(tmp_path, monkeypatch, records=[_Rec("orphan-point", vector={})])

        assert vs.backfill_sparse() == 0
        assert fake.updated_vectors == []


class TestBackfillScanCost:
    """Стоимость прохода на старте: бэкфиллы не должны выкачивать коллекцию.

    Три бэкфилла выполняются при каждом запуске процесса (main.lifespan), в том
    числе при перезапуске воркера. Полный scroll с векторами/payload превращал
    старт в скачивание всего корпуса.
    """

    def test_sparse_does_not_download_vectors(self, tmp_path, monkeypatch):
        _create_db_concept()
        point_id = concept_point_id("a1b2c3d4e5f60718", "concept")
        vs, fake = _vs(tmp_path, monkeypatch, records=[_Rec(point_id, vector={"": [0.1] * 8})])

        vs.backfill_sparse()

        assert fake.scroll_calls, "scroll не вызывался"
        assert all(c["with_vectors"] is False for c in fake.scroll_calls), (
            "векторы выкачивать нельзя: наличие sparse определяет фильтр Qdrant"
        )

    def test_sparse_force_also_does_not_download_vectors(self, tmp_path, monkeypatch):
        _create_db_concept()
        point_id = concept_point_id("a1b2c3d4e5f60718", "concept")
        vs, fake = _vs(tmp_path, monkeypatch, records=[_Rec(point_id, vector={"": [0.1] * 8})])

        vs.backfill_sparse(force=True)

        assert all(c["with_vectors"] is False for c in fake.scroll_calls)

    def test_sparse_asks_qdrant_only_for_points_without_sparse(self, tmp_path, monkeypatch):
        """Фильтр передаётся серверу: на прогретой коллекции работы нет."""
        _create_db_concept()
        point_id = concept_point_id("a1b2c3d4e5f60718", "concept")
        # У точки sparse уже есть — фейк отсеет её ровно так же, как сервер.
        vs, fake = _vs(
            tmp_path,
            monkeypatch,
            records=[_Rec(point_id, vector={"": [0.1] * 8, SPARSE_VECTOR_NAME: None})],
        )

        assert vs.backfill_sparse() == 0
        assert fake.updated_vectors == []

    def test_relations_requests_only_relations_key(self, tmp_path, monkeypatch):
        """payload концепта содержит content до 4000 символов — тянуть его нельзя."""
        from app.db.models import Document, OkfConcept
        from app.db.session import session_scope

        doc_id = "a1b2c3d4e5f60718"
        with session_scope() as s:
            s.add(Document(id=doc_id, filename="a.docx", content_type="doc", size=1))
            s.add(
                OkfConcept(
                    doc_id=doc_id, slug="c1", title="T", content="body", relations=["Другой"]
                )
            )
        point_id = concept_point_id(doc_id, "c1")
        vs, fake = _vs(
            tmp_path,
            monkeypatch,
            records=[_Rec(point_id, vector={}, payload={"relations": []})],
        )

        assert vs.backfill_relations() == 1
        assert fake.scroll_calls, "scroll не вызывался"
        assert all(c["with_payload"] == ["relations"] for c in fake.scroll_calls), (
            f"payload запрошен целиком: {[c['with_payload'] for c in fake.scroll_calls]}"
        )


class TestBackfillChunks:
    def _create_chunks(self, doc_id: str, n: int) -> None:
        from app.db.models import Document, DocumentChunk
        from app.db.session import session_scope

        with session_scope() as s:
            if s.get(Document, doc_id) is None:
                s.add(Document(id=doc_id, filename="a.pdf", content_type="application/pdf", size=1))
            for i in range(n):
                s.add(
                    DocumentChunk(
                        doc_id=doc_id,
                        chunk_index=i,
                        content=f"Текст чанка {i}.\n",
                        char_count=len(f"Текст чанка {i}.\n"),
                    )
                )

    def test_no_reembed_when_all_points_exist(self, tmp_path, monkeypatch):
        doc_id = "1111111111111111"
        self._create_chunks(doc_id, 2)
        records = [
            _Rec(chunk_point_id(doc_id, 0), vector={}),
            _Rec(chunk_point_id(doc_id, 1), vector={}),
        ]
        vs, fake = _vs(tmp_path, monkeypatch, records)
        embedder = FakeEmbedder()

        assert vs.backfill_chunks(embedder) == 0
        assert embedder.calls == []  # эмбеддинг не вызывался вовсе
        assert fake.upserted == []

    def test_embeds_only_missing_chunks(self, tmp_path, monkeypatch):
        doc_id = "1111111111111111"
        self._create_chunks(doc_id, 2)
        records = [_Rec(chunk_point_id(doc_id, 0), vector={})]  # chunk 0 есть
        vs, fake = _vs(tmp_path, monkeypatch, records)
        embedder = FakeEmbedder()

        assert vs.backfill_chunks(embedder) == 1
        assert len(embedder.calls) == 1 and len(embedder.calls[0]) == 1  # только chunk 1
        assert fake.upserted[0].payload["chunk_index"] == 1

    def test_skips_trash_and_missing_docs(self, tmp_path, monkeypatch):
        from app.services.registry import DocumentRegistry

        reg = DocumentRegistry()
        reg.create("1111111111111111", "a.pdf", "application/pdf", 1)
        reg.soft_delete("1111111111111111", "u1")
        self._create_chunks("1111111111111111", 2)  # корзина
        vs, fake = _vs(tmp_path, monkeypatch, records=[])
        embedder = FakeEmbedder()

        assert vs.backfill_chunks(embedder) == 0
        assert embedder.calls == []
        assert fake.upserted == []

    def test_payload_contains_dev_tags(self, tmp_path, monkeypatch):
        from app.services.development_registry import DevelopmentRegistry
        from app.services.registry import DocumentRegistry

        doc_id = "1111111111111111"
        dev = DevelopmentRegistry().create("123", "Разработка", created_by="u1")
        reg = DocumentRegistry()
        reg.create(doc_id, "a.pdf", "application/pdf", 1)
        reg.update(doc_id, development_id=dev["id"])
        self._create_chunks(doc_id, 2)
        vs, fake = _vs(tmp_path, monkeypatch, records=[])
        embedder = FakeEmbedder()

        assert vs.backfill_chunks(embedder) == 2
        for point in fake.upserted:
            assert point.payload["dev_tags"] == ["123", "Разработка"]


class TestBackfillBatchResilience:
    """Пер-батчевая устойчивость (инцидент 03.09.2026): один битый батч
    (422 на коллизии sparse-индексов) не должен убивать весь backfill корпуса."""

    def _create_chunks(self, doc_id: str, n: int) -> None:
        from app.db.models import Document, DocumentChunk
        from app.db.session import session_scope

        with session_scope() as s:
            if s.get(Document, doc_id) is None:
                s.add(Document(id=doc_id, filename="a.pdf", content_type="application/pdf", size=1))
            for i in range(n):
                s.add(
                    DocumentChunk(
                        doc_id=doc_id,
                        chunk_index=i,
                        content=f"Текст чанка {i}.\n",
                        char_count=len(f"Текст чанка {i}.\n"),
                    )
                )

    def test_failed_batch_skipped_rest_indexed(self, tmp_path, monkeypatch, caplog):
        doc_id = "1111111111111111"
        self._create_chunks(doc_id, 66)  # 66 чанков → 2 батча по 64 (64+2)

        vs, _ = _vs(tmp_path, monkeypatch, records=[])
        # Батч 2 (чанки 64..65) падает всегда: 422-валидация не ретраится.
        monkeypatch.setattr(vs, "client", _BatchFailingQdrant(fail_from=64, fail_to=65))
        embedder = FakeEmbedder()

        import logging

        with caplog.at_level(logging.ERROR):
            indexed = vs.backfill_chunks(embedder, batch_size=64)

        assert indexed == 64
        assert any("ЧАСТИЧНО" in r.message for r in caplog.records), (
            "итоговая сводка с счётчиками обязательна"
        )

    def test_all_batches_ok_returns_total(self, tmp_path, monkeypatch):
        doc_id = "1111111111111111"
        self._create_chunks(doc_id, 70)

        vs, fake = _vs(tmp_path, monkeypatch, records=[])
        embedder = FakeEmbedder()

        assert vs.backfill_chunks(embedder, batch_size=64) == 70
        assert len(fake.upserted) == 70


def _raise_422():
    from qdrant_client.http.exceptions import UnexpectedResponse

    raise UnexpectedResponse(
        status_code=422,
        reason_phrase="Unprocessable Entity",
        content=b'{"status":{"error":"indices: must be unique"}}',
        headers=httpx.Headers(),
    )


class _BatchFailingQdrant:
    """Qdrant-фейк: upsert батчей, содержащих chunk_index из [fail_from, fail_to],
    всегда падает 422 (валидация не ретраится backfill'ом), остальные проходят."""

    def __init__(self, fail_from: int, fail_to: int):
        self.fail_from = fail_from
        self.fail_to = fail_to
        self.upserts: list[list] = []

    def scroll(
        self, *, collection_name, limit, with_payload, with_vectors, offset=None, scroll_filter=None
    ):
        return [], None

    def upsert(self, *, collection_name, points):
        self.upserts.append(list(points))
        first_idx = points[0].payload["chunk_index"]
        if self.fail_from <= first_idx <= self.fail_to:
            _raise_422()
