"""Тесты загрузки документа: потоковая запись и потолок размера.

Раньше эндпоинт читал тело целиком в память (await file.read()) и писал его
синхронным write_bytes прямо в event loop — на время записи backend не отвечал
даже на поллинг прогресса. Ограничения на размер не было нигде.
"""
import asyncio
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import create_app

DOCX = b"PK\x03\x04" + b"x" * 100


class FakeVectorStore:
    def __getattr__(self, name):
        return lambda *a, **k: 0


class FakePipeline:
    """Пайплайн подменён: нас интересует запись файла, а не обработка."""

    def __init__(self):
        self.ingested: list[tuple[str, Path]] = []

    def ingest(self, doc_id, filepath, filename, user_tags=None):
        self.ingested.append((doc_id, Path(filepath)))


def _app(tmp_path: Path, monkeypatch, **overrides):
    settings = Settings(data_dir=tmp_path, **overrides)
    monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.VectorStore", FakeVectorStore)
    monkeypatch.setattr("app.services.pipeline.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.registry.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.tag_registry.get_settings", lambda: settings)
    pipeline = FakePipeline()
    monkeypatch.setattr("app.api.documents._pipeline", pipeline)
    from app.services.registry import DocumentRegistry
    from app.services.tag_registry import TagRegistry

    monkeypatch.setattr("app.api.documents._registry", DocumentRegistry())
    monkeypatch.setattr("app.api.documents._tag_registry", TagRegistry())
    return create_app(), settings, pipeline


