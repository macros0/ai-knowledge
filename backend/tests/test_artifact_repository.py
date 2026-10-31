from dataclasses import replace
import io

import pytest
from sqlalchemy import select

from app.db.models import ArtifactBinding, ArtifactObject, Document
from app.db.session import session_scope
from app.services.artifacts.contract import BlobConflict
from app.services.artifacts.local import LocalBlobStore
from app.services.artifacts.repository import attach_blob, resolve_blob

ID = "b" * 16


def test_binding_rollback_and_immutable_absent_generation(tmp_path):
    store = LocalBlobStore(tmp_path / "blobs", store_id="local")
    ref = store.put(io.BytesIO(b"bytes"), object_id="c" * 32, media_type="text/plain")
    scope = dict(owner_type="original", owner_id=ID, generation_id=None, logical_name="original")
    with pytest.raises(RuntimeError):
        with session_scope() as session:
            session.add(Document(id=ID, filename="a.pdf"))
            attach_blob(session, ref=ref, **scope)
            raise RuntimeError("abort")
    with session_scope() as session:
        assert session.get(Document, ID) is None
        assert not session.scalars(select(ArtifactObject)).all()
        assert not session.scalars(select(ArtifactBinding)).all()
        session.add(Document(id=ID, filename="a.pdf"))
        attach_blob(session, ref=ref, **scope)
        attach_blob(session, ref=ref, **scope)
        assert resolve_blob(session, **scope) == ref
        with pytest.raises(BlobConflict):
            attach_blob(session, ref=replace(ref, sha256="0" * 64), **scope)
        assert len(session.scalars(select(ArtifactBinding)).all()) == 1


def test_binding_cannot_attach_deleted_document(tmp_path):
    from datetime import datetime, timezone
    ref = LocalBlobStore(tmp_path, store_id="local").put(io.BytesIO(b"bytes"),
        object_id="c" * 32, media_type="text/plain")
    with session_scope() as session:
        session.add(Document(id=ID, filename="a.pdf", deleted_at=datetime.now(timezone.utc)))
        with pytest.raises(BlobConflict):
            attach_blob(session, ref=ref, owner_type="original", owner_id=ID, generation_id=None, logical_name="original")
