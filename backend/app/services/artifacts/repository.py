"""Transactional bindings. Authorization is the caller's responsibility."""
import re

from app.db.models import ArtifactBinding, ArtifactObject, UploadReceipt
from app.services.generation_store import lock_document_write
from .contract import BlobConflict, BlobRef

OWNER_TYPES = {"original", "source", "generation", "checkpoint", "export", "diagnostic_bundle"}
DOCUMENT_OWNERS = {"original", "source", "generation", "checkpoint"}


def _identity(owner_type, owner_id, generation_id, logical_name):
    if owner_type not in OWNER_TYPES:
        raise ValueError("Invalid artifact owner type")
    for value, limit in ((owner_id, 64), (logical_name, 512)):
        if not isinstance(value, str) or not 1 <= len(value) <= limit or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid artifact binding")
    if generation_id is not None and (not isinstance(generation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", generation_id)):
        raise ValueError("Invalid artifact generation")
    return {"owner_type": owner_type, "owner_id": owner_id, "generation_id": generation_id or "",
            "logical_name": logical_name}


def _ref(row):
    return BlobRef(row.object_id, row.key, row.sha256, row.size, row.media_type, row.store_id)


def attach_blob(session, *, ref, owner_type, owner_id, generation_id, logical_name):
    identity = _identity(owner_type, owner_id, generation_id, logical_name)
    if not isinstance(ref, BlobRef):
        raise ValueError("Invalid artifact reference")
    doc_id = owner_id if owner_type in DOCUMENT_OWNERS else None
    if doc_id is not None and not lock_document_write(session, doc_id, allow_deleted=False):
        raise BlobConflict("Artifact document missing or deleted")
    row = session.get(ArtifactObject, ref.object_id)
    if row is None:
        row = ArtifactObject(**vars(ref))
        session.add(row)
        session.flush()
    elif _ref(row) != ref:
        raise BlobConflict("Artifact reference already has different metadata")
    binding = session.get(ArtifactBinding, identity)
    if binding is not None:
        if binding.object_id != ref.object_id:
            raise BlobConflict("Artifact binding already exists")
        return
    session.add(ArtifactBinding(**identity, object_id=ref.object_id, doc_id=doc_id))
    session.flush()


def resolve_blob(session, *, owner_type, owner_id, generation_id, logical_name):
    binding = session.get(ArtifactBinding, _identity(owner_type, owner_id, generation_id, logical_name))
    if binding is None:
        return None
    row = session.get(ArtifactObject, binding.object_id)
    if row is None:
        raise BlobConflict("Artifact metadata missing")
    return _ref(row)


def record_upload(session, *, document_id, ref, receipt):
    session.info["artifact_upload"] = True
    attach_blob(session, ref=ref, owner_type="original", owner_id=document_id,
                generation_id=None, logical_name="original")
    if receipt is not None:
        session.add(UploadReceipt(document_id=document_id, **receipt))
        session.flush()