async def _post(app, files, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post("/api/documents", files=files, headers=headers or {})


class TestUploadWritesStreamed:
    def test_saves_file_and_registers_document(self, tmp_path: Path, monkeypatch):
        app, settings, pipeline = _app(tmp_path, monkeypatch)
        resp = asyncio.run(_post(app, {"file": ("doc.docx", DOCX)}))

        assert resp.status_code == 200, resp.text
        doc = resp.json()
        assert doc["size"] == len(DOCX)
        saved = settings.uploads_dir / f"{doc['id']}.docx"
        assert saved.read_bytes() == DOCX
        assert pipeline.ingested == [(doc["id"], saved)]

    def test_large_file_written_in_chunks(self, tmp_path: Path, monkeypatch):
        """Файл больше одной порции доходит на диск без потерь."""
        app, settings, _ = _app(tmp_path, monkeypatch)
        big = b"PK\x03\x04" + bytes(range(256)) * 20_000  # ~5 МБ, > _UPLOAD_CHUNK_SIZE
        resp = asyncio.run(_post(app, {"file": ("big.docx", big)}))

        assert resp.status_code == 200, resp.text
        saved = settings.uploads_dir / f"{resp.json()['id']}.docx"
        assert saved.read_bytes() == big

    def test_blocking_write_goes_through_threadpool(self, tmp_path: Path, monkeypatch):
        """Запись на диск обязана уходить в пул, а не выполняться в event loop."""
        app, _, _ = _app(tmp_path, monkeypatch)
        offloaded: list[str] = []
        import fastapi.concurrency

        real = fastapi.concurrency.run_in_threadpool

        async def spy(func, *args, **kwargs):
            offloaded.append(getattr(func, "__name__", repr(func)))
            return await real(func, *args, **kwargs)

        monkeypatch.setattr("app.api.documents.run_in_threadpool", spy)
        resp = asyncio.run(_post(app, {"file": ("doc.docx", DOCX)}))

        assert resp.status_code == 200, resp.text
        assert "write" in offloaded, f"запись шла мимо пула: {offloaded}"

    def test_event_loop_stays_responsive_during_upload(self, tmp_path: Path, monkeypatch):
        """Пока идёт медленная запись, другой запрос обслуживается.

        Это и есть регресс: синхронная запись в event loop останавливала весь
        backend, включая поллинг прогресса раз в 1.5 с. Тормозится сама запись
        на диск (а не наш вызов пула), поэтому проверка не зависит от того,
        каким способом эндпоинт пишет файл.
        """
        import pathlib
        import time

        app, settings, _ = _app(tmp_path, monkeypatch)
        real_open = pathlib.Path.open
        real_write_bytes = pathlib.Path.write_bytes
        uploads = settings.uploads_dir

        class SlowFile:
            def __init__(self, f):
                self._f = f

            def write(self, data):
                time.sleep(0.5)
                return self._f.write(data)

            def close(self):
                return self._f.close()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._f.close()

        def slow_open(self, *args, **kwargs):
            f = real_open(self, *args, **kwargs)
            return SlowFile(f) if uploads in self.parents else f

        def slow_write_bytes(self, data):
            if uploads in self.parents:
                time.sleep(0.5)
            return real_write_bytes(self, data)

        monkeypatch.setattr(pathlib.Path, "open", slow_open)
        monkeypatch.setattr(pathlib.Path, "write_bytes", slow_write_bytes)

        async def scenario():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                # засекаем ДО старта загрузки: заблокированный loop задерживает
                # не длительность соседнего запроса, а сам момент его начала
                started = time.perf_counter()
                upload = asyncio.create_task(
                    c.post("/api/documents", files={"file": ("doc.docx", DOCX)})
                )
                await asyncio.sleep(0.05)  # даём загрузке дойти до записи
                other = await c.get("/api/documents")
                elapsed = time.perf_counter() - started
                up = await upload
                return up.status_code, other.status_code, elapsed

        up_status, other_status, elapsed = asyncio.run(scenario())
        assert up_status == 200
        assert other_status == 200
        assert elapsed < 0.3, (
            f"соседний запрос обслужен только через {elapsed:.2f}с при записи 0.5с — loop заблокирован"
        )


class TestUploadSizeLimit:
    def test_rejects_file_over_limit(self, tmp_path: Path, monkeypatch):
        app, settings, pipeline = _app(tmp_path, monkeypatch, upload_max_size_mb=1)
        big = b"PK\x03\x04" + b"x" * (2 * 1024 * 1024)
        resp = asyncio.run(_post(app, {"file": ("big.docx", big)}))

        assert resp.status_code == 413
        assert "МБ" in resp.json()["detail"]
        assert pipeline.ingested == []
        assert list(settings.uploads_dir.glob("*")) == [], "недописанный файл остался на диске"

    def test_streaming_check_catches_undeclared_length(self, tmp_path: Path, monkeypatch):
        """Клиент без Content-Length (chunked) — ловит проверка по факту чтения."""
        app, settings, pipeline = _app(tmp_path, monkeypatch, upload_max_size_mb=1)
        boundary = "----t"
        head = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="big.docx"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        payload = b"PK\x03\x04" + b"x" * (2 * 1024 * 1024)

        async def body():
            yield head
            yield payload
            yield tail

        async def scenario():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                return await c.post(
                    "/api/documents",
                    content=body(),
                    headers={"content-type": f"multipart/form-data; boundary={boundary}"},
                )

        resp = asyncio.run(scenario())
        assert resp.status_code == 413, resp.text
        assert pipeline.ingested == []
        assert list(settings.uploads_dir.glob("*")) == []

    def test_accepts_file_at_limit(self, tmp_path: Path, monkeypatch):
        app, settings, _ = _app(tmp_path, monkeypatch, upload_max_size_mb=1)
        ok = b"PK\x03\x04" + b"x" * (512 * 1024)
        resp = asyncio.run(_post(app, {"file": ("ok.docx", ok)}))

        assert resp.status_code == 200, resp.text
        assert (settings.uploads_dir / f"{resp.json()['id']}.docx").read_bytes() == ok


class TestUploadRejections:
    def test_empty_file_rejected_without_leftovers(self, tmp_path: Path, monkeypatch):
        app, settings, pipeline = _app(tmp_path, monkeypatch)
        resp = asyncio.run(_post(app, {"file": ("empty.docx", b"")}))

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Файл пустой"
        assert pipeline.ingested == []
        assert list(settings.uploads_dir.glob("*")) == []

    def test_unsupported_extension_rejected(self, tmp_path: Path, monkeypatch):
        app, settings, pipeline = _app(tmp_path, monkeypatch)
        resp = asyncio.run(_post(app, {"file": ("script.exe", b"MZ")}))

        assert resp.status_code == 400
        assert "Неподдерживаемый тип файла" in resp.json()["detail"]
        assert pipeline.ingested == []
        assert list(settings.uploads_dir.glob("*")) == []
