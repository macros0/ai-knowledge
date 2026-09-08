"""Тесты экспорта OKF-бандла из БД (Этап 2b, Фаза 5)."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.models import DocumentChunk, OkfAttachment, OkfConcept
from app.db.session import session_scope
from app.services.registry import get_registry

DOC_ID = "a1b2c3d4e5f60718"


class _FakeVectorStore:
    def ensure_collection(self) -> None:
        pass

    def backfill_sparse(self) -> int:
        return 0


@pytest.fixture
def settings(tmp_path, monkeypatch):
    s = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
    monkeypatch.setattr("app.services.export_okf.get_settings", lambda: s)
    monkeypatch.setattr("app.services.okf_generator.get_settings", lambda: s)
    return s


def _seed(settings, doc_id: str = DOC_ID) -> None:
    get_registry().create(doc_id, "a.docx", "doc", 10, tags=["t1"])
    with session_scope() as s:
        s.add(OkfConcept(doc_id=doc_id, slug="c1", title="C1", type="concept", content="body1", tags=["t1"]))
        s.add(DocumentChunk(doc_id=doc_id, chunk_index=0, content="chunk text", char_count=10))
        att_dir = settings.uploads_dir / doc_id / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)
        (att_dir / "img.png").write_bytes(b"pngdata")
        s.add(OkfAttachment(doc_id=doc_id, name="img.png", saved_path="attachments/img.png", kind="image"))


class TestExportOkfBundle:
    def test_exports_from_db(self, settings):
        from app.services.export_okf import export_okf_bundle

        _seed(settings)
        dest = settings.data_dir / "out"
        files = export_okf_bundle(DOC_ID, dest)

        assert "c1.md" in files
        assert "_files.json" in files
        assert "chunks/chunk_00.md" in files
        assert "attachments/img.png" in files
        assert (dest / "c1.md").is_file()
        assert (dest / "chunks" / "chunk_00.md").read_text(encoding="utf-8") == "chunk text"
        assert (dest / "attachments" / "img.png").read_bytes() == b"pngdata"
        text = (dest / "c1.md").read_text(encoding="utf-8")
        assert "title: C1" in text
        assert "body1" in text

    def test_windows_saved_path_not_leaked_into_bundle(self, settings):
        """Легаси-строка БД с windows-путём не должна утечь в выгруженный бандл.

        saved_path мог быть записан на Windows-машине (перенос данных, старый
        staging). На Linux/macOS Path().name вернул бы такой путь целиком, и
        абсолютный путь машины-источника оказался бы в бандле (инцидент
        2026-09-07). Проверка не зависит от ОС, на которой идут тесты.
        """
        from app.services.export_okf import export_okf_bundle

        win = "C:\\Users\\alexey\\ai-workspace\\data\\uploads\\doc1\\attachments\\embedded-0.pdf"
        get_registry().create(DOC_ID, "a.docx", "doc", 10, tags=["t1"])
        with session_scope() as s:
            s.add(OkfConcept(doc_id=DOC_ID, slug="c1", title="C1", type="concept", content="body1", tags=["t1"]))
            s.add(OkfAttachment(doc_id=DOC_ID, name="oleObject2.bin", saved_path=win, kind="pdf"))

        dest = settings.data_dir / "out"
        export_okf_bundle(DOC_ID, dest)

        leaked = [
            f.relative_to(dest).as_posix()
            for f in dest.rglob("*")
            if f.is_file() and "C:\\Users" in f.read_text(encoding="utf-8", errors="ignore")
        ]
        assert leaked == [], f"абсолютный путь утёк в: {leaked}"
        # И имя файла в метаданных приведено к переносимому виду.
        text = (dest / "c1.md").read_text(encoding="utf-8")
        assert "embedded-0.pdf" in text

    def test_export_missing_doc_raises(self, settings):
        from app.services.export_okf import export_okf_bundle

        with pytest.raises(ValueError):
            export_okf_bundle("ffffffffffffffff", settings.data_dir / "out")


class TestExportOkfEndpoint:
    def _client(self, tmp_path, monkeypatch, settings):
        monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        monkeypatch.setattr("app.main.get_settings", lambda: settings)
        monkeypatch.setattr("app.main.VectorStore", _FakeVectorStore)
        from app.main import create_app

        return TestClient(create_app())

    def test_export_zip(self, tmp_path, monkeypatch, settings):
        _seed(settings)
        client = self._client(tmp_path, monkeypatch, settings)

        with client:
            resp = client.post(f"/api/documents/{DOC_ID}/export-okf")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]
        # ZIP-магия PK\x03\x04.
        assert resp.content[:2] == b"PK"

    def test_export_missing_doc_404(self, tmp_path, monkeypatch, settings):
        client = self._client(tmp_path, monkeypatch, settings)

        with client:
            resp = client.post("/api/documents/ffffffffffffffff/export-okf")

        assert resp.status_code == 404
