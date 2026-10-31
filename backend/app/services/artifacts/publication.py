"""Portable publication bytes and generation-scoped, verified local caches."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from tempfile import NamedTemporaryFile
from uuid import uuid4

from sqlalchemy import select

from app.db.models import ArtifactBinding, Document, DocumentGeneration
from app.db.session import session_scope
from app.services.generation_artifacts import GenerationIntegrityError, _read_hashed_json, artifact_manifest, file_digest
from app.services.generation_files import _reject_links, generation_paths
from app.services.generation_store import lock_document_write, lock_generation_read, lock_writable_generation
from app.services.json_atomic import write_json_atomic
from app.services.errors import DomainError
from app.services.storage import StorageFullError, is_storage_full
from .contract import BlobRef
from .leased import LeasedBlobStore
from .materialize import materialize
from .repository import attach_blob, resolve_blob


def _encoder():
    return json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
                            default=lambda value: value.isoformat())


def _wire_form(value):
    """Normalize JSON object keys without a second full serialized document."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                key = next(iter(json.loads(json.dumps({key: None}, allow_nan=False))))
            if key in result:
                raise ValueError("Duplicate JSON publication key")
            result[key] = _wire_form(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_wire_form(item) for item in value]
    return value


def canonical_hash(value):
    digest = hashlib.sha256()
    for part in _encoder().iterencode(_wire_form(value)):
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def _logical(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise GenerationIntegrityError()
    parts = value.split("/")
    if len(parts) < 2 or parts[0] not in {"attachments", "bundle"}:
        raise GenerationIntegrityError()
    for part in parts:
        if (not part or part in {".", ".."} or len(part) > 255 or part.endswith((".", " "))
                or any(ord(c) < 32 or ord(c) == 127 or c in '\\:<>"|?*' for c in part)
                or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.I)):
            raise GenerationIntegrityError()
    return value


def artifact_path(paths, logical):
    parts = _logical(logical).split("/")
    root = paths.attachments if parts[0] == "attachments" else paths.bundle
    target = root.joinpath(*parts[1:])
    _reject_links(target)
    return target


def _validate_semantics(body, names):
    for name in ("concepts", "chunks", "attachments", "point_ids"):
        if not isinstance(body.get(name), list):
            raise GenerationIntegrityError()
    if body.get("sources") is not None and not isinstance(body.get("sources"), list):
        raise GenerationIntegrityError()
    for name in ("document_fields", "index_metadata", "parse_effects"):
        if not isinstance(body.get(name), dict):
            raise GenerationIntegrityError()
    required_files = body.get("bundle_files_enabled", True)
    if type(required_files) is not bool:
        raise GenerationIntegrityError()
    for concept in body["concepts"]:
        if not isinstance(concept, dict):
            raise GenerationIntegrityError()
        name = _logical(concept.get("filepath"))
        if not name.startswith("bundle/") or (required_files and name not in names):
            raise GenerationIntegrityError()
    prefix = f"generations/{body['generation_id']}/"
    for item in [*(body.get("sources") or []), *body["attachments"]]:
        if not isinstance(item, dict):
            raise GenerationIntegrityError()
        saved = item.get("saved_path")
        if saved is not None and not isinstance(saved, str):
            raise GenerationIntegrityError()
        if saved and (not saved.startswith(prefix) or _logical(saved[len(prefix):]) not in names):
            raise GenerationIntegrityError()


def validate_v2(body, doc_id, generation_id):
    try:
        if (not isinstance(body, dict) or type(body.get("version")) is not int or body["version"] != 2
                or body.get("doc_id") != doc_id or body.get("generation_id") != generation_id
                or not isinstance(body.get("artifacts"), dict)):
            raise GenerationIntegrityError()
        refs = {_logical(name): BlobRef(**value) for name, value in body["artifacts"].items()}
        if len({name.casefold() for name in refs}) != len(refs):
            raise GenerationIntegrityError()
        _validate_semantics(body, refs)
        return refs
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        if isinstance(exc, GenerationIntegrityError):
            raise
        raise GenerationIntegrityError() from exc


def local_publication(body, paths):
    """Rebase identity paths only after verifying the original canonical bytes."""
    result = deepcopy(body)
    for concept in result["concepts"]:
        concept["filepath"] = str(artifact_path(paths, concept["filepath"]))
    return result


