"""Version extracted review originals before acknowledging a pending selection."""
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import delete, select

from app.db.models import ArtifactBinding, Document, DocumentProcessingReview
from app.services.errors import DomainError
from app.services.generation_files import GenerationStorageError, _reject_links, processing_review_paths
from app.services.generation_store import lock_document_write, lock_generation_read
from .contract import BlobRef
from .leased import TransactionLeasedBlobStore
from .materialize import materialize
from .publication import _atomic_write, _copy_file, _logical, _write_canonical, canonical_hash
from .repository import attach_blob, resolve_blob, document_uses_portable_artifacts


def _prefix(row):
    if not re.fullmatch(r"[0-9a-f]{32}", row.artifact_revision or ""):
        raise GenerationStorageError("Review artifact revision unavailable")
    return f"review/{row.id}/{row.artifact_revision}/"


def _inventory(row):
    result = {}
    aliases = {}
    for node in row.inventory_json.get("nodes", []):
        name = node.get("saved_path")
        if not name:
            continue
        _logical("attachments/" + name)
        if name == "_manifest" or Path(name).name != name or aliases.get(name.casefold(), name) != name:
            raise GenerationStorageError("Invalid review original name")
        aliases[name.casefold()] = name
        fingerprint = node.get("content_fingerprint")
        if name in result and result[name] != fingerprint:
            raise GenerationStorageError("Conflicting review original identity")
        result[name] = fingerprint
    return result


def _read_manifest(session, settings, row):
    from . import get_blob_store
    prefix = _prefix(row)
    ref = resolve_blob(session, owner_type="source", owner_id=row.doc_id, generation_id=None, logical_name=prefix + "_manifest")
    if ref is None:
        raise GenerationStorageError("Review artifact manifest unavailable")
    scratch = settings.artifact_scratch_root or settings.data_dir / "artifact-scratch"
    with materialize(ref, store=TransactionLeasedBlobStore(get_blob_store(settings), session), scratch_root=scratch) as path:
        if path.stat().st_size > 1024 * 1024:
            raise GenerationStorageError("Review artifact manifest exceeds limit")
        body = json.loads(path.read_text(encoding="utf8"))
    if (body.get("version") != 2 or body.get("doc_id") != row.doc_id or body.get("review_id") != row.id
            or body.get("source_file_hash") != row.source_file_hash or canonical_hash(body) != ref.sha256):
        raise GenerationStorageError("Review artifact identity changed")
    refs = {name: BlobRef(**value) for name, value in body["files"].items()}
    names = set(session.scalars(select(ArtifactBinding.logical_name).where(ArtifactBinding.owner_type == "source",
        ArtifactBinding.owner_id == row.doc_id, ArtifactBinding.generation_id == "", ArtifactBinding.logical_name.startswith(prefix))))
    if names != {prefix + name for name in refs} | {prefix + "_manifest"}:
        raise GenerationStorageError("Review artifact bindings changed")
    for name, member in refs.items():
        _logical("attachments/" + name)
        if member.store_id != ref.store_id or resolve_blob(session, owner_type="source", owner_id=row.doc_id,
                generation_id=None, logical_name=prefix + name) != member:
            raise GenerationStorageError("Review artifact binding unavailable")
    return refs


def seal_review_files(session, settings, row):
    if not document_uses_portable_artifacts(session, settings, row.doc_id) and row.artifact_version != 2:
        return
    if not lock_document_write(session, row.doc_id, allow_deleted=False):
        raise GenerationStorageError("Review document unavailable")
    from . import get_blob_store
    previous = _prefix(row) if row.artifact_version == 2 else None
    old_refs = _read_manifest(session, settings, row) if previous else {}
    root = processing_review_paths(settings, row.doc_id, row.id) / "attachments"
    store = get_blob_store(settings)
    refs, size = {}, 0
    for name, fingerprint in _inventory(row).items():
        path = root / name
        _reject_links(path)
        with path.open("rb") as source:
            ref = store.put(source, object_id=uuid4().hex, media_type="application/octet-stream")
        if fingerprint and ref.sha256 != fingerprint or ref.size > 50 * 1024 * 1024:
            raise GenerationStorageError("Review original changed or exceeds limit")
        if name in old_refs:
            # A local cache cannot silently repair a missing authoritative blob.
            with TransactionLeasedBlobStore(store, session).open(old_refs[name]):
                pass
        size += ref.size
        if size > 200 * 1024 * 1024:
            raise GenerationStorageError("Review originals exceed limit")
        refs[name] = ref
    body = {"version": 2, "doc_id": row.doc_id, "review_id": row.id, "source_file_hash": row.source_file_hash,
            "files": {name: vars(ref) for name, ref in refs.items()}}
    scratch = settings.artifact_scratch_root or settings.data_dir / "artifact-scratch"
    _reject_links(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="review-", dir=scratch) as folder:
        path = Path(folder) / "manifest.json"
        _write_canonical(path, body)
        with path.open("rb") as source:
            manifest = store.put(source, object_id=uuid4().hex, media_type="application/json")
    row.artifact_revision = uuid4().hex
    row.artifact_version = 2
    for name, ref in {**refs, "_manifest": manifest}.items():
        attach_blob(session, ref=ref, owner_type="source", owner_id=row.doc_id,
                    generation_id=None, logical_name=_prefix(row) + name)
    if previous:
        session.execute(delete(ArtifactBinding).where(ArtifactBinding.owner_type == "source", ArtifactBinding.owner_id == row.doc_id,
            ArtifactBinding.generation_id == "", ArtifactBinding.logical_name.startswith(previous)))
    session.info["portable_review_write"] = True
    session.flush()


def hydrate_review_files(session, settings, row):
    if row.artifact_version == 1:
        return False
    if row.artifact_version != 2:
        raise GenerationStorageError("Unknown review artifact protocol")
    try:
        lock_generation_read(session, [row.doc_id])
        document = session.get(Document, row.doc_id)
        current = session.get(DocumentProcessingReview, row.doc_id, populate_existing=True)
        if document is None or document.deleted_at is not None or current is None or current.id != row.id:
            raise GenerationStorageError("Review document unavailable")
        refs = _read_manifest(session, settings, row)
        names = _inventory(row)
        if set(refs) != set(names):
            raise GenerationStorageError("Incomplete review originals")
        from . import get_blob_store
        store = TransactionLeasedBlobStore(get_blob_store(settings), session)
        root = processing_review_paths(settings, row.doc_id, row.id) / "attachments"
        scratch = settings.artifact_scratch_root or settings.data_dir / "artifact-scratch"
        for name, ref in refs.items():
            if names[name] and ref.sha256 != names[name]:
                raise GenerationStorageError("Review original fingerprint changed")
            with materialize(ref, store=store, scratch_root=scratch) as source:
                _atomic_write(root / name, lambda output: _copy_file(source, output))
        return True
    except DomainError:
        raise
    except Exception as exc:
        raise GenerationStorageError("Review originals unavailable or changed") from exc


def retire_review_files(session, doc_id, review_id):
    session.execute(delete(ArtifactBinding).where(ArtifactBinding.owner_type == "source", ArtifactBinding.owner_id == doc_id,
        ArtifactBinding.generation_id == "", ArtifactBinding.logical_name.startswith(f"review/{review_id}/")))
