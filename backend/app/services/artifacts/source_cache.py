"""Portable, generation-scoped source results with an atomic SQL head."""
from contextlib import ExitStack, contextmanager, nullcontext
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import delete, select

from app.db.models import ArtifactBinding, ArtifactObject, Document, DocumentGeneration, SourceCacheHead
from app.db.session import current_session, session_scope
from app.services.generation_files import _reject_links, generation_paths
from app.services.generation_store import lock_document_write, lock_generation_read, lock_writable_generation
from app.services.generation_artifacts import GenerationIntegrityError
from app.services.errors import DomainError
from app.services.processing_scope_cache import SourceResultCache, STAGES, _hash
from app.services.storage import StorageFullError, is_storage_full
from .contract import BlobRef, BlobUnavailable
from .leased import TransactionLeasedBlobStore
from .materialize import materialize
from .publication import _atomic_write, _write_canonical
from .repository import _ref, attach_blob, resolve_blob


def _names(manifest):
    result = set()
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("stages"), dict) or set(manifest["stages"]) - set(STAGES):
        raise ValueError("Invalid source cache manifest")
    for stage, info in manifest["stages"].items():
        name = info.get("file", "")
        if not re.fullmatch(stage + r"-[0-9a-f]{64}\.jsonl", name) or info.get("sha256") != name[len(stage) + 1:-6]:
            raise ValueError("Invalid source cache member")
        result.add(name)
    original = manifest.get("original")
    if original:
        name = original.get("file", "")
        if not re.fullmatch(r"original\.[a-z0-9]{1,8}", name):
            raise ValueError("Invalid source original")
        result.add(name)
    return result


