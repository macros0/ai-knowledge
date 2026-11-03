"""Bridge for the combined pipeline until all file consumers use materialize."""
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from app.db.models import Document
from app.db.session import current_session, session_scope
from app.services.generation_store import lock_document_write, lock_generation_read
from app.services.diagnostics.paths import assert_no_links, canonical_root
from .contract import BlobConflict
from .local import _storage_failure
from .materialize import materialize
from .repository import resolve_blob
from .leased import LeasedBlobStore, TransactionLeasedBlobStore


def _scoped_original(settings, doc_id, legacy):
    from .original_scope import current_original_scope
    scope = current_original_scope()
    if scope is None:
        raise ValueError("Distributed originals require a request or work scratch scope")
    parent = current_session()
    if parent is not None:
        lock_generation_read(parent, [doc_id])
        doc = parent.get(Document, doc_id)
        if doc is None or doc.deleted_at is not None:
            raise BlobConflict("Original document missing or deleted")
        ref = resolve_blob(parent, owner_type="original", owner_id=doc_id, generation_id=None, logical_name="original")
    else:
        # Leaving the context, not merely committing it, is essential: work
        # scopes otherwise reuse this Session for lease writes via SAVEPOINT.
        with session_scope() as session:
            lock_generation_read(session, [doc_id])
            doc = session.get(Document, doc_id)
            if doc is None or doc.deleted_at is not None:
                raise BlobConflict("Original document missing or deleted")
            ref = resolve_blob(session, owner_type="original", owner_id=doc_id, generation_id=None, logical_name="original")
    if ref is None:
        return legacy
    key = (str(settings.uploads_dir.absolute()), doc_id, ref.store_id, ref.object_id, ref.sha256)
    if key not in scope.paths:
        from . import get_blob_store
        store = get_blob_store(settings)
        leased = TransactionLeasedBlobStore(store, parent) if parent is not None else LeasedBlobStore(store)
        root = settings.artifact_scratch_root or settings.data_dir / "artifact-scratch"
        scope.paths[key] = scope.stack.enter_context(materialize(ref, store=leased, scratch_root=root))
    return scope.paths[key]


def original_path(settings, doc_id, filename, *, legacy_path=None):
    legacy = Path(legacy_path) if legacy_path is not None else settings.uploads_dir / f"{doc_id}{Path(filename).suffix.lower()}"
    if settings.okf_deployment_schema == "replicas-v1":
        return _scoped_original(settings, doc_id, legacy)
    parent = current_session()
    if parent is not None:
        lock_generation_read(parent, [doc_id])
        doc = parent.get(Document, doc_id)
        if doc is None or doc.deleted_at is not None:
            raise BlobConflict("Original document missing or deleted")
        ref = resolve_blob(parent, owner_type="original", owner_id=doc_id, generation_id=None, logical_name="original")
        if ref is None:
            return legacy
        from . import get_blob_store
        from .publication import _atomic_write, _copy_file
        root = canonical_root(settings.uploads_dir)
        target = root / f"{doc_id}{Path(doc.filename).suffix.lower()}"
        assert_no_links(target, root)
        with materialize(ref, store=TransactionLeasedBlobStore(get_blob_store(settings), parent),
                         scratch_root=settings.artifact_scratch_root or settings.data_dir / "artifact-scratch") as source:
            _atomic_write(target, lambda output: _copy_file(source, output))
        return target
    with session_scope() as session:
        lock_generation_read(session, [doc_id])
        ref = resolve_blob(session, owner_type="original", owner_id=doc_id,
                           generation_id=None, logical_name="original")
        if ref is None:
            return legacy
        doc = session.get(Document, doc_id)
        if doc is None or doc.deleted_at is not None:
            raise BlobConflict("Original document missing or deleted")
        # Lease transactions must not wait on our own SQLite read snapshot.
        session.commit()
        # Bound originals never trust the caller's legacy path or filename.
        root = canonical_root(settings.uploads_dir)
        target = root / f"{doc_id}{Path(doc.filename).suffix.lower()}"
        assert_no_links(target, root)
        temporary = root / f".{doc_id}.{uuid4().hex}.tmp"
        from . import get_blob_store
        try:
            root.mkdir(parents=True, exist_ok=True)
            with materialize(ref, store=LeasedBlobStore(get_blob_store(settings)),
                             scratch_root=settings.artifact_scratch_root or settings.data_dir / "artifact-scratch") as source:
                with source.open("rb") as incoming, temporary.open("xb") as output:
                    shutil.copyfileobj(incoming, output, length=64 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                if not lock_document_write(session, doc_id, allow_deleted=False):
                    raise BlobConflict("Original document missing or deleted")
                os.replace(temporary, target)
            return target
        except OSError as exc:
            raise _storage_failure(exc) from None
        finally:
            active_error = sys.exc_info()[0] is not None
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                if not active_error:
                    raise _storage_failure(exc) from None
