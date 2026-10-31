import pytest

from app.services.artifacts.contract import BlobIntegrityError
from tests.test_artifact_upload_commit import uploads as uploads
from tests.test_artifact_upload_commit import upload, PAYLOAD


def test_original_cache_can_be_rebuilt_from_committed_blob(uploads):
    from app.services.artifacts.original import original_path
    client, settings, _ = uploads
    doc_id = upload(client).json()["id"]
    path = settings.uploads_dir / f"{doc_id}.pdf"
    path.unlink()
    assert original_path(settings, doc_id, "a.pdf").read_bytes() == PAYLOAD


def test_corrupt_blob_cannot_be_hidden_by_legacy_pipeline_cache(uploads):
    from app.services.artifacts.original import original_path
    client, settings, _ = uploads
    doc_id = upload(client).json()["id"]
    blob = next((settings.data_dir / "artifacts" / "objects").iterdir())
    blob.write_bytes(b"corrupt")
    with pytest.raises(BlobIntegrityError):
        original_path(settings, doc_id, "a.pdf")
