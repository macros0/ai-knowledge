"""Verified cache bridge for explicitly migrated pre-generation publications."""
from contextlib import nullcontext
import json

from sqlalchemy import select

from app.db.models import ArtifactBinding, Document, DocumentGeneration
from app.db.session import current_session, session_scope
from app.services.generation_artifacts import GenerationIntegrityError
from app.services.generation_files import GenerationPaths, generation_paths
from app.services.generation_store import lock_document_write
from .contract import BlobRef
from .leased import TransactionLeasedBlobStore
from .materialize import materialize
from .publication import _atomic_write, _copy_file, _logical, artifact_path, canonical_hash
from .repository import resolve_blob


def snapshot_ref(session, doc_id, generation_id, logical):
    prefix = "migration/" if generation_id else "legacy/"
    row = session.get(DocumentGeneration, generation_id) if generation_id else session.get(Document, doc_id)
    object_id = getattr(row, "migration_artifacts_id" if generation_id else "legacy_artifacts_id", None)
    version = getattr(row, "migration_artifacts_version" if generation_id else "legacy_artifacts_version", 1)
    if version not in {1, 2} or (version == 2) != (object_id is not None):
        raise GenerationIntegrityError()
    if object_id is None:
        return None
    manifest = resolve_blob(session, owner_type="source", owner_id=doc_id, generation_id=generation_id, logical_name=prefix + "_manifest")
    ref = resolve_blob(session, owner_type="source", owner_id=doc_id, generation_id=generation_id, logical_name=prefix + _logical(logical))
    if manifest is None or manifest.object_id != object_id or ref is None or ref.store_id != manifest.store_id:
        raise GenerationIntegrityError()
    return ref


def hydrate_snapshot(settings, doc_id, generation_id=None):
    parent = current_session()
    with (nullcontext(parent) if parent is not None else session_scope()) as session:
        row = session.get(DocumentGeneration, generation_id) if generation_id else session.get(Document, doc_id)
        object_id = getattr(row, "migration_artifacts_id" if generation_id else "legacy_artifacts_id", None)
        version = getattr(row, "migration_artifacts_version" if generation_id else "legacy_artifacts_version", 1)
        if version not in {1, 2} or (version == 2) != (object_id is not None):
            raise GenerationIntegrityError()
        if object_id is None:
            return False
        if not lock_document_write(session, doc_id, allow_deleted=False):
            raise GenerationIntegrityError()
        if generation_id and (row.doc_id != doc_id or row.phase not in {"preparing", "ready", "active"}):
            raise GenerationIntegrityError()
        prefix = "migration/" if generation_id else "legacy/"
        ref = resolve_blob(session, owner_type="source", owner_id=doc_id, generation_id=generation_id, logical_name=prefix + "_manifest")
        if ref is None or ref.object_id != object_id:
            raise GenerationIntegrityError()
        from . import get_blob_store
        store = TransactionLeasedBlobStore(get_blob_store(settings), session)
        scratch = settings.artifact_scratch_root or settings.data_dir / "artifact-scratch"
        with materialize(ref, store=store, scratch_root=scratch) as path:
            body = json.loads(path.read_text(encoding="utf8"))
        if (body.get("version") != 2 or body.get("doc_id") != doc_id or body.get("generation_id") != generation_id
                or canonical_hash(body) != ref.sha256):
            raise GenerationIntegrityError()
        files = {name: BlobRef(**value) for name, value in body["files"].items()}
        names = set(session.scalars(select(ArtifactBinding.logical_name).where(ArtifactBinding.owner_type == "source",
            ArtifactBinding.owner_id == doc_id, ArtifactBinding.generation_id == (generation_id or ""),
            ArtifactBinding.logical_name.startswith(prefix))))
        if names != {prefix + name for name in files} | {prefix + "_manifest"}:
            raise GenerationIntegrityError()
        paths = generation_paths(settings, doc_id, generation_id) if generation_id else GenerationPaths(
            settings.uploads_dir / doc_id, settings.uploads_dir / doc_id / "attachments", settings.okf_dir / doc_id)
        for name, member in files.items():
            if snapshot_ref(session, doc_id, generation_id, name) != member:
                raise GenerationIntegrityError()
            with materialize(member, store=store, scratch_root=scratch) as source:
                _atomic_write(artifact_path(paths, name), lambda out: _copy_file(source, out))
        return True
