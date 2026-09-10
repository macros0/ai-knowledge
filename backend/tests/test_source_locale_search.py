"""Тесты фильтра поиска по source_locale (Этап 7 фаза D) + payload при индексации."""
import pytest

import app.services.vector_store as vs_module
from app.config import Settings
from app.models.schemas import OkfDocument
from app.services.vector_store import VectorStore, _source_locale_filter


class TestSourceLocaleFilter:
    def test_match_any_only(self):
        f = _source_locale_filter(["ru", "en"], include_unknown=False)
        assert len(f.should) == 1
        cond = f.should[0]
        assert cond.key == "source_locale"
        assert cond.match.any == ["ru", "en"]

    def test_include_unknown_adds_is_empty(self):
        f = _source_locale_filter(["ru"], include_unknown=True)
        assert len(f.should) == 2
        assert any(getattr(c, "is_empty", None) is True for c in f.should)

    def test_unknown_only(self):
        f = _source_locale_filter([], include_unknown=True)
        assert len(f.should) == 1
        assert f.should[0].is_empty is True


class TestBuildSearchFilter:
    def test_locale_and_tags_are_and(self):
        f = VectorStore._build_search_filter(["tag"], source_locales=["ru"])
        assert len(f.must) == 2
        assert f.must_not  # deleted-фильтр всегда есть

    def test_locale_only(self):
        f = VectorStore._build_search_filter(None, source_locales=["ru"])
        assert len(f.must) == 1
        assert f.must_not

    def test_no_filters(self):
        f = VectorStore._build_search_filter(None)
        assert not f.must
        assert f.must_not


class _FakeQdrant:
    def __init__(self):
        self.points = []

    def upsert(self, *, collection_name, points):
        self.points.extend(points)


@pytest.fixture
def store(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, embedding_dimensions=8)
    monkeypatch.setattr(vs_module, "get_settings", lambda: settings)
    vs = VectorStore()
    return vs


class TestSourceLocaleInPayload:
    def test_concept_payload_includes_source_locale(self, store, monkeypatch):
        fake = _FakeQdrant()
        monkeypatch.setattr(store, "client", fake)
        docs = [
            OkfDocument(
                filepath="/b/doc/c1.md",
                metadata={"title": "T", "type": "concept", "tags": [], "relations": []},
                content="text",
                markdown="",
            )
        ]
        store.index_concepts("a1b2c3d4e5f60718", docs, [[0.1] * 8], source_locale="ru")
        assert fake.points[0].payload["source_locale"] == "ru"

    def test_chunk_payload_includes_source_locale(self, store, monkeypatch):
        fake = _FakeQdrant()
        monkeypatch.setattr(store, "client", fake)
        store.index_chunks(
            "a1b2c3d4e5f60718", "f.docx", ["chunk text"], [], [[0.1] * 8],
            source_locale="en",
        )
        assert fake.points[0].payload["source_locale"] == "en"

    def test_concept_payload_none_source_locale(self, store, monkeypatch):
        fake = _FakeQdrant()
        monkeypatch.setattr(store, "client", fake)
        docs = [
            OkfDocument(
                filepath="/b/doc/c1.md",
                metadata={"title": "T", "type": "concept", "tags": [], "relations": []},
                content="text",
                markdown="",
            )
        ]
        store.index_concepts("a1b2c3d4e5f60718", docs, [[0.1] * 8], source_locale=None)
        assert fake.points[0].payload["source_locale"] is None
