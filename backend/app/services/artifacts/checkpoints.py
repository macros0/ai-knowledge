"""Generation-scoped immutable checkpoint bytes and explicit CAS replacement."""
from contextlib import contextmanager
import re

from app.db.models import ArtifactObject, CheckpointBinding, Document, DocumentGeneration
from app.db.session import session_scope
from app.services.generation_store import lock_generation_read, lock_writable_generation
from .contract import BlobConflict, BlobNotFound
from .leased import LeasedBlobStore
from .materialize import materialize
from .repository import _ref, register_blob


def _identity(doc_id, generation_id, chunk_index, source_signature):
    if (not isinstance(doc_id, str) or not re.fullmatch(r"[0-9a-f]{16}", doc_id)
            or not isinstance(generation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", generation_id)
            or type(chunk_index) is not int or chunk_index < 0
            or not isinstance(source_signature, str) or not re.fullmatch(r"[0-9a-f]{64}", source_signature)):
        raise ValueError("Invalid checkpoint identity")
    return dict(doc_id=doc_id, generation_id=generation_id, chunk_index=chunk_index, source_signature=source_signature)


def attach_checkpoint(session, *, ref, doc_id, generation_id, chunk_index, source_signature, expected_object_id=None):
    identity = _identity(doc_id, generation_id, chunk_index, source_signature)
    lock_writable_generation(session, doc_id, generation_id)
    existing = session.get(CheckpointBinding, identity, populate_existing=True)
    if existing is not None and existing.object_id != ref.object_id and existing.object_id != expected_object_id:
        raise BlobConflict("Checkpoint changed")
    if existing is None and expected_object_id is not None:
        raise BlobConflict("Checkpoint missing")
    register_blob(session, ref)
    if existing is None:
        session.add(CheckpointBinding(**identity, object_id=ref.object_id))
    else:
        existing.object_id = ref.object_id
    session.flush()


@contextmanager
def materialize_checkpoint(settings, *, store, doc_id, generation_id, chunk_index, source_signature, scratch_root=None):
    identity = _identity(doc_id, generation_id, chunk_index, source_signature)
    with session_scope() as session:
        lock_generation_read(session, [doc_id])
        doc = session.get(Document, doc_id)
        generation = session.get(DocumentGeneration, generation_id)
        row = session.get(CheckpointBinding, identity)
        if (doc is None or doc.deleted_at is not None or generation is None or generation.doc_id != doc_id
                or generation.phase not in {"preparing", "ready", "active"} or row is None):
            raise BlobNotFound("Checkpoint missing")
        obj = session.get(ArtifactObject, row.object_id)
        if obj is None or obj.state != "ready":
            raise BlobNotFound("Checkpoint unavailable")
        ref = _ref(obj)
    with materialize(ref, store=LeasedBlobStore(store),
                     scratch_root=scratch_root or settings.artifact_scratch_root or settings.data_dir / "artifact-scratch") as path:
        yield path
