"""Тесты эндпоинта раздачи вложений/картинок бандла (/okf/attachments/{filename})."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# doc_id имеет форму uuid4().hex[:16] — эндпоинты валидируют её
DOC_ID = "a1b2c3d4e5f60718"


class FakeVectorStore:
    def ensure_collection(self) -> None:
        pass

    def backfill_sparse(self) -> int:
        return 0


class TestOkfAttachmentsEndpoint:
    def _make_bundle(self, settings: Settings, doc_id: str = DOC_ID) -> None:
        attach_dir = settings.okf_dir / doc_id / "attachments"
        attach_dir.mkdir(parents=True, exist_ok=True)
        (attach_dir / "image-0.png").write_bytes(PNG_MAGIC + b"fake-image")
        (settings.okf_dir / doc_id / "concept.md").write_text(
            "---\ntitle: Concept\n---\n\nbody", encoding="utf-8"
        )

    def _client(self, tmp_path: Path, monkeypatch) -> TestClient:
        settings = Settings(data_dir=tmp_path)
        monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)
        monkeypatch.setattr("app.main.get_settings", lambda: settings)
        monkeypatch.setattr("app.main.VectorStore", FakeVectorStore)
        return TestClient(create_app())

    def test_serves_attachment(self, tmp_path: Path, monkeypatch):
        settings = Settings(data_dir=tmp_path)
        self._make_bundle(settings)
        client = self._client(tmp_path, monkeypatch)

        with client:
            resp = client.get(f"/api/documents/{DOC_ID}/okf/attachments/image-0.png")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == PNG_MAGIC + b"fake-image"

    def test_missing_attachment_returns_404(self, tmp_path: Path, monkeypatch):
        settings = Settings(data_dir=tmp_path)
        self._make_bundle(settings)
        client = self._client(tmp_path, monkeypatch)

        with client:
            resp = client.get(f"/api/documents/{DOC_ID}/okf/attachments/nope.png")

        assert resp.status_code == 404

    def test_path_traversal_blocked(self, tmp_path: Path, monkeypatch):
        settings = Settings(data_dir=tmp_path)
        self._make_bundle(settings)
        (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
        client = self._client(tmp_path, monkeypatch)

        with client:
            resp = client.get(f"/api/documents/{DOC_ID}/okf/attachments/..%2F..%2Fsecret.txt")

        assert resp.status_code == 404

    def test_path_traversal_via_doc_id_blocked(self, tmp_path: Path, monkeypatch):
        """Traversal в самом doc_id: bundle_dir уезжает наружу, и проверка
        вложенности подтверждает, что файл лежит «внутри» уехавшей директории."""
        settings = Settings(data_dir=tmp_path)
        self._make_bundle(settings)
        (tmp_path / "documents.json").write_text("registry contents", encoding="utf-8")
        (tmp_path / "attachments").mkdir(exist_ok=True)
        (tmp_path / "attachments" / "a.txt").write_text("secret", encoding="utf-8")
        (tmp_path / "chunks").mkdir(exist_ok=True)
        (tmp_path / "chunks" / "chunk_00.md").write_text("secret", encoding="utf-8")
        (tmp_path / "leak.md").write_text("---\ntitle: T\n---\nsecret", encoding="utf-8")
        client = self._client(tmp_path, monkeypatch)

        # uvicorn раскодирует %2F до роутинга, поэтому doc_id=".." доходит до хендлера
        with client:
            for path in (
                "/api/documents/..%2Fokf/documents.json",
                "/api/documents/..%2Fokf/attachments/a.txt",
                "/api/documents/..%2Fchunks/0",
                "/api/documents/..%2Fokf",
                "/api/documents/..%2Ffulltext",
            ):
                resp = client.get(path)
                assert resp.status_code == 404, f"{path} -> {resp.status_code} {resp.text[:100]}"
                assert "secret" not in resp.text
                assert "registry contents" not in resp.text

        # хендлер листинга пишет _files.json — он не должен уйти за пределы бандла
        assert not (tmp_path / "_files.json").exists()

    def test_rejects_non_hex_doc_id(self, tmp_path: Path, monkeypatch):
        client = self._client(tmp_path, monkeypatch)
        with client:
            for bad in ("doc1", "a1b2c3d4e5f6071", "a1b2c3d4e5f607188", "A1B2C3D4E5F60718"):
                assert client.get(f"/api/documents/{bad}/okf").status_code == 404