def verify_bindings(session, doc_id, generation_id, refs, expected_hash, size):
    generation = session.get(DocumentGeneration, generation_id)
    if (generation is None or generation.doc_id != doc_id or generation.publication_version != 2
            or generation.publication_hash != expected_hash or generation.phase not in {"ready", "active"}):
        raise GenerationIntegrityError()
    names = set(session.scalars(select(ArtifactBinding.logical_name).where(
        ArtifactBinding.owner_type == "generation", ArtifactBinding.owner_id == doc_id,
        ArtifactBinding.generation_id == generation_id)))
    manifest = resolve_blob(session, owner_type="generation", owner_id=doc_id,
                            generation_id=generation_id, logical_name="publication.json")
    if (names != {*refs, "publication.json"} or manifest is None or manifest.sha256 != expected_hash
            or manifest.size != size):
        raise GenerationIntegrityError()
    for name, ref in refs.items():
        registered = resolve_blob(session, owner_type="generation", owner_id=doc_id,
                                  generation_id=generation_id, logical_name=name)
        if registered != ref or ref.store_id != manifest.store_id:
            raise GenerationIntegrityError()


def _atomic_write(target, writer):
    _reject_links(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix=".artifact-cache-", delete=False) as output:
            temporary = Path(output.name)
            writer(output)
            output.flush()
            os.fsync(output.fileno())
        _reject_links(target)
        os.replace(temporary, target)
    finally:
        active_error = sys.exc_info()[0] is not None
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                if not active_error:
                    raise


def _write_canonical(path, body):
    def write(output):
        for part in _encoder().iterencode(_wire_form(body)):
            output.write(part.encode("utf-8"))
    _atomic_write(path, write)


def _cache_file(settings, doc_id, generation_id, manifest, name, ref, source):
    # Provider materialization has finished/released its read lease before this
    # transaction starts. Serialize local cache publication with retired cleanup.
    with session_scope() as session:
        if not lock_document_write(session, doc_id, allow_deleted=False):
            raise GenerationIntegrityError()
        generation = session.get(DocumentGeneration, generation_id, populate_existing=True)
        current = resolve_blob(session, owner_type="generation", owner_id=doc_id,
                               generation_id=generation_id, logical_name=name)
        if (generation is None or generation.doc_id != doc_id or generation.phase not in {"ready", "active"}
                or generation.publication_hash != manifest.sha256 or current != ref):
            raise GenerationIntegrityError()
        paths = generation_paths(settings, doc_id, generation_id)
        target = paths.uploads_root / name if name == "publication.json" else artifact_path(paths, name)
        _atomic_write(target, lambda output: _copy_file(source, output))


def _put_bound(session, store, path, doc_id, generation_id, name, expected, media_type):
    existing = resolve_blob(session, owner_type="generation", owner_id=doc_id,
                            generation_id=generation_id, logical_name=name)
    if existing and (existing.sha256 != expected or existing.size != path.stat().st_size):
        raise GenerationIntegrityError()
    with path.open("rb") as source:
        ref = store.put(source, object_id=existing.object_id if existing else uuid4().hex, media_type=media_type)
    if ref.sha256 != expected:
        raise GenerationIntegrityError()
    attach_blob(session, ref=ref, owner_type="generation", owner_id=doc_id,
                generation_id=generation_id, logical_name=name)
    return ref


