"""Blob download cannot silently fall back to an unverified legacy copy."""
import pytest

from tests.test_artifact_upload_commit import uploads as uploads
from tests.test_artifact_upload_commit import upload, PAYLOAD


def test_bound_original_download_and_range(uploads):
    client, settings, _ = uploads
    doc_id = upload(client).json()["id"]
    (settings.uploads_dir / f"{doc_id}.pdf").unlink()
    response = client.get(f"/api/documents/{doc_id}/download", headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == PAYLOAD[2:6]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_missing_blob_blocks_download(uploads):
    client, settings, _ = uploads
    doc_id = upload(client).json()["id"]
    for path in (settings.data_dir / "artifacts" / "objects").iterdir():
        path.unlink()
    response = client.get(f"/api/documents/{doc_id}/download")
    assert response.status_code == 404


def test_unauthorized_cannot_resolve_blob(uploads, monkeypatch):
    client, _, _ = uploads
    doc_id = upload(client).json()["id"]
    client.cookies.clear()
    def forbidden(*args, **kwargs):
        pytest.fail("Blob store accessed before authentication")
    monkeypatch.setattr("app.services.artifacts.get_blob_store", forbidden)
    assert client.get(f"/api/documents/{doc_id}/download").status_code == 401


def test_trashed_document_cannot_open_blob(uploads, monkeypatch):
    client, _, _ = uploads
    doc_id = upload(client).json()["id"]
    from app.services.registry import get_registry
    get_registry().soft_delete(doc_id)
    def forbidden(*args, **kwargs):
        pytest.fail("Blob store accessed for deleted document")
    monkeypatch.setattr("app.services.artifacts.get_blob_store", forbidden)
    assert client.get(f"/api/documents/{doc_id}/download").status_code == 404


def test_blob_response_closes_private_scratch_on_disconnect(tmp_path):
    import asyncio
    import io
    from app.services.artifacts.local import LocalBlobStore
    from app.services.artifact_response import OpenedBlobResponse
    store = LocalBlobStore(tmp_path / "blobs", store_id="local")
    ref = store.put(io.BytesIO(PAYLOAD), object_id="a" * 32, media_type="application/pdf")
    scratch = tmp_path / "scratch"
    response = OpenedBlobResponse(store=store, ref=ref, scratch_root=scratch, filename="a.pdf")
    assert list(scratch.iterdir())
    async def receive():
        return {"type": "http.disconnect"}
    async def send(message):
        raise RuntimeError("disconnected")
    with pytest.raises(RuntimeError, match="disconnected"):
        asyncio.run(response({"type": "http", "method": "GET", "headers": [],
                              "asgi": {"spec_version": "2.4"}}, receive, send))
    assert response._file.closed
    assert not list(scratch.iterdir())


def test_corrupt_blob_cannot_fall_back_to_legacy_copy(uploads):
    client, settings, _ = uploads
    doc_id = upload(client).json()["id"]
    path = next((settings.data_dir / "artifacts" / "objects").iterdir())
    path.write_bytes(b"corrupt")
    assert client.get(f"/api/documents/{doc_id}/download").status_code == 503


@pytest.mark.parametrize("source_id", ["root", "container-child"])
def test_registered_source_download_uses_bound_original_container(uploads, source_id):
    from app.db.models import DocumentSource
    from app.db.session import session_scope
    client, settings, _ = uploads
    doc_id = upload(client).json()["id"]
    with session_scope() as session:
        session.add(DocumentSource(doc_id=doc_id, source_id="root", kind="document", display_name="a.pdf"))
        session.flush()
        session.add(DocumentSource(doc_id=doc_id, source_id="container-child", parent_source_id="root",
                                   kind="document", display_name="nested", container_source_id="root"))
    (settings.uploads_dir / f"{doc_id}.pdf").unlink()
    response = client.get(f"/api/documents/{doc_id}/sources/download", params={"source_id": source_id})
    assert response.status_code == 200
    assert response.content == PAYLOAD


def test_read_transaction_failure_closes_prepared_download(uploads, monkeypatch):
    from app.api import documents
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    client, settings, _ = uploads
    doc_id = upload(client).json()["id"]
    prepared = []
    original = documents._bound_original_response
    def prepare(session, doc):
        response = original(session, doc)
        prepared.append(response)
        session.info["fail_read_commit"] = True
        return response
    def fail(session):
        if session.info.get("fail_read_commit"):
            raise RuntimeError("Read transaction failed")
    monkeypatch.setattr(documents, "_bound_original_response", prepare)
    event.listen(Session, "before_commit", fail)
    try:
        assert client.get(f"/api/documents/{doc_id}/download").status_code == 500
    finally:
        event.remove(Session, "before_commit", fail)
    assert prepared[0]._file.closed
    assert not list((settings.data_dir / "artifact-scratch").iterdir())
