import io

import pytest

from app.config import Settings
from app.db.models import Document
from app.db.session import session_scope
from app.services.artifacts.contract import BlobConflict
from app.services.artifacts.local import LocalBlobStore
from app.services.generation_store import begin_generation, abandon_generation

DOC = "a" * 16
SIGNATURE = "d" * 64


def candidate():
    with session_scope() as session:
        session.add(Document(id=DOC, filename="a.pdf"))
        return begin_generation(session, DOC).id


def test_checkpoint_survives_worker_disk_loss(tmp_path):
    from app.services.artifacts.checkpoints import attach_checkpoint, materialize_checkpoint
    generation = candidate()
    store = LocalBlobStore(tmp_path / "durable", store_id="local")
    ref = store.put(io.BytesIO(b'{"concepts":[]}'), object_id="b" * 32, media_type="application/json")
    with session_scope() as session:
        attach_checkpoint(session, ref=ref, doc_id=DOC, generation_id=generation, chunk_index=0,
                          source_signature=SIGNATURE)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with materialize_checkpoint(settings, store=store, doc_id=DOC, generation_id=generation, chunk_index=0,
                                source_signature=SIGNATURE, scratch_root=tmp_path / "other-worker") as path:
        assert path.read_bytes() == b'{"concepts":[]}'
    assert not path.exists()


def test_checkpoint_cas_and_abandoned_generation_reject_late_writer(tmp_path):
    from app.services.artifacts.checkpoints import attach_checkpoint
    generation = candidate()
    store = LocalBlobStore(tmp_path, store_id="local")
    first = store.put(io.BytesIO(b"partial"), object_id="b" * 32, media_type="application/json")
    second = store.put(io.BytesIO(b"complete"), object_id="c" * 32, media_type="application/json")
    scope = dict(doc_id=DOC, generation_id=generation, chunk_index=0, source_signature=SIGNATURE)
    with session_scope() as session:
        attach_checkpoint(session, ref=first, **scope)
    with session_scope() as session:
        with pytest.raises(BlobConflict):
            attach_checkpoint(session, ref=second, **scope)
    with session_scope() as session:
        attach_checkpoint(session, ref=second, expected_object_id=first.object_id, **scope)
    with session_scope() as session:
        abandon_generation(session, DOC, generation)
    from app.services.generation_store import GenerationConflict
    with session_scope() as session:
        with pytest.raises(GenerationConflict):
            attach_checkpoint(session, ref=first, expected_object_id=second.object_id, **scope)


def test_checkpoint_reference_blocks_orphan_gc(tmp_path):
    from app.services.artifacts.checkpoints import attach_checkpoint
    from app.services.artifacts.lifecycle import collect_blob
    generation = candidate()
    store = LocalBlobStore(tmp_path, store_id="local")
    ref = store.put(io.BytesIO(b"checkpoint"), object_id="b" * 32, media_type="application/json")
    with session_scope() as session:
        attach_checkpoint(session, ref=ref, doc_id=DOC, generation_id=generation, chunk_index=0,
                          source_signature=SIGNATURE)
    assert not collect_blob(store, ref.object_id, grace_seconds=0)
