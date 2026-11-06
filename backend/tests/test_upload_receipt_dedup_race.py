"""A receipt miss before another replica commits is not a duplicate upload."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from app.db.models import Document, UploadReceipt, WorkItem
from app.db.session import session_scope
from tests.test_artifact_upload_commit import KEY, uploads as uploads
from tests.test_upload_similarity import docx_bytes
from tests.test_work_document_admission import future_api


def test_same_key_retry_rechecks_receipt_after_concurrent_hash_match(uploads, monkeypatch):
    from app.api import documents
    client, settings, _ = future_api(uploads)
    settings.dedup_enabled = True
    settings.parser_supervisor_enabled = False
    payload = docx_bytes("same-key-concurrent")
    paused, committed, guard = Event(), Event(), Lock()
    original = documents.file_hash_exists
    first = True

    def after_initial_receipt_miss(digest):
        nonlocal first
        with guard:
            wait_for_commit, first = first, False
        if wait_for_commit:
            paused.set()
            assert committed.wait(15)
        return original(digest)

    monkeypatch.setattr(documents, "file_hash_exists", after_initial_receipt_miss)

    def request():
        return client.post("/api/documents", headers={"Idempotency-Key": KEY},
            files={"file": ("same.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})

    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(request)
        try:
            assert paused.wait(10)
            admitted = request()
            assert admitted.status_code == 200, admitted.text
        finally:
            committed.set()
        replay = waiting.result(timeout=15)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == admitted.json()["id"]
    with session_scope() as session:
        assert session.query(Document).count() == session.query(UploadReceipt).count() == session.query(WorkItem).count() == 1
