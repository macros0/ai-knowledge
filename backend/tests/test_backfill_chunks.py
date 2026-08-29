"""Тесты бэкфилла чанков: он не должен пере-эмбеддить уже проиндексированное.

existing_ids собирается полным скроллом коллекции — если его не сверять с
детерминированным point_id, каждый рестарт backend прогоняет весь корпус
через embedding-сервис заново.
"""
from pathlib import Path

from app.config import Settings
from app.services.vector_store import VectorStore, _chunk_point_id


class FakeRecord:
    def __init__(self, id_: str):
        self.id = id_


class FakeQdrant:
    """Минимальный клиент: scroll отдаёт заданные id, upsert копит точки."""

    def __init__(self, existing: list[str] | None = None):
        self.existing = [FakeRecord(i) for i in (existing or [])]
        self.upserted: list = []

    def scroll(self, collection_name, limit=1000, with_payload=False, with_vectors=False, offset=None):
        return self.existing, None

    def upsert(self, collection_name, points):
        self.upserted.extend(points)


class FakeEmbedder:
    def __init__(self):
        self.batches: list[list[str]] = []

    @property
    def embedded_count(self) -> int:
        return sum(len(b) for b in self.batches)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]


def _make_bundle(settings: Settings, doc_id: str, n_chunks: int) -> None:
    chunks_dir = settings.okf_dir / doc_id / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n_chunks):
        (chunks_dir / f"chunk_{i:02d}.md").write_text(f"# Раздел {i}\n\nтекст чанка {i}", encoding="utf-8")
    (settings.okf_dir / doc_id / "concept.md").write_text(
        "---\ntitle: C\nglobal_tags: [tag-a]\n---\n\nтело", encoding="utf-8"
    )


def _store(tmp_path: Path, monkeypatch, client: FakeQdrant) -> VectorStore:
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr("app.services.vector_store.get_settings", lambda: settings)
    vs = VectorStore()
    vs.client = client
    return vs


class TestBackfillChunksSkipsIndexed:
    def test_indexes_chunks_missing_from_collection(self, tmp_path: Path, monkeypatch):
        settings = Settings(data_dir=tmp_path)
        _make_bundle(settings, "a1b2c3d4e5f60718", 3)
        client = FakeQdrant(existing=[])
        vs = _store(tmp_path, monkeypatch, client)
        emb = FakeEmbedder()

        assert vs.backfill_chunks(emb) == 3
        assert emb.embedded_count == 3
        assert len(client.upserted) == 3

    def test_skips_already_indexed_chunks(self, tmp_path: Path, monkeypatch):
        """Главный регресс: все точки уже в коллекции — embedder не зовём вовсе."""
        settings = Settings(data_dir=tmp_path)
        doc_id = "a1b2c3d4e5f60718"
        _make_bundle(settings, doc_id, 3)
        client = FakeQdrant(existing=[_chunk_point_id(doc_id, i) for i in range(3)])
        vs = _store(tmp_path, monkeypatch, client)
        emb = FakeEmbedder()

        assert vs.backfill_chunks(emb) == 0
        assert emb.batches == [], "уже проиндексированные чанки ушли в embedder"
        assert client.upserted == []

    def test_embeds_only_the_missing_chunk(self, tmp_path: Path, monkeypatch):
        settings = Settings(data_dir=tmp_path)
        doc_id = "a1b2c3d4e5f60718"
        _make_bundle(settings, doc_id, 4)
        # чанк 2 потерян — только он и должен пойти на эмбеддинг
        client = FakeQdrant(existing=[_chunk_point_id(doc_id, i) for i in (0, 1, 3)])
        vs = _store(tmp_path, monkeypatch, client)
        emb = FakeEmbedder()

        assert vs.backfill_chunks(emb) == 1
        assert emb.embedded_count == 1
        assert "текст чанка 2" in emb.batches[0][0]
        assert len(client.upserted) == 1
        assert client.upserted[0].id == _chunk_point_id(doc_id, 2)
        assert client.upserted[0].payload["chunk_index"] == 2

    def test_skip_is_per_document(self, tmp_path: Path, monkeypatch):
        settings = Settings(data_dir=tmp_path)
        done, fresh = "a1b2c3d4e5f60718", "b1b2c3d4e5f60719"
        _make_bundle(settings, done, 2)
        _make_bundle(settings, fresh, 2)
        client = FakeQdrant(existing=[_chunk_point_id(done, i) for i in range(2)])
        vs = _store(tmp_path, monkeypatch, client)
        emb = FakeEmbedder()

        assert vs.backfill_chunks(emb) == 2
        assert emb.embedded_count == 2
        assert {p.payload["doc_id"] for p in client.upserted} == {fresh}

    def test_disabled_by_setting(self, tmp_path: Path, monkeypatch):
        settings = Settings(data_dir=tmp_path, search_index_chunks_enabled=False)
        _make_bundle(settings, "a1b2c3d4e5f60718", 2)
        monkeypatch.setattr("app.services.vector_store.get_settings", lambda: settings)
        vs = VectorStore()
        vs.client = FakeQdrant(existing=[])
        emb = FakeEmbedder()

        assert vs.backfill_chunks(emb) == 0
        assert emb.batches == []


class TestChunkPointId:
    def test_matches_index_chunks_formula(self, tmp_path: Path, monkeypatch):
        """point_id из index_chunks и из бэкфилла обязан совпадать —
        иначе пропуск молча перестаёт срабатывать."""
        settings = Settings(data_dir=tmp_path)
        monkeypatch.setattr("app.services.vector_store.get_settings", lambda: settings)
        vs = VectorStore()
        vs.client = FakeQdrant()
        doc_id = "a1b2c3d4e5f60718"

        ids = vs.index_chunks(
            doc_id, "in.docx", ["текст 0", "текст 1"], [], [[0.1], [0.2]], section_titles=["A", "B"]
        )
        assert ids == {_chunk_point_id(doc_id, 0), _chunk_point_id(doc_id, 1)}

    def test_isolated_from_concept_ids(self):
        import uuid

        doc_id = "a1b2c3d4e5f60718"
        concept_like = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}/chunks/chunk_00.md"))
        assert _chunk_point_id(doc_id, 0) != concept_like
