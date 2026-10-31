"""Immutable chunk snapshots; staging files are verified, disposable caches."""
from copy import deepcopy
from hashlib import sha256
from itertools import islice
import json
import shutil
from tempfile import TemporaryDirectory
from pathlib import Path
from uuid import uuid4

from sqlalchemy import ARRAY, Text, cast, delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB

from app.db.models import ArtifactObject, CheckpointBinding, Document, DocumentGeneration, DocumentStaging
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.errors import DomainError
from app.services.generation_artifacts import GenerationIntegrityError
from app.services.generation_files import _check_tree, _reject_links
from app.services.generation_store import lock_document_write, lock_generation_read, lock_writable_generation
from app.services.storage import StorageFullError, is_storage_full
from .checkpoints import _identity, attach_checkpoint
from .leased import LeasedBlobStore
from .materialize import materialize
from .publication import _atomic_write, _write_canonical, canonical_hash
from .repository import _ref

ROOT_FIELDS = ("total_chunks", "parser_version", "source_file_hash", "processing_scope_hash", "generation_id", "checkpoint_version")


def _metadata(info):
    result = deepcopy(info)
    result.pop("checkpoint", None)
    if "table_refs" in result:
        refs = result.pop("table_refs")
        result.pop("table_refs_file", None)
        if refs:
            payload = json.dumps(refs, ensure_ascii=False, indent=None, sort_keys=True).encode("utf8")
            if not isinstance(refs, list) or len(payload) > 4 * 1024 * 1024:
                raise GenerationIntegrityError()
            result["table_refs_file"] = f"table_refs_{sha256(payload).hexdigest()}.json"
        else:
            result["table_refs"] = refs
    return result


def _signature(snapshot, index, body):
    return canonical_hash({name: snapshot[name] for name in ("parser_version", "source_file_hash", "processing_scope_hash")}
        | {"index": index, "source_id": body["info"].get("source_id", "root"),
           "text_hash": sha256((body["text"] or "").encode("utf8")).hexdigest()})


