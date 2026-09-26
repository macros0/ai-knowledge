"""API дерева источников и безопасной раздачи зарегистрированных байтов."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db.models import Document, DocumentSource
from app.db.session import session_scope
from app.main import create_app


DOC_ID = "a1b2c3d4e5f60718"


class _FakeVectorStore:
    def ensure_collection(self) -> None:
        pass

    def backfill_sparse(self) -> int:
        return 0


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Settings]:
    settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
    monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.main.VectorStore", _FakeVectorStore)
    return TestClient(create_app()), settings


def _sources() -> None:
    with session_scope() as session:
        session.add(Document(id=DOC_ID, filename="forward.eml", content_type="message/rfc822", size=4))
        session.add_all(
            [
                DocumentSource(
                    doc_id=DOC_ID,
                    source_id="root",
                    ordinal=0,
                    kind="mail",
                    display_name="forward.eml",
                    metadata_json={"subject": "Пересылка"},
                    artifact_kind="original",
                ),
                DocumentSource(
                    doc_id=DOC_ID,
                    source_id="root/0",
                    parent_source_id="root",
                    ordinal=0,
                    kind="mail",
                    display_name="embedded.eml",
                    artifact_kind="container_only",
                    container_source_id="root",
                ),
            ]
        )


def test_sources_tree_and_container_download(tmp_path, monkeypatch):
    _sources()
    client, settings = _client(tmp_path, monkeypatch)
    original = settings.uploads_dir / f"{DOC_ID}.eml"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"From: sender@example.test\n\nbody")

    with client:
        tree = client.get(f"/api/documents/{DOC_ID}/sources")
        download = client.get(f"/api/documents/{DOC_ID}/sources/download", params={"source_id": "root/0"})

    assert tree.status_code == 200
    assert [item["source_id"] for item in tree.json()["sources"]] == ["root", "root/0"]
    assert tree.json()["sources"][1]["artifact_kind"] == "container_only"
    assert download.status_code == 200
    assert download.headers["x-content-type-options"] == "nosniff"
    assert 'filename="forward.eml"' in download.headers["content-disposition"]
    assert download.content == original.read_bytes()


def test_sources_hide_trashed_document(tmp_path, monkeypatch):
    _sources()
    with session_scope() as session:
        document = session.get(Document, DOC_ID)
        document.deleted_at = document.created_at
    client, _settings = _client(tmp_path, monkeypatch)

    with client:
        response = client.get(f"/api/documents/{DOC_ID}/sources")

    assert response.status_code == 404
