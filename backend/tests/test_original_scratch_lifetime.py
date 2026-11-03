"""Durable originals outlive requests; their private materializations do not."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from starlette.responses import FileResponse

from app.db.models import ArtifactBinding, WorkItem
from app.db.session import session_scope
from app.services.work.context import work_execution
from app.services.work.store import claim
from tests.test_artifact_upload_commit import PAYLOAD, uploads as uploads, upload
from tests.test_work_document_admission import future_api


def accepted_original(uploads):
    client, settings, _ = future_api(uploads)
    response = upload(client)
    assert response.status_code == 200
    doc_id = response.json()["id"]
    (settings.uploads_dir / f"{doc_id}.pdf").unlink(missing_ok=True)
    with session_scope() as session:
        work = claim(session, owner_id="scratch-reader", supported={"document": {1}}, lease_seconds=120)
    return client, settings, doc_id, work


def test_successive_api_uploads_do_not_accumulate_private_originals(uploads):
    from app.services.artifacts import get_blob_store
    from app.services.artifacts.repository import resolve_blob
    client, settings, _ = future_api(uploads)
    chunk = b"x" * (256 * 1024)
    for index in range(5):
        raw = PAYLOAD + chunk + bytes([index])
        response = upload(client, key=f"scratch-upload-{index:016d}", payload=raw)
        assert response.status_code == 200
        assert not list(settings.uploads_dir.glob("*.pdf"))
        with session_scope() as session:
            ref = resolve_blob(session, owner_type="original", owner_id=response.json()["id"], generation_id=None, logical_name="original")
        with get_blob_store(settings).open(ref) as stream:
            assert stream.read() == raw
    with session_scope() as session:
        assert session.query(ArtifactBinding).count() == session.query(WorkItem).count() == 5


def test_nested_work_reader_keeps_original_until_outer_attempt_finishes(uploads):
    from app.services.artifacts.original import original_path
    _, settings, doc_id, work = accepted_original(uploads)
    with work_execution(work):
        path = original_path(settings, doc_id, "a.pdf")
        with work_execution(work):
            assert original_path(settings, doc_id, "a.pdf").read_bytes() == PAYLOAD
        assert path.read_bytes() == PAYLOAD
    assert not path.exists()


def test_finished_attempt_does_not_remove_another_active_readers_copy(uploads):
    from app.services.artifacts.original import original_path
    _, settings, doc_id, work = accepted_original(uploads)
    entered, release = Event(), Event()

    def held_reader():
        with work_execution(work):
            path = original_path(settings, doc_id, "a.pdf")
            entered.set()
            assert release.wait(15)
            assert path.read_bytes() == PAYLOAD
        return path

    with ThreadPoolExecutor(max_workers=1) as pool:
        reader = pool.submit(held_reader)
        try:
            assert entered.wait(10)
            with work_execution(work):
                finished = original_path(settings, doc_id, "a.pdf")
                assert finished.read_bytes() == PAYLOAD
            assert not finished.exists()
        finally:
            release.set()
        held = reader.result(timeout=15)
    assert not held.exists()


def test_request_scope_keeps_materialization_until_file_response_finishes(uploads):
    from app.services.artifacts.original import original_path
    client, settings, doc_id, _ = accepted_original(uploads)
    observed = []

    def download():
        path = original_path(settings, doc_id, "a.pdf")
        observed.append(path)
        return FileResponse(path)

    client.app.add_api_route("/scratch-test-download", download)
    response = client.get("/scratch-test-download")
    assert response.status_code == 200 and response.content == PAYLOAD
    assert observed and not observed[0].exists()


def test_combined_legacy_original_survives_attempt_scope(uploads):
    from app.services.artifacts.original import original_path
    _, settings, doc_id, work = accepted_original(uploads)
    settings.okf_deployment_schema, settings.app_role = None, "combined"
    with work_execution(work):
        path = original_path(settings, doc_id, "a.pdf")
    assert path.read_bytes() == PAYLOAD
