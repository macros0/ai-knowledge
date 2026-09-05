"""Тесты эндпоинта раздачи вложений/картинок бандла (/okf/attachments/{filename})."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PNG_MAGIC = b"\x89PNG\r\x1a\n"


class FakeVectorStore:
    def ensure_collection(self) -> None:
        pass

    def backfill_sparse(self) -> int:
        return 0


def make_client(tmp_path: Path, monkeypatch) -> TestClient:
    settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
    monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.VectorStore", FakeVectorStore)
    return TestClient(create_app())


class TestOkfAttachmentsEndpoint:
    def _make_bundle(self, settings: Settings, doc_id: str = "a1b2c3d4e5f60718") -> None:
        # Этап 2b: бинарники вложений — в uploads/<doc_id>/attachments/ (не в бандле).
        attach_dir = settings.uploads_dir / doc_id / "attachments"
        attach_dir.mkdir(parents=True, exist_ok=True)
        (attach_dir / "image-0.png").write_bytes(PNG_MAGIC + b"fake-image")

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
        # Stored-XSS-защита: вложения никогда не рендерятся inline при прямой
        # навигации; тип контента не переинтерпретируется браузером.
        assert resp.headers["content-disposition"] == "attachment"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.content == PNG_MAGIC + b"fake-image"

    def test_svg_attachment_served_as_download(self, tmp_path: Path, monkeypatch):
        """SVG-вложение (вектор stored XSS) не отдаётся inline даже с image/svg+xml."""
        settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
        attach_dir = settings.uploads_dir / "a1b2c3d4e5f60718" / "attachments"
        attach_dir.mkdir(parents=True, exist_ok=True)
        (attach_dir / "schema.svg").write_text(
            "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            encoding="utf-8",
        )
        client = make_client(tmp_path, monkeypatch)

        with client:
            resp = client.get("/api/documents/a1b2c3d4e5f60718/okf/attachments/schema.svg")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")
        assert resp.headers["content-disposition"] == "attachment"
        assert resp.headers["x-content-type-options"] == "nosniff"

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


class TestDocIdTraversalBlocked:
    """Все /{doc_id}-роуты обязаны валидировать формат id (16 hex).

    `okf_dir / doc_id` без валидации допускает `..` и абсолютный путь
    (Windows) — листинг чужого каталога + запись туда `_files.json`
    (list_okf_files), чтение чужих чанков (list_chunks/fulltext).
    """

    BAD_IDS = [
        "..%2f..%2fsecret",      # .. + encoded /
        "..",                    # голые dot-segments
        "c%3a%2ftemp%2fx",      # абсолютный путь Windows (C:/temp/x)
        "aaaaaaaaaaaaaaaaa",    # 17 символов — не hex-16
        "zzzzzzzzzzzzzzzz",     # не hex
    ]
    ENDPOINTS = ["", "/download", "/okf", "/chunks", "/fulltext"]

    @pytest.mark.parametrize("doc_id", BAD_IDS)
    @pytest.mark.parametrize("suffix", ENDPOINTS)
    def test_invalid_doc_id_404(self, doc_id, suffix, tmp_path, monkeypatch):
        (tmp_path / "secret.md").write_text("secret", encoding="utf-8")
        client = make_client(tmp_path, monkeypatch)

        with client:
            resp = client.get(f"/api/documents/{doc_id}{suffix}")

        assert resp.status_code == 404, doc_id

    def test_traversal_does_not_write_outside_bundles(self, tmp_path, monkeypatch):
        """list_okf_files c `..` не должен создавать _files.json в чужом каталоге."""
        secret_dir = tmp_path / "secret_dir"
        secret_dir.mkdir()
        (secret_dir / "leak.md").write_text("---\ntitle: leak\n---\nbody", encoding="utf-8")
        client = make_client(tmp_path, monkeypatch)

        with client:
            resp = client.get("/api/documents/..%2fsecret_dir/okf")

        assert resp.status_code == 404
        assert not (secret_dir / "_files.json").exists()

    def test_valid_doc_id_still_lists_okf(self, tmp_path, monkeypatch):
        """Регресс: легитимный hex-id проходит валидацию; список идёт из БД (okf_concepts)."""
        from app.db.models import Document, OkfConcept
        from app.db.session import session_scope

        with session_scope() as s:
            s.add(Document(id="a1b2c3d4e5f60718", filename="c.docx", content_type="doc", size=1))
            s.add(OkfConcept(doc_id="a1b2c3d4e5f60718", slug="concept", title="Concept", content="body"))
        client = make_client(tmp_path, monkeypatch)

        with client:
            resp = client.get("/api/documents/a1b2c3d4e5f60718/okf")

        assert resp.status_code == 200
        files = resp.json()
        assert [f["filename"] for f in files] == ["concept.md"]