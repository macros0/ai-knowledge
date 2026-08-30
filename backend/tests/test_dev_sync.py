"""Тесты фонового fallback реиндекса dev_tags (dev_sync) + флаг в ответе API."""
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.attribute_registry import get_attribute_registry
from app.services.development_registry import get_development_registry
from app.services.registry import get_registry
from app.services.vector_store import VectorStore


def _make_client(monkeypatch) -> TestClient:
    """Клиент с отключённой авторизацией (как make_client в test_audit.py)."""
    from app.config import Settings

    settings = Settings(_env_file=None, auth_provider="disabled")
    for mod in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: settings)
    return TestClient(create_app())


class TestReindexReturnsBool:
    def test_failure_returns_false(self, monkeypatch):
        from app.services.dev_sync import reindex_document_dev_tags

        def boom(*a, **k):
            raise RuntimeError("qdrant down")

        monkeypatch.setattr(VectorStore, "reindex_document_dev_tags", boom)
        assert reindex_document_dev_tags("doc1", ["111"]) is False

    def test_success_returns_true(self, monkeypatch):
        from app.services.dev_sync import reindex_document_dev_tags

        monkeypatch.setattr(VectorStore, "reindex_document_dev_tags", lambda *a, **k: None)
        assert reindex_document_dev_tags("doc1", ["111"]) is True


class TestReindexFromDb:
    def test_reads_current_development_at_execution_time(self, monkeypatch):
        from app.services.dev_sync import reindex_document_dev_tags_from_db

        get_attribute_registry().add("module", "PY")
        get_attribute_registry().add("module", "PT")
        dev1 = get_development_registry().create("111", "Первый", module="PY")
        dev2 = get_development_registry().create("222", "Второй", module="PT")
        get_registry().create("0123456789abcdef", "a.docx", "x", 10, tags=[])
        # Документ сначала привязан к dev1, затем перепривязан к dev2 ДО запуска реиндекса.
        get_registry().update("0123456789abcdef", development_id=dev1["id"])
        get_registry().update("0123456789abcdef", development_id=dev2["id"])

        calls: list[tuple[str, list[str]]] = []

        def capture(_self, doc_id, dev_tags):
            calls.append((doc_id, dev_tags))

        monkeypatch.setattr(VectorStore, "reindex_document_dev_tags", capture)
        reindex_document_dev_tags_from_db("0123456789abcdef")

        assert calls == [("0123456789abcdef", ["222", "Второй", "PT"])]


class TestSetDevelopmentFallback:
    def test_qdrant_failure_schedules_sync_and_flags(self, monkeypatch):
        from app.api import documents as docs

        get_development_registry().create("111", "222")
        get_registry().create("0123456789abcdef", "a.docx", "x", 10, tags=[])

        def boom(*a, **k):
            raise RuntimeError("qdrant down")

        monkeypatch.setattr(VectorStore, "reindex_document_dev_tags", boom)
        schedule_mock = Mock()
        monkeypatch.setattr(docs, "schedule_document_dev_tags_sync", schedule_mock)

        resp = _make_client(monkeypatch).post(
            "/api/documents/0123456789abcdef/development",
            json={"development_id": 1, "confirmed": True},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["dev_tags_sync_pending"] is True
        schedule_mock.assert_called_once_with("0123456789abcdef")

    def test_success_no_pending(self, monkeypatch):
        from app.api import documents as docs

        get_development_registry().create("111", "222")
        get_registry().create("0123456789abcdef", "a.docx", "x", 10, tags=[])

        monkeypatch.setattr(VectorStore, "reindex_document_dev_tags", lambda *a, **k: None)
        schedule_mock = Mock()
        monkeypatch.setattr(docs, "schedule_document_dev_tags_sync", schedule_mock)

        resp = _make_client(monkeypatch).post(
            "/api/documents/0123456789abcdef/development",
            json={"development_id": 1, "confirmed": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["dev_tags_sync_pending"] is False
        schedule_mock.assert_not_called()
