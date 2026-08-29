"""Тесты эндпоинта раздачи вложений/картинок бандла (/okf/attachments/{filename})."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class FakeVectorStore:
    def ensure_collection(self) -> None:
        pass

    def backfill_sparse(self) -> int:
        return 0


class TestOkfAttachmentsEndpoint:
    def _make_bundle(self, settings: Settings, doc_id: str = "a1b2c3d4e5f60718") -> None:
        attach_dir = settings.okf_dir / doc_id / "attachments"
        attach_dir.mkdir(parents=True, exist_ok=True)
        (attach_dir / "image-0.png").write_bytes(PNG_MAGIC + b"fake-image")
        (settings.okf_dir / doc_id / "concept.md").write_text(
            "---\ntitle: Concept\n---\n\nbody", encoding="utf-8"
        )

    def _client(self, tmp_path: Path, monkeypatch) -> TestClient:
        settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
        monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        monkeypatch.setattr("app.main.get_settings", lambda: settings)
        monkeypatch.setattr("app.main.VectorStore", FakeVectorStore)
        return TestClient(create_app())

    def test_serves_attachment(self, tmp_path: Path, monkeypatch):
        settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
        self._make_bundle(settings)
        client = self._client(tmp_path, monkeypatch)

        with client:
            resp = client.get("/api/documents/a1b2c3d4e5f60718/okf/attachments/image-0.png")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == PNG_MAGIC + b"fake-image"

    def test_missing_attachment_returns_404(self, tmp_path: Path, monkeypatch):
        settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
        self._make_bundle(settings)
        client = self._client(tmp_path, monkeypatch)

        with client:
            resp = client.get("/api/documents/a1b2c3d4e5f60718/okf/attachments/nope.png")

        assert resp.status_code == 404

    def test_path_traversal_blocked(self, tmp_path: Path, monkeypatch):
        settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
        self._make_bundle(settings)
        (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
        client = self._client(tmp_path, monkeypatch)

        with client:
            resp = client.get("/api/documents/a1b2c3d4e5f60718/okf/attachments/..%2F..%2Fsecret.txt")

        assert resp.status_code == 404