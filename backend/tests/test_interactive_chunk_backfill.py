"""Ленивый backfill чанков из GET-запросов не превращается в нагрузку на парсер.

Любой viewer может открыть /chunks или /fulltext документа без чанков; раньше
каждое такое чтение заново запускало разбор исходника (до 120 с и 1 ГБ), ждало
чужой разбор того же документа и делило слоты парсера с загрузками.
"""
import threading

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.errors import DomainError
from app.services.parser_supervisor import ParserBusyError, ParserTimeoutError
from app.services.pipeline import ChunkBackfillBusyError, get_pipeline
from app.services.registry import DocumentRegistry

DOC_ID = "0123456789abcdef"
OTHER_ID = "fedcba9876543210"


def _document(doc_id=DOC_ID, status="error"):
    registry = DocumentRegistry()
    registry.create(doc_id, "scan.pdf", "application/pdf", 1)
    registry.update(doc_id, status=status)


@pytest.fixture
def backfill_calls(monkeypatch):
    calls = []

    def backfill(self, doc_id):
        calls.append(doc_id)

    monkeypatch.setattr("app.services.pipeline.Pipeline._backfill_chunks", backfill)
    return calls


class TestInteractiveBackfill:
    @pytest.mark.parametrize("status", ["uploaded", "queued", "processing", "splitting", "indexing"])
    def test_document_in_processing_is_not_parsed_by_reads(self, backfill_calls, status):
        _document(status=status)
        assert get_pipeline().ensure_chunks(DOC_ID, interactive=True) == []
        assert backfill_calls == []

    def test_empty_result_is_remembered(self, backfill_calls):
        _document()
        pipeline = get_pipeline()
        assert pipeline.ensure_chunks(DOC_ID, interactive=True) == []
        assert pipeline.ensure_chunks(DOC_ID, interactive=True) == []
        assert backfill_calls == [DOC_ID]

    def test_parser_failure_is_remembered_with_stable_code(self, monkeypatch):
        _document()
        calls = []

        def timeout(self, doc_id):
            calls.append(doc_id)
            raise ParserTimeoutError("slow")

        monkeypatch.setattr("app.services.pipeline.Pipeline._backfill_chunks", timeout)
        pipeline = get_pipeline()
        for _ in range(3):
            with pytest.raises(DomainError) as caught:
                pipeline.ensure_chunks(DOC_ID, interactive=True)
            assert caught.value.code == "parser_timeout"
        assert calls == [DOC_ID]

    def test_busy_parser_is_not_remembered(self, monkeypatch):
        _document()
        outcomes = iter([ParserBusyError("busy"), None])

        def backfill(self, doc_id):
            outcome = next(outcomes)
            if outcome is not None:
                raise outcome

        monkeypatch.setattr("app.services.pipeline.Pipeline._backfill_chunks", backfill)
        pipeline = get_pipeline()
        with pytest.raises(ChunkBackfillBusyError):
            pipeline.ensure_chunks(DOC_ID, interactive=True)
        # Повтор после освобождения слотов снова пробует разбор.
        assert pipeline.ensure_chunks(DOC_ID, interactive=True) == []

    def test_second_read_does_not_wait_for_running_backfill(self, monkeypatch):
        _document()
        _document(OTHER_ID)
        started, release = threading.Event(), threading.Event()

        def slow(self, doc_id):
            started.set()
            release.wait(5)

        monkeypatch.setattr("app.services.pipeline.Pipeline._backfill_chunks", slow)
        pipeline = get_pipeline()
        worker = threading.Thread(target=lambda: pipeline.ensure_chunks(DOC_ID, interactive=True))
        worker.start()
        try:
            assert started.wait(5)
            for doc_id in (DOC_ID, OTHER_ID):
                with pytest.raises(ChunkBackfillBusyError):
                    pipeline.ensure_chunks(doc_id, interactive=True)
        finally:
            release.set()
            worker.join(5)

    def test_script_path_keeps_parsing_every_call(self, backfill_calls):
        _document()
        pipeline = get_pipeline()
        pipeline.ensure_chunks(DOC_ID)
        pipeline.ensure_chunks(DOC_ID)
        assert backfill_calls == [DOC_ID, DOC_ID]


class TestReadEndpoints:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
        for module in ("app.config", "app.main", "app.api.documents"):
            monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
        return TestClient(create_app())

    def test_busy_parser_returns_429_with_retry_after(self, client, monkeypatch):
        _document()

        def busy(self, doc_id):
            raise ParserBusyError("busy")

        monkeypatch.setattr("app.services.pipeline.Pipeline._backfill_chunks", busy)
        for path in ("/chunks", "/fulltext", "/fulltext/chunks"):
            resp = client.get(f"/api/documents/{DOC_ID}{path}")
            assert resp.status_code == 429, (path, resp.text)
            assert resp.json()["code"] == "rate_limited"
            assert resp.headers["retry-after"] == "5"

    def test_failed_parse_is_422_and_not_repeated(self, client, monkeypatch):
        _document()
        calls = []

        def timeout(self, doc_id):
            calls.append(doc_id)
            raise ParserTimeoutError("slow")

        monkeypatch.setattr("app.services.pipeline.Pipeline._backfill_chunks", timeout)
        for path in ("/fulltext", "/chunks", "/fulltext/chunks"):
            resp = client.get(f"/api/documents/{DOC_ID}{path}")
            assert resp.status_code == 422, (path, resp.text)
            assert resp.json()["code"] == "parser_timeout"
        assert calls == [DOC_ID]
