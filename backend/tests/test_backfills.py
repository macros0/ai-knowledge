"""Тесты backfill-функций VectorStore (backfill_sparse / backfill_chunks).

Фейковый Qdrant-клиент: scroll отдаёт подготовленные записи, update_vectors/
upsert — записывают вызовы. Реальная БД (SQLite per-test из conftest) —
для проверок registry-гейтов (корзина, dev_tags).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.services.sparse import to_sparse_vector
from app.services.vector_store import SPARSE_VECTOR_NAME, VectorStore


class _Rec:
    def __init__(self, id, vector=None, payload=None):
        self.id = id
        self.vector = vector
        self.payload = payload or {}


class FakeQdrant:
    def __init__(self, records):
        self.records = records
        self.updated_vectors: list = []
        self.upserted: list = []

    def scroll(self, *, collection_name, limit, with_payload, with_vectors, offset=None):
        return self.records, None

    def update_vectors(self, *, collection_name, points):
        self.updated_vectors.extend(points)

    def upsert(self, *, collection_name, points):
        self.upserted.extend(points)


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


def _write_concept_md(okf_dir: Path, doc_id: str = "a1b2c3d4e5f60718") -> Path:
    bundle = okf_dir / doc_id
    bundle.mkdir(parents=True)
    md = bundle / "concept.md"
    md.write_text(
        "---\n"
        "title: Концепт 12410\n"
        "tags: [уникальныйтег]\n"
        "source_document: report.docx\n"
        "---\n"
        "\n"
        "# Концепт 12410\n"
        "\n"
        "Тело концепта с деталями.\n",
        encoding="utf-8",
    )
    return md


class TestBackfillSparse:
    def test_skips_points_with_existing_sparse(self, tmp_path, monkeypatch):
        settings = Settings(_env_file=None, data_dir=tmp_path)
        md = _write_concept_md(settings.okf_dir)
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(md)))
        vs, fake = _vs(
            tmp_path, monkeypatch, records=[_Rec(point_id, vector={"": [0.1] * 8, SPARSE_VECTOR_NAME: None})]
        )

        assert vs.backfill_sparse() == 0
        assert fake.updated_vectors == []

    def test_backfills_missing_sparse_from_title_and_content_only(self, tmp_path, monkeypatch):
        """Sparse строится по формуле индексации (title + content), без frontmatter."""
        settings = Settings(_env_file=None, data_dir=tmp_path)
        md = _write_concept_md(settings.okf_dir)
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(md)))
        vs, fake = _vs(tmp_path, monkeypatch, records=[_Rec(point_id, vector={"": [0.1] * 8})])

        assert vs.backfill_sparse() == 1
        assert len(fake.updated_vectors) == 1
        got = fake.updated_vectors[0].vector[SPARSE_VECTOR_NAME]
        # Каноническая формула: "Концепт 12410\nТело концепта с деталями." —
        # frontmatter (теги, source_document) в BM25 не попадает.
        expected = to_sparse_vector("Концепт 12410\nТело концепта с деталями.")
        assert got.indices == expected.indices
        polluted = to_sparse_vector(md.read_text(encoding="utf-8"))
        assert got.indices != polluted.indices

    def test_force_recomputes_even_with_sparse(self, tmp_path, monkeypatch):
        settings = Settings(_env_file=None, data_dir=tmp_path)
        md = _write_concept_md(settings.okf_dir)
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(md)))
        vs, fake = _vs(
            tmp_path,
            monkeypatch,
            records=[_Rec(point_id, vector={"": [0.1] * 8, SPARSE_VECTOR_NAME: None})],
        )

        assert vs.backfill_sparse(force=True) == 1
        assert len(fake.updated_vectors) == 1

    def test_skips_points_not_in_bundles(self, tmp_path, monkeypatch):
        settings = Settings(_env_file=None, data_dir=tmp_path)
        _write_concept_md(settings.okf_dir)  # бандл есть, но точки в Qdrant нет
        vs, fake = _vs(tmp_path, monkeypatch, records=[_Rec("orphan-point", vector={})])

        assert vs.backfill_sparse() == 0
        assert fake.updated_vectors == []


class TestBackfillChunks:
    def _write_chunks(self, okf_dir: Path, doc_id: str = "1111111111111111") -> list[Path]:
        chunks_dir = okf_dir / doc_id / "chunks"
        chunks_dir.mkdir(parents=True)
        paths = []
        for i in range(2):
            p = chunks_dir / f"chunk_{i:02d}.md"
            p.write_text(f"Текст чанка {i}.\n", encoding="utf-8")
            paths.append(p)
        return paths

    def _chunk_point_id(self, doc_id: str, idx: int) -> str:
        return str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"chunk:{doc_id}/chunks/chunk_{idx:02d}.md")
        )

    def test_no_reembed_when_all_points_exist(self, tmp_path, monkeypatch):
        from app.services.registry import DocumentRegistry

        settings = Settings(_env_file=None, data_dir=tmp_path)
        doc_id = "1111111111111111"
        DocumentRegistry().create(doc_id, "a.pdf", "application/pdf", 1)
        self._write_chunks(settings.okf_dir)
        records = [
            _Rec(self._chunk_point_id(doc_id, 0), vector={}),
            _Rec(self._chunk_point_id(doc_id, 1), vector={}),
        ]
        vs, fake = _vs(tmp_path, monkeypatch, records)
        embedder = FakeEmbedder()

        assert vs.backfill_chunks(embedder) == 0
        assert embedder.calls == []  # эмбеддинг не вызывался вовсе
        assert fake.upserted == []

    def test_embeds_only_missing_chunks(self, tmp_path, monkeypatch):
        from app.services.registry import DocumentRegistry

        settings = Settings(_env_file=None, data_dir=tmp_path)
        doc_id = "1111111111111111"
        DocumentRegistry().create(doc_id, "a.pdf", "application/pdf", 1)
        self._write_chunks(settings.okf_dir)
        records = [_Rec(self._chunk_point_id(doc_id, 0), vector={})]  # chunk 0 есть
        vs, fake = _vs(tmp_path, monkeypatch, records)
        embedder = FakeEmbedder()

        assert vs.backfill_chunks(embedder) == 1
        assert len(embedder.calls) == 1 and len(embedder.calls[0]) == 1  # только chunk 1
        assert fake.upserted[0].payload["chunk_index"] == 1

    def test_skips_trash_and_missing_docs(self, tmp_path, monkeypatch):
        from app.services.registry import DocumentRegistry

        settings = Settings(_env_file=None, data_dir=tmp_path)
        reg = DocumentRegistry()
        reg.create("1111111111111111", "a.pdf", "application/pdf", 1)
        reg.soft_delete("1111111111111111", "u1")
        self._write_chunks(settings.okf_dir, "1111111111111111")  # корзина
        self._write_chunks(settings.okf_dir, "2222222222222222")  # нет строки в БД (purge)
        vs, fake = _vs(tmp_path, monkeypatch, records=[])
        embedder = FakeEmbedder()

        assert vs.backfill_chunks(embedder) == 0
        assert embedder.calls == []
        assert fake.upserted == []

    def test_payload_contains_dev_tags(self, tmp_path, monkeypatch):
        from app.services.development_registry import DevelopmentRegistry
        from app.services.registry import DocumentRegistry

        settings = Settings(_env_file=None, data_dir=tmp_path)
        doc_id = "1111111111111111"
        dev = DevelopmentRegistry().create("123", "Разработка", created_by="u1")
        reg = DocumentRegistry()
        reg.create(doc_id, "a.pdf", "application/pdf", 1)
        reg.update(doc_id, development_id=dev["id"])
        self._write_chunks(settings.okf_dir)
        vs, fake = _vs(tmp_path, monkeypatch, records=[])
        embedder = FakeEmbedder()

        assert vs.backfill_chunks(embedder) == 2
        for point in fake.upserted:
            assert point.payload["dev_tags"] == ["123", "Разработка"]


class TestBackfillBatchResilience:
    """Пер-батчевая устойчивость (инцидент 03.09.2026): один битый батч
    (422 на коллизии sparse-индексов) не должен убивать весь backfill корпуса."""

    def _chunk_point_id(self, doc_id: str, idx: int) -> str:
        return str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"chunk:{doc_id}/chunks/chunk_{idx:02d}.md")
        )

    def test_failed_batch_skipped_rest_indexed(self, tmp_path, monkeypatch, caplog):
        from app.services.registry import DocumentRegistry

        settings = Settings(_env_file=None, data_dir=tmp_path)
        doc_id = "1111111111111111"
        DocumentRegistry().create(doc_id, "a.pdf", "application/pdf", 1)
        chunks_dir = settings.okf_dir / doc_id / "chunks"
        chunks_dir.mkdir(parents=True)
        # 66 чанков → 2 батча по 64 (64+2)
        for i in range(66):
            (chunks_dir / f"chunk_{i:02d}.md").write_text(f"Текст чанка {i}.\n", encoding="utf-8")

        vs, _ = _vs(tmp_path, monkeypatch, records=[])

        # Батч 2 (чанки 64..65) падает всегда: 422-валидация не ретраится.
        monkeypatch.setattr(vs, "client", _BatchFailingQdrant(fail_from=64, fail_to=65))
        embedder = FakeEmbedder()

        import logging

        with caplog.at_level(logging.ERROR):
            indexed = vs.backfill_chunks(embedder, batch_size=64)

        # 66 точек собраны, 64 проиндексированы (батч 1), возврат —
        # число реально ушедших точек, не собранных.
        assert indexed == 64
        assert any("ЧАСТИЧНО" in r.message for r in caplog.records), (
            "итоговая сводка с счётчиками обязательна"
        )

    def test_all_batches_ok_returns_total(self, tmp_path, monkeypatch):
        from app.services.registry import DocumentRegistry

        settings = Settings(_env_file=None, data_dir=tmp_path)
        doc_id = "1111111111111111"
        DocumentRegistry().create(doc_id, "a.pdf", "application/pdf", 1)
        chunks_dir = settings.okf_dir / doc_id / "chunks"
        chunks_dir.mkdir(parents=True)
        for i in range(70):
            (chunks_dir / f"chunk_{i:02d}.md").write_text(f"Текст чанка {i}.\n", encoding="utf-8")

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

    def scroll(self, *, collection_name, limit, with_payload, with_vectors, offset=None):
        return [], None

    def upsert(self, *, collection_name, points):
        self.upserts.append(list(points))
        first_idx = points[0].payload["chunk_index"]
        if self.fail_from <= first_idx <= self.fail_to:
            _raise_422()
