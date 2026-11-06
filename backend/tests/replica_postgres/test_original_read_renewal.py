"""Real independent PG transactions renew during a slow healthy original read."""
from contextlib import contextmanager
from threading import Event, Thread
import time

from sqlalchemy import select

from app.db.models import ArtifactReadLease
from app.db.session import session_scope
from app.services.work.context import work_execution
from app.services.work.store import claim, renew
from tests.test_artifact_upload_commit import PAYLOAD, uploads as uploads, upload
from tests.test_work_document_admission import future_api


def test_slow_original_read_does_not_hold_its_lease_or_work_heartbeat_transaction(uploads, monkeypatch):
    from app.services.artifacts import get_blob_store
    from app.services.artifacts import original
    from app.services.artifacts.leased import LeasedBlobStore
    client, settings, _ = future_api(uploads)
    response = upload(client)
    assert response.status_code == 200
    doc_id = response.json()["id"]
    (settings.uploads_dir / f"{doc_id}.pdf").unlink(missing_ok=True)
    with session_scope() as session:
        work = claim(session, owner_id="slow-reader", supported={"document": {1}}, lease_seconds=10)
    durable = get_blob_store(settings)
    entered, stop = Event(), Event()
    failures, beats = [], []

    class SlowStore:
        store_id = durable.store_id

        @contextmanager
        def open(self, ref, *, start=0, end=None, guard=None):
            with durable.open(ref, start=start, end=end, guard=guard) as source:
                class Reader:
                    def read(self, size=-1):
                        entered.set()
                        time.sleep(.1)
                        guard()
                        return source.read(min(size, 1) if size >= 0 else 1)
                yield Reader()

    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda _settings: SlowStore())
    monkeypatch.setattr(original, "LeasedBlobStore", lambda store: LeasedBlobStore(store, ttl_seconds=1, renew_interval=.1))

    def renew_work():
        assert entered.wait(5)
        while not stop.wait(.1):
            try:
                with session_scope() as session:
                    renew(session, work, lease_seconds=10)
                beats.append(True)
            except Exception as exc:
                failures.append(type(exc).__name__)
                return

    thread = Thread(target=renew_work)
    thread.start()
    try:
        with work_execution(work):
            path = original.original_path(settings, doc_id, "a.pdf")
            assert path.read_bytes() == PAYLOAD
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive() and not failures
    assert len(beats) >= 2
    with session_scope() as session:
        assert not session.scalars(select(ArtifactReadLease)).all()