class PortableStaging:
    def __init__(self, owner, settings):
        self.owner, self.settings, self.doc_id = owner, settings, owner.doc_id

    def _store(self):
        from . import get_blob_store
        return get_blob_store(self.settings)

    def _snapshot(self, index=None):
        if index is None:
            result = self.owner._load_raw()
        else:
            if type(index) is not int or index < 0:
                raise ValueError("Invalid staging chunk index")
            with session_scope() as session:
                fields = [getattr(DocumentStaging, name) for name in (*ROOT_FIELDS, "processed_chunks", "used_slugs", "global_tags")]
                result = session.execute(select(*fields, DocumentStaging.chunks_data[str(index)].label("info"))
                    .where(DocumentStaging.doc_id == self.doc_id)).mappings().one_or_none()
            if result is not None:
                result = dict(result)
                result["chunks_data"] = {str(index): result.pop("info") or {}}
        if result is not None and result["checkpoint_version"] != 2:
            raise GenerationIntegrityError()
        if result is not None:
            processed = result["processed_chunks"] or []
            relevant = processed if index is None else [index] if index in processed else []
            if (any(type(i) is not int or not 0 <= i < result["total_chunks"] for i in processed)
                    or len(set(processed)) != len(processed)
                    or any(not (result["chunks_data"].get(str(i)) or {}).get("file") for i in relevant)):
                raise GenerationIntegrityError()
        return result

    def _scope(self, snapshot, index, info):
        marker = info.get("checkpoint")
        if not isinstance(marker, dict) or set(marker) != {"object_id", "source_signature"}:
            raise GenerationIntegrityError()
        return _identity(self.doc_id, snapshot["generation_id"], index, marker["source_signature"])

    def _assert_current(self, session, snapshot, index, info, *, recovery=False):
        phases = {"preparing", "ready", "active"}
        if recovery:
            from app.services.work.context import current_claim
            work = current_claim()
            if work is None or work.spec.doc_id != self.doc_id:
                raise GenerationIntegrityError()
            phases |= {"retired", "abandoned"}
        generation = session.get(DocumentGeneration, snapshot["generation_id"])
        doc = session.get(Document, self.doc_id)
        current = session.execute(select(DocumentStaging.generation_id, DocumentStaging.checkpoint_version,
            DocumentStaging.chunks_data[str(index)].label("info")).where(DocumentStaging.doc_id == self.doc_id)).mappings().one_or_none()
        if (doc is None or doc.deleted_at is not None or generation is None or generation.doc_id != self.doc_id
                or generation.phase not in phases or current is None
                or current["generation_id"] != snapshot["generation_id"] or current["checkpoint_version"] != 2
                or current["info"] != info):
            raise GenerationIntegrityError()
        binding = session.get(CheckpointBinding, self._scope(snapshot, index, info))
        if binding is None or binding.object_id != info["checkpoint"]["object_id"]:
            raise GenerationIntegrityError()
        obj = session.get(ArtifactObject, binding.object_id)
        if obj is None or obj.state != "ready":
            raise GenerationIntegrityError()
        return _ref(obj)

    def _read(self, snapshot, index):
        info = snapshot["chunks_data"].get(str(index)) or {}
        if not info:
            return {"info": {}, "text": None, "concepts": None}
        try:
            with session_scope() as session:
                lock_generation_read(session, [self.doc_id])
                ref = self._assert_current(session, snapshot, index, info)
            return self._read_ref(snapshot, index, info, ref)
        except DomainError:
            raise
        except Exception as exc:
            if is_storage_full(exc):
                raise StorageFullError() from exc
            raise GenerationIntegrityError() from exc

    def _read_ref(self, snapshot, index, info, ref):
        """Decode a captured immutable ref; recovery commits still CAS the source snapshot."""
        try:
            with materialize(ref, store=LeasedBlobStore(self._store()), scratch_root=self._scratch()) as path:
                body = json.loads(path.read_text(encoding="utf8"))
            if (not isinstance(body, dict) or body.get("version") != 1
                    or body.get("identity") != self._scope(snapshot, index, info)
                    or _signature(snapshot, index, body) != info["checkpoint"]["source_signature"]
                    or _metadata(body["info"]) != {k: v for k, v in info.items() if k != "checkpoint"}
                    or body["text"] is not None and not isinstance(body["text"], str)
                    or body["concepts"] is not None and not isinstance(body["concepts"], list)):
                raise GenerationIntegrityError()
            if body["concepts"] is not None:
                concepts = [Concept(**item) for item in body["concepts"]]
                if len(concepts) != body["info"].get("concepts_count") or len(concepts) != len(body["info"].get("slugs", [])):
                    raise GenerationIntegrityError()
            return body
        except DomainError:
            raise
        except Exception as exc:
            if is_storage_full(exc):
                raise StorageFullError() from exc
            raise GenerationIntegrityError() from exc

    def _scratch(self):
        return self.settings.artifact_scratch_root or self.settings.data_dir / "artifact-scratch"

    def _cache(self, snapshot, index, body):
        info = snapshot["chunks_data"].get(str(index)) or {}
        if not info:
            return
        with session_scope() as session:
            if not lock_document_write(session, self.doc_id, allow_deleted=False):
                raise GenerationIntegrityError()
            self._assert_current(session, snapshot, index, info)
            if body["text"] is not None:
                _atomic_write(self.owner.dir / f"chunk_{index:02d}.md", lambda out: out.write(body["text"].encode("utf8")))
            if body["concepts"] is not None:
                _write_canonical(self.owner.dir / f"chunk_{index:02d}.json", body["concepts"])
            if "table_refs_file" in info:
                payload = json.dumps(body["info"]["table_refs"], ensure_ascii=False, indent=None, sort_keys=True).encode("utf8")
                _atomic_write(self.owner.dir / info["table_refs_file"], lambda out: out.write(payload))

    def create(self, total_chunks, global_tags=None, parser_version=None, source_file_hash=None,
               generation_id=None, processing_scope_hash=None):
        _identity(self.doc_id, generation_id, 0, source_file_hash)
        with session_scope() as session:
            lock_writable_generation(session, self.doc_id, generation_id)
            if session.get(DocumentStaging, self.doc_id) is not None:
                raise GenerationIntegrityError()
            session.add(DocumentStaging(doc_id=self.doc_id, total_chunks=total_chunks, processed_chunks=[], used_slugs=[],
                global_tags=global_tags or [], chunks_data={}, parser_version=parser_version, source_file_hash=source_file_hash,
                generation_id=generation_id, processing_scope_hash=processing_scope_hash, checkpoint_version=2))
        return self.load()

    def _commit(self, snapshot, bodies, *, processed=None, slugs=None, generation_id=None):
        """Put bytes before SQL; CAS pointers and progress together under the owner lock."""
        target_generation = generation_id or snapshot["generation_id"]
        prepared = {}
        root = self._scratch()
        _reject_links(root)
        root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="checkpoint-", dir=root) as directory:
            store = self._store()
            for index, body in bodies.items():
                signature = _signature(snapshot, index, body)
                body = dict(body, version=1, identity=_identity(self.doc_id, target_generation, index, signature))
                path = Path(directory) / "checkpoint.json"
                _write_canonical(path, body)
                with path.open("rb") as source:
                    ref = store.put(source, object_id=uuid4().hex, media_type="application/json")
                info = _metadata(body["info"])
                info["checkpoint"] = {"object_id": ref.object_id, "source_signature": signature}
                prepared[index] = (ref, info)
        with session_scope() as session:
            lock_writable_generation(session, self.doc_id, target_generation)
            fields = [getattr(DocumentStaging, name) for name in (*ROOT_FIELDS, "processed_chunks", "used_slugs")]
            current = session.execute(select(*fields).where(DocumentStaging.doc_id == self.doc_id)).mappings().one_or_none()
            if (current is None or any(current[name] != snapshot[name] for name in ROOT_FIELDS)
                    or current["processed_chunks"] != snapshot["processed_chunks"] or current["used_slugs"] != snapshot["used_slugs"]):
                raise GenerationIntegrityError()
            for index, (ref, info) in prepared.items():
                previous = snapshot["chunks_data"].get(str(index)) or {}
                actual = session.scalar(select(DocumentStaging.chunks_data[str(index)]).where(DocumentStaging.doc_id == self.doc_id)) or {}
                if previous != actual:
                    raise GenerationIntegrityError()
                old = previous.get("checkpoint")
                same_scope = old and old["source_signature"] == info["checkpoint"]["source_signature"] and target_generation == snapshot["generation_id"]
                attach_checkpoint(session, ref=ref, doc_id=self.doc_id, generation_id=target_generation, chunk_index=index,
                    source_signature=info["checkpoint"]["source_signature"], expected_object_id=old["object_id"] if same_scope else None)
                if old and not same_scope:
                    session.execute(delete(CheckpointBinding).filter_by(**self._scope(snapshot, index, previous)))
                raw = json.dumps(info, ensure_ascii=False)
                if session.get_bind().dialect.name == "postgresql":
                    patched = func.jsonb_set(cast(DocumentStaging.chunks_data, JSONB), cast([str(index)], ARRAY(Text)), cast(literal(raw), JSONB), True)
                else:
                    patched = func.json_set(DocumentStaging.chunks_data, f'$."{index}"', func.json(raw))
                session.execute(update(DocumentStaging).where(DocumentStaging.doc_id == self.doc_id).values(chunks_data=patched))
            session.execute(update(DocumentStaging).where(DocumentStaging.doc_id == self.doc_id).values(
                generation_id=target_generation, processed_chunks=processed if processed is not None else snapshot["processed_chunks"],
                used_slugs=slugs if slugs is not None else snapshot["used_slugs"]))
            session.info["portable_staging_write"] = True

    def load(self):
        snapshot = self._snapshot()
        if snapshot is not None:
            from .legacy import hydrate_snapshot
            hydrate_snapshot(self.settings, self.doc_id)
            if snapshot["generation_id"]:
                hydrate_snapshot(self.settings, self.doc_id, snapshot["generation_id"])
        if snapshot is None:
            return None
        result = deepcopy(snapshot)
        for key in snapshot["chunks_data"]:
            index = int(key)
            body = self._read(snapshot, index)
            self._cache(snapshot, index, body)
            result["chunks_data"][key] = body["info"]
        return result

    def _change(self, index, change):
        with self.owner._lock:
            snapshot = self._snapshot(index)
            if snapshot is None:
                raise GenerationIntegrityError()
            body = self._read(snapshot, index)
            change(body)
            self._commit(snapshot, {index: body})
        fresh = self._snapshot(index)
        self._cache(fresh, index, self._read(fresh, index))

    def save_chunk_text(self, index, text):
        def change(body):
            if body["concepts"] is not None and body["text"] != text:
                raise GenerationIntegrityError()
            body["text"] = text
        self._change(index, change)

    def set_chunk_source(self, index, source_id):
        self._change(index, lambda body: body["info"].update(source_id=source_id))

    def set_chunk_table_refs(self, index, refs, *, title=""):
        self._change(index, lambda body: body["info"].update(table_refs=refs, table_title=title))

    def set_chunks_metadata(self, entries):
        source = iter(entries)
        while batch := list(islice(source, 256)):
            with self.owner._lock:
                snapshot = self._snapshot()
                bodies = {}
                for entry in batch:
                    index = entry["index"]
                    body = self._read(snapshot, index)
                    body["info"]["source_id"] = entry["source_id"]
                    if entry.get("table_refs"):
                        body["info"].update(table_refs=entry["table_refs"], table_title=entry.get("table_title", ""))
                    bodies[index] = body
                self._commit(snapshot, bodies)

    def append_chunk(self, index, concepts, degradation=None, provenance=None, global_tags=None):
        with self.owner._lock:
            snapshot = self._snapshot(index)
            body = self._read(snapshot, index)
            previous = body["info"]
            used = [slug for slug in snapshot["used_slugs"] if slug not in set(previous.get("slugs", []))]
            slugs = self.owner._allocate_slugs(concepts, used)
            info = {key: previous[key] for key in ("source_id", "table_refs", "table_title") if key in previous}
            info.update(file=f"chunk_{index:02d}.json", concepts_count=len(concepts), slugs=slugs,
                        global_tags=list(global_tags if global_tags is not None else snapshot["global_tags"]))
            if degradation:
                info["degradation"] = degradation
            if provenance:
                info["provenance"] = provenance
            body.update(info=info, concepts=[concept.model_dump() for concept in concepts])
            self._commit(snapshot, {index: body}, processed=sorted(set(snapshot["processed_chunks"]) | {index}), slugs=[*used, *slugs])
        # Publication of local caches follows the durable transaction only.
        fresh = self._snapshot(index)
        self._cache(fresh, index, self._read(fresh, index))
        return slugs

    def concepts(self):
        snapshot = self._snapshot()
        if snapshot is None:
            return []
        result = []
        for key in sorted(snapshot["chunks_data"], key=int):
            index = int(key)
            body = self._read(snapshot, index)
            if index in snapshot["processed_chunks"] and body["concepts"] is None:
                raise GenerationIntegrityError()
            self._cache(snapshot, index, body)
            result.extend(Concept(**item) for item in body["concepts"] or [])
        return result

    def bind_generation(self, generation_id):
        with self.owner._lock:
            snapshot = self._snapshot()
            if snapshot is None:
                raise GenerationIntegrityError()
            if generation_id == snapshot["generation_id"]:
                return
            bodies = {int(key): self._read(snapshot, int(key)) for key in snapshot["chunks_data"]}
            self._commit(snapshot, bodies, generation_id=generation_id)

    def remove(self):
        with self.owner._lock, session_scope() as session:
            lock_document_write(session, self.doc_id)
            generation = session.scalar(select(DocumentStaging.generation_id).where(DocumentStaging.doc_id == self.doc_id))
            if generation is not None:
                session.execute(delete(CheckpointBinding).where(CheckpointBinding.doc_id == self.doc_id,
                                                                 CheckpointBinding.generation_id == generation))
            session.execute(delete(DocumentStaging).where(DocumentStaging.doc_id == self.doc_id))
            # Cache deletion is serialized with hydration; immutable bytes wait for GC.
            _check_tree(self.owner.dir)
            if self.owner.dir.exists():
                shutil.rmtree(self.owner.dir)
