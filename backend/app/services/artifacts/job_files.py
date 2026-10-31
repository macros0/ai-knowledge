"""Immutable job results; job status/audit and bindings share one transaction."""
from contextlib import ExitStack
import hashlib
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, update

from app.db.models import ArtifactBinding
from app.services.generation_files import _reject_links
from .contract import BlobIntegrityError, BlobNotFound
from .leased import LeasedBlobStore
from .materialize import materialize
from .publication import _logical
from .repository import attach_blob, resolve_blob


def lock_job(session, model, job_id):
    session.flush()
    session.execute(update(model).where(model.id == job_id).values(status=model.status)
                    .execution_options(synchronize_session=False))
    return session.get(model, job_id, populate_existing=True)


def bind_file(session, settings, *, kind, job_id, filename, path, size, sha256=None):
    from . import get_blob_store
    _logical("bundle/" + filename)
    _reject_links(Path(path))
    with Path(path).open("rb") as stream:
        expected = sha256 or hashlib.file_digest(stream, "sha256").hexdigest()
        stream.seek(0)
        ref = get_blob_store(settings).put(stream, object_id=uuid4().hex, media_type="application/zip")
    if ref.size != size or ref.sha256 != expected:
        raise BlobIntegrityError("Job artifact changed")
    attach_blob(session, ref=ref, owner_type=kind, owner_id=str(job_id), generation_id=None, logical_name=filename)
    return ref


def file_ref(session, *, kind, job_id, filename, size, sha256=None):
    _logical("bundle/" + filename)
    ref = resolve_blob(session, owner_type=kind, owner_id=str(job_id), generation_id=None, logical_name=filename)
    if ref is None or ref.size != size or sha256 is not None and ref.sha256 != sha256:
        raise BlobNotFound("Job artifact unavailable")
    return ref


def pin_file(settings, ref):
    from . import get_blob_store
    stack = ExitStack()
    try:
        path = stack.enter_context(materialize(ref, store=LeasedBlobStore(get_blob_store(settings)),
            scratch_root=settings.artifact_scratch_root or settings.data_dir / "artifact-scratch"))
        return stack, path
    except BaseException:
        stack.close()
        raise


def retire_files(session, *, kind, job_id):
    session.execute(delete(ArtifactBinding).where(ArtifactBinding.owner_type == kind, ArtifactBinding.owner_id == str(job_id)))