def write_prepared_publication(session, settings, prepared):
    """Bind every durable file plus the canonical manifest in the caller's SQL transaction."""
    doc_id, generation_id = prepared["doc_id"], prepared["generation_id"]
    paths = generation_paths(settings, doc_id, generation_id)
    path = paths.uploads_root / "publication.json"
    version = prepared.get("version", 1)
    if type(version) is not int or version not in {1, 2}:
        raise GenerationIntegrityError()
    if not getattr(settings, "artifact_uploads_enabled", False):
        generation = session.get(DocumentGeneration, generation_id)
        if ((generation and generation.publication_version != 1) or resolve_blob(session, owner_type="generation", owner_id=doc_id,
                         generation_id=generation_id, logical_name="publication.json") is not None):
            raise GenerationIntegrityError()
        write_json_atomic(path, prepared)
        return file_digest(path)
    try:
        actual = artifact_manifest(settings, doc_id, generation_id)
        if prepared.get("artifacts") != actual:
            raise GenerationIntegrityError()
        body = _wire_form(prepared)
        body["version"] = 2
        body["bundle_files_enabled"] = bool(settings.okf_write_bundles)
        for concept in body["concepts"]:
            old = concept["filepath"]
            if old.startswith("bundle/"):
                logical = old
            elif not body["bundle_files_enabled"] and len(Path(old).parts) == 1:
                logical = "bundle/" + old
            else:
                logical = "bundle/" + Path(old).absolute().relative_to(paths.bundle.absolute()).as_posix()
            concept["filepath"] = _logical(logical)
        if len({name.casefold() for name in actual}) != len(actual):
            raise GenerationIntegrityError()
        for name in actual:
            _logical(name)
        _validate_semantics(body, actual)
        generation = lock_writable_generation(session, doc_id, generation_id)
        from . import get_blob_store
        store = get_blob_store(settings)
        body["artifacts"] = {name: vars(_put_bound(session, store, artifact_path(paths, name),
            doc_id, generation_id, name, expected, "application/octet-stream")) for name, expected in actual.items()}
        validate_v2(body, doc_id, generation_id)
        digest = canonical_hash(body)
        _write_canonical(path, body)
        _put_bound(session, store, path, doc_id, generation_id, "publication.json", digest, "application/json")
        generation.publication_version = 2
        session.flush()
        return digest
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        if isinstance(exc, DomainError):
            raise
        if isinstance(exc, OSError) and is_storage_full(exc):
            raise StorageFullError() from None
        raise GenerationIntegrityError() from exc


def hydrate_publication(settings, doc_id, generation_id, *, skip_active=False):
    """Read provider bytes before any publication SQL write lock/transaction."""
    try:
        generation_paths(settings, doc_id, generation_id)
        with session_scope() as session:
            lock_generation_read(session, [doc_id])
            generation = session.get(DocumentGeneration, generation_id)
            doc = session.get(Document, doc_id)
            if skip_active and generation and generation.doc_id == doc_id and generation.phase == "active":
                return False
            manifest = resolve_blob(session, owner_type="generation", owner_id=doc_id,
                                    generation_id=generation_id, logical_name="publication.json")
            if manifest is None:
                if generation and generation.doc_id == doc_id and generation.publication_version != 1:
                    raise GenerationIntegrityError()
                return False  # Legacy filesystem publication.
            if (doc is None or doc.deleted_at is not None or generation is None or generation.doc_id != doc_id
                    or generation.phase not in {"ready", "active"} or generation.publication_hash != manifest.sha256
                    or generation.publication_version != 2):
                raise GenerationIntegrityError()
            names = set(session.scalars(select(ArtifactBinding.logical_name).where(
                ArtifactBinding.owner_type == "generation", ArtifactBinding.owner_id == doc_id,
                ArtifactBinding.generation_id == generation_id)))
            bindings = {name: resolve_blob(session, owner_type="generation", owner_id=doc_id,
                generation_id=generation_id, logical_name=name) for name in names}
        from . import get_blob_store
        store = LeasedBlobStore(get_blob_store(settings))
        scratch = settings.artifact_scratch_root or settings.data_dir / "artifact-scratch"
        with materialize(manifest, store=store, scratch_root=scratch) as source:
            body = _read_hashed_json(source, manifest.sha256)
            refs = validate_v2(body, doc_id, generation_id)
            if (canonical_hash(body) != manifest.sha256 or names != {*refs, "publication.json"}
                    or any(bindings[name] != ref for name, ref in refs.items())):
                raise GenerationIntegrityError()
            for name, ref in refs.items():
                with materialize(ref, store=store, scratch_root=scratch) as value:
                    _cache_file(settings, doc_id, generation_id, manifest, name, ref, value)
            _cache_file(settings, doc_id, generation_id, manifest, "publication.json", manifest, source)
        return True
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        if isinstance(exc, DomainError):
            raise
        if isinstance(exc, OSError) and is_storage_full(exc):
            raise StorageFullError() from None
        raise GenerationIntegrityError() from exc


def _copy_file(source, output):
    with source.open("rb") as incoming:
        shutil.copyfileobj(incoming, output, length=64 * 1024)