class PortableSourceCache:
    def __init__(self, owner, settings):
        self.owner, self.settings, self.doc_id = owner, settings, owner.doc_id
        root = owner.root
        if root.name == "scope-stage-cache" and root.parent.name == self.doc_id:
            self.generation_id = ""
            owner.root = settings.uploads_dir / self.doc_id / "scope-stage-cache"
        elif (root.name == "source-cache" and root.parent.parent.name == "generations"
              and root.parent.parent.parent.name == self.doc_id and re.fullmatch(r"[0-9a-f]{32}", root.parent.name)):
            self.generation_id = root.parent.name
            owner.root = generation_paths(settings, self.doc_id, self.generation_id).bundle / "source-cache"
        else:
            raise ValueError("Unknown portable source cache scope")
        _reject_links(owner.root)

    def _head_key(self, source):
        return self.doc_id, self.generation_id, self.owner.identity(source)

    def _prefix(self, source, object_id):
        return f"source-cache/{self.owner.identity(source)}/{object_id}/"

    def _owner(self, session, *, write=False):
        if write:
            if not lock_document_write(session, self.doc_id, allow_deleted=False):
                raise ValueError("Source cache document unavailable")
            if self.generation_id:
                lock_writable_generation(session, self.doc_id, self.generation_id)
        else:
            lock_generation_read(session, [self.doc_id])
        doc = session.get(Document, self.doc_id, populate_existing=True)
        if doc is None or doc.deleted_at is not None:
            raise ValueError("Source cache document unavailable")
        if self.generation_id:
            generation = session.get(DocumentGeneration, self.generation_id)
            if (generation is None or generation.doc_id != self.doc_id
                    or generation.phase not in {"preparing", "ready", "active"}):
                raise ValueError("Source cache generation unavailable")
        return doc

    @contextmanager
    def _local(self, session, source):
        head = session.get(SourceCacheHead, self._head_key(source), populate_existing=True)
        with ExitStack() as stack:
            try:
                result = stack.enter_context(self._unverified_local(session, source))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                if is_storage_full(exc):
                    raise StorageFullError() from exc
                if head is not None:
                    raise GenerationIntegrityError() from exc
                raise
            yield result

    @contextmanager
    def _unverified_local(self, session, source):
        from . import get_blob_store
        root = self.settings.artifact_scratch_root or self.settings.data_dir / "artifact-scratch"
        _reject_links(root)
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="source-cache-", dir=root) as directory:
            local = SourceResultCache(Path(directory), self.doc_id, _local_only=True)
            head = session.get(SourceCacheHead, self._head_key(source), populate_existing=True)
            refs = {}
            if head is not None:
                obj = session.get(ArtifactObject, head.object_id)
                if obj is None or obj.state != "ready":
                    raise ValueError("Source cache metadata unavailable")
                prefix = self._prefix(source, head.object_id)
                manifest_ref = resolve_blob(session, owner_type="source", owner_id=self.doc_id,
                    generation_id=self.generation_id or None, logical_name=prefix + "manifest")
                if manifest_ref != _ref(obj):
                    raise ValueError("Source cache manifest binding unavailable")
                store = TransactionLeasedBlobStore(get_blob_store(self.settings), session)
                with materialize(manifest_ref, store=store, scratch_root=root) as path:
                    if path.stat().st_size > 512 * 1024:
                        raise ValueError("Source cache manifest exceeds limit")
                    body = json.loads(path.read_text(encoding="utf8"))
                manifest = body["manifest"]
                if (type(body.get("version")) is not int or body["version"] != 2 or body.get("cache_id") != self.owner.identity(source)
                        or manifest.get("doc_id") != self.doc_id or manifest.get("source_key") != source["source_key"]
                        or manifest.get("content_fingerprint") != source["content_fingerprint"]):
                    raise ValueError("Source cache identity mismatch")
                refs = {name: BlobRef(**ref) for name, ref in body["files"].items()}
                if set(refs) != _names(manifest):
                    raise ValueError("Incomplete source cache")
                registered = set(session.scalars(select(ArtifactBinding.logical_name).where(
                    ArtifactBinding.owner_type == "source", ArtifactBinding.owner_id == self.doc_id,
                    ArtifactBinding.generation_id == self.generation_id, ArtifactBinding.logical_name.startswith(prefix))))
                if registered != {prefix + name for name in refs} | {prefix + "manifest"}:
                    raise ValueError("Source cache bindings changed")
                folder = local.folder(source)
                for name, ref in refs.items():
                    if (ref.store_id != manifest_ref.store_id or resolve_blob(session, owner_type="source", owner_id=self.doc_id,
                            generation_id=self.generation_id or None, logical_name=prefix + name) != ref):
                        raise ValueError("Source cache member binding unavailable")
                    with materialize(ref, store=store, scratch_root=root) as path:
                        _atomic_write(folder / name, lambda out: _copy(path, out))
                _write_canonical(folder / "manifest.json", manifest)
            else:
                known = session.scalar(select(ArtifactBinding.object_id).where(ArtifactBinding.owner_type == "source",
                    ArtifactBinding.owner_id == self.doc_id, ArtifactBinding.generation_id == self.generation_id,
                    ArtifactBinding.logical_name.startswith(f"source-cache/{self.owner.identity(source)}/")).limit(1))
                if known:
                    raise GenerationIntegrityError()
                path = self.owner.folder(source) / "manifest.json"
                if path.exists() and session.get(Document, self.doc_id).source_cache_version != 2:
                    with path.open("rb") as stream:
                        payload = stream.read(512 * 1024 + 1)
                    if len(payload) > 512 * 1024 or json.loads(payload).get("schema_version") != 2:
                        raise ValueError("Legacy source cache requires explicit migration")
            yield local, head.object_id if head else None, refs

    def _seal(self, session, source, local, expected, previous_refs):
        from . import get_blob_store
        self._owner(session, write=True)
        current = session.get(SourceCacheHead, self._head_key(source), populate_existing=True)
        if (current.object_id if current else None) != expected:
            raise ValueError("Source cache head changed")
        manifest = local._manifest(source)
        store = get_blob_store(self.settings)
        refs = {}
        for name in _names(manifest):
            path = local.folder(source) / name
            ref = previous_refs.get(name)
            if ref is None or ref.sha256 != _hash(path) or ref.size != path.stat().st_size:
                with path.open("rb") as stream:
                    ref = store.put(stream, object_id=uuid4().hex, media_type="application/octet-stream")
            refs[name] = ref
        body = {"version": 2, "cache_id": self.owner.identity(source), "manifest": manifest,
                "files": {name: vars(ref) for name, ref in refs.items()}}
        path = local.root / "portable-manifest.json"
        _write_canonical(path, body)
        with path.open("rb") as stream:
            ref = store.put(stream, object_id=uuid4().hex, media_type="application/json")
        prefix = self._prefix(source, ref.object_id)
        for name, member in {**refs, "manifest": ref}.items():
            attach_blob(session, ref=member, owner_type="source", owner_id=self.doc_id,
                        generation_id=self.generation_id or None, logical_name=prefix + name)
        if current is None:
            session.add(SourceCacheHead(doc_id=self.doc_id, generation_id=self.generation_id,
                cache_id=self.owner.identity(source), source_key=source["source_key"], content_fingerprint=source["content_fingerprint"], object_id=ref.object_id))
        else:
            current.object_id = ref.object_id
        if expected:
            session.execute(delete(ArtifactBinding).where(ArtifactBinding.owner_type == "source", ArtifactBinding.owner_id == self.doc_id,
                ArtifactBinding.generation_id == self.generation_id, ArtifactBinding.logical_name.startswith(self._prefix(source, expected))))
        session.get(Document, self.doc_id).source_cache_version = 2
        session.info["portable_cache_write"] = True
        session.flush()

    def _cache(self, source, local):
        manifest = local._manifest(source)
        for name in _names(manifest):
            _atomic_write(self.owner.folder(source) / name, lambda out: _copy(local.folder(source) / name, out))
        # A rolled-back first write must never look like a valid legacy cache.
        _write_canonical(self.owner.folder(source) / "manifest.json", {**manifest, "schema_version": 2})

    @contextmanager
    def _operation(self, method, source):
        parent = current_session()
        with (nullcontext(parent) if parent is not None else session_scope()) as session:
            if method == "copy_to" and not lock_document_write(session, self.doc_id, allow_deleted=False):
                raise ValueError("Source cache document unavailable")
            self._owner(session, write=method in {"save_stage", "save_original"})
            with self._local(session, source) as (local, expected, refs):
                yield session, local, expected, refs

    def _iterate(self, source, *args, **kwargs):
        try:
            with self._operation("iter_stage_records", source) as (_, local, _, _):
                yield from local.iter_stage_records(source, *args, **kwargs)
        except OSError as exc:
            if is_storage_full(exc):
                raise StorageFullError() from exc
            raise ValueError("Source cache bytes unavailable") from exc

    def run(self, method, source, *args, **kwargs):
        if method == "iter_stage_records":
            return self._iterate(source, *args, **kwargs)
        try:
            with self._operation(method, source) as (session, local, expected, refs):
                if method == "copy_to":
                    destination = args[0]
                    adapter = destination._portable()
                    if adapter is None:
                        raise ValueError("Cannot downgrade portable source cache")
                    adapter._owner(session, write=True)
                    with adapter._local(session, source) as (target, target_id, target_refs):
                        local.copy_to(source, target)
                        adapter._seal(session, source, target, target_id, target_refs)
                        adapter._cache(source, target)
                        return target.inspect(source)
                result = getattr(local, method)(source, *args, **kwargs)
                if method in {"save_stage", "save_original"}:
                    self._seal(session, source, local, expected, refs)
                if method in {"original_path", "save_original", "save_stage"}:
                    self._cache(source, local)
                if method in {"original_path", "save_original"}:
                    return self.owner.folder(source) / Path(result).name
                return result
        except DomainError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if is_storage_full(exc):
                raise StorageFullError() from exc
            if method == "inspect":
                return {"verified": False, "cache_id": None, "stages": dict.fromkeys(STAGES, False), "counts": {}}
            if isinstance(exc, ValueError) or isinstance(exc, OSError) and not isinstance(exc, BlobUnavailable):
                raise
            raise ValueError("Source cache bytes unavailable") from exc


def _copy(path, output):
    with path.open("rb") as source:
        shutil.copyfileobj(source, output, 65536)
