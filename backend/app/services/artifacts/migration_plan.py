"""Compile legacy/portable corpus files into a deterministic immutable graph."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import delete, select

from app.db import models
from app.db.base import Base
from app.services.generation_files import _check_tree, _reject_links, generation_paths, processing_review_paths
from .contract import BlobRef
from .publication import _logical, _wire_form, _write_canonical, canonical_hash, validate_v2
from .repository import _identity, _ref


class Compiler:
    def __init__(self, task, migration_id):
        self.task, self.settings, self.id = task, task.settings, migration_id
        self.objects, self.bindings, self.updates, self.inserts, self.checkpoints, self.heads = {}, {}, {}, [], [], []
        self.remap, self.visiting, self.root_types = {}, set(), {}
        self.rows = {}
        self.source_files, self.source_blobs = {}, {}
        self.cache_owners, self.review_owners = {}, {}

    def update(self, model, pk, **values):
        table = model.__table__
        timestamps = [column for column in table.columns if column.onupdate is not None and column.name not in values]
        if timestamps:
            old = self.session.execute(select(table).where(*(table.c[name] == value for name, value in pk.items()))).mappings().one_or_none()
            if old:
                values = {**{column.name: old[column.name] for column in timestamps}, **values}
        key = model.__tablename__, tuple(sorted(pk.items()))
        item = self.updates.setdefault(key, {"table": key[0], "pk": pk, "values": {}})
        item["values"].update(values)

    def bind(self, owner_type, owner_id, generation_id, name, ref):
        identity = _identity(owner_type, owner_id, generation_id or None, name)
        key = tuple(identity.values())
        value = {**identity, "object_id": ref.object_id,
                 "doc_id": owner_id if owner_type in {"original", "source", "generation", "checkpoint"} else None}
        if key in self.bindings and self.bindings[key] != value:
            raise ValueError("Conflicting migration bindings")
        self.bindings[key] = value

    def _item(self, sha, size, media, source):
        object_id = uuid5(UUID(self.id), sha + "/" + str(size) + "/" + media).hex
        ref = BlobRef(object_id, "objects/" + object_id, sha, size, media, self.task.target["store"]["store_id"])
        self.objects.setdefault(object_id, {"ref": asdict(ref), "source": source})
        return ref

    def file(self, kind, path, *, expected=None, media="application/octet-stream"):
        source, sha, size = self.track(kind, path, expected=expected)
        return self._item(sha, size, media, source)

    def track(self, kind, path, *, expected=None):
        roots = {"uploads": self.settings.uploads_dir, "okf": self.settings.okf_dir, "staging": self.settings.staging_dir,
                 "diagnostics": self.settings.diagnostics_dir, "exports": self.settings.exports_dir}
        roots.update(diagnostic_legacy_backend=self.task.legacy_diagnostics_root,
            diagnostic_legacy_frontend=self.task.legacy_frontend_root)
        path, root = Path(path).absolute(), roots[kind].absolute()
        _reject_links(path)
        relative = path.relative_to(root).as_posix()
        _logical("bundle/" + relative)
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as source:
            while block := source.read(64 * 1024):
                size += len(block)
                digest.update(block)
        sha = digest.hexdigest()
        if expected and sha != expected:
            raise ValueError("Legacy artifact checksum mismatch")
        source = {"kind": kind, "path": relative}
        self.source_files[(kind, relative)] = {"source": source, "sha256": sha, "size": size}
        return source, sha, size

    def generated(self, body):
        body = _wire_form(body)
        sha = canonical_hash(body)
        folder = self.task.path.parent / "payloads"
        _reject_links(folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (sha + ".json")
        if path.exists():
            from .migration import _digest
            if _digest(path) != sha:
                raise ValueError("Planned payload changed")
        else:
            _write_canonical(path, body)
        return self._item(sha, path.stat().st_size, "application/json", {"kind": "generated", "path": path.relative_to(self.task.path.parent).as_posix()})

    def blob(self, old_id):
        if old_id in self.remap:
            return self.remap[old_id]
        if old_id in self.visiting:
            raise ValueError("Cyclic artifact graph")
        self.visiting.add(old_id)
        row = self.rows.get(old_id)
        if row is None or row.state != "ready" or row.store_id != self.task.source["store"]["store_id"]:
            raise ValueError("Missing/foreign source artifact")
        old = _ref(row)
        self.source_blobs[old.object_id] = asdict(old)
        kind = self.root_types.get(old_id)
        if kind:
            with self.task.store(self.task.source["store"]).open(old) as source:
                body = json.load(source)
            category, doc_id, generation_id, prefix, manifest_name = kind
            field = "artifacts" if category == "publication" else "files"
            if category == "publication":
                validate_v2(body, doc_id, generation_id)
            elif body.get("version") != 2:
                raise ValueError("Unknown portable manifest protocol")
            if category == "cache":
                from .source_cache import _names
                owner = self.cache_owners[old_id]
                if (body.get("cache_id") != owner.cache_id or body["manifest"].get("doc_id") != doc_id
                        or body["manifest"].get("source_key") != owner.source_key
                        or body["manifest"].get("content_fingerprint") != owner.content_fingerprint
                        or set(body["files"]) != _names(body["manifest"])):
                    raise ValueError("Invalid source cache identity")
            elif body.get("doc_id") != doc_id:
                raise ValueError("Manifest owner changed")
            if category == "snapshot" and body.get("generation_id") != generation_id:
                raise ValueError("Snapshot generation changed")
            if category == "review":
                from .review_files import _inventory
                owner = self.review_owners[old_id]
                inventory = _inventory(owner)
                if (body.get("review_id") != owner.id or body.get("source_file_hash") != owner.source_file_hash
                        or set(body["files"]) != set(inventory)
                        or any(value and body["files"][name]["sha256"] != value for name, value in inventory.items())):
                    raise ValueError("Review original identity changed")
            if canonical_hash(body) != old.sha256:
                raise ValueError("Noncanonical portable manifest")
            scope = "generation" if category == "publication" else "source"
            registered = {b.logical_name: b.object_id for b in self.old_bindings
                          if b.owner_type == scope and b.owner_id == doc_id and b.generation_id == (generation_id or "")
                          and b.logical_name.startswith(prefix)}
            if set(registered) != {prefix + name for name in body[field]} | {manifest_name} or registered[manifest_name] != old_id:
                raise ValueError("Manifest binding inventory changed")
            for name, value in body[field].items():
                member = BlobRef(**value)
                if registered.get(prefix + name) != member.object_id or self.rows.get(member.object_id) is None or _ref(self.rows[member.object_id]) != member:
                    raise ValueError("Manifest member binding changed")
            body[field] = {name: asdict(self.blob(value["object_id"])) for name, value in body[field].items()}
            ref = self.generated(body)
        else:
            ref = self._item(old.sha256, old.size, old.media_type, {"kind": "blob", "ref": asdict(old)})
        self.remap[old_id] = ref
        self.visiting.remove(old_id)
        return ref

    def snapshot(self, doc_id, generation_id, files, *, legacy=False):
        if not files:
            return
        if len({name.casefold() for name in files}) != len(files):
            raise ValueError("Portable snapshot path aliases")
        body = {"version": 2, "doc_id": doc_id, "generation_id": generation_id,
                "files": {name: asdict(ref) for name, ref in files.items()}}
        ref = self.generated(body)
        prefix = "legacy/" if legacy else "migration/"
        for name, member in files.items():
            self.bind("source", doc_id, generation_id, prefix + name, member)
        self.bind("source", doc_id, generation_id, prefix + "_manifest", ref)
        if legacy:
            self.update(models.Document, {"id": doc_id}, legacy_artifacts_id=ref.object_id, legacy_artifacts_version=2)
        else:
            self.update(models.DocumentGeneration, {"id": generation_id}, migration_artifacts_id=ref.object_id, migration_artifacts_version=2)

    def _tree(self, doc_id, generation_id=None):
        roots = (("attachments", self.settings.uploads_dir / doc_id / "attachments", "uploads"),
                 ("bundle", self.settings.okf_dir / doc_id, "okf")) if generation_id is None else (
                    ("attachments", generation_paths(self.settings, doc_id, generation_id).attachments, "uploads"),
                    ("bundle", generation_paths(self.settings, doc_id, generation_id).bundle, "okf"))
        result = {}
        for group, root, kind in roots:
            _check_tree(root)
            if root.exists():
                for path in sorted(root.rglob("*")):
                    if generation_id is None and path.relative_to(root).parts[0] == "generations":
                        continue
                    if path.is_file():
                        name = _logical(group + "/" + path.relative_to(root).as_posix())
                        if path.name == "manifest.json" and "source-cache" in path.relative_to(root).parts:
                            self.track(kind, path)
                            body = json.loads(path.read_text(encoding="utf8"))
                            result[name] = self.generated({**body, "schema_version": 2}) if body.get("schema_version") == 1 else self.file(kind, path)
                        else:
                            result[name] = self.file(kind, path)
        return result

    def _generations(self, session, generations):
        from app.services.generation_artifacts import load_verified_publication
        for row in generations:
            if row.publication_version == 2:
                ref = next((self.blob(b.object_id) for b in self.old_bindings if b.owner_type == "generation"
                    and b.owner_id == row.doc_id and b.generation_id == row.id and b.logical_name == "publication.json"), None)
                if ref is None:
                    raise ValueError("Portable publication binding lost")
                self.update(models.DocumentGeneration, {"id": row.id}, publication_hash=ref.sha256)
                continue
            if row.publication_version != 1:
                raise ValueError("Unknown publication protocol")
            if row.migration_artifacts_version == 2:
                if row.publication_hash:
                    raise ValueError("Conflicting published/candidate snapshot protocols")
                continue  # Its verified immutable graph was already transferred; cache is not authority.
            files = self._tree(row.doc_id, row.id)
            if row.publication_hash:
                self.track("uploads", generation_paths(self.settings, row.doc_id, row.id).uploads_root / "publication.json", expected=row.publication_hash)
                body = load_verified_publication(self.settings, row.doc_id, row.id, row.publication_hash, session=session)
                if set(files) != set(body["artifacts"]):
                    raise ValueError("Published artifact inventory changed")
                for concept in body["concepts"]:
                    old = concept["filepath"]
                    marker = f"/{row.doc_id}/generations/{row.id}/"
                    normalized = old.replace("\\", "/")
                    if ".." in Path(normalized).parts:
                        raise ValueError("Unsafe published concept path")
                    if normalized.startswith("bundle/"):
                        name = normalized
                    elif marker in normalized:
                        name = "bundle/" + normalized.split(marker, 1)[1]
                    elif len(Path(normalized).parts) == 1:
                        name = "bundle/" + normalized
                    else:
                        raise ValueError("Published concept path has no registered generation ancestry")
                    concept["filepath"] = _logical(name)
                body.update(version=2, bundle_files_enabled=all(c["filepath"] in files for c in body["concepts"]),
                            artifacts={name: asdict(ref) for name, ref in files.items()})
                validate_v2(body, row.doc_id, row.id)
                ref = self.generated(body)
                for name, member in files.items():
                    self.bind("generation", row.doc_id, row.id, name, member)
                self.bind("generation", row.doc_id, row.id, "publication.json", ref)
                self.update(models.DocumentGeneration, {"id": row.id}, publication_version=2, publication_hash=ref.sha256)
            else:
                self.snapshot(row.doc_id, row.id, files)

    def _caches(self, session, documents, generations):
        from app.services.artifacts.source_cache import _names
        from app.services.processing_scope_cache import SourceResultCache
        for doc in documents:
            roots = [("", self.settings.uploads_dir / doc.id / "scope-stage-cache")]
            roots += [(row.id, generation_paths(self.settings, doc.id, row.id).bundle / "source-cache") for row in generations if row.doc_id == doc.id]
            for generation, root in roots:
                _check_tree(root)
                if not root.exists():
                    continue
                for folder in sorted(root.iterdir()):
                    if not folder.is_dir():
                        raise ValueError("Invalid source cache folder")
                    if session.get(models.SourceCacheHead, (doc.id, generation, folder.name)):
                        continue  # Existing portable caches were copied from their authoritative graph.
                    raw = json.loads((folder / "manifest.json").read_text(encoding="utf8"))
                    self.track("uploads" if not generation else "okf", folder / "manifest.json")
                    source = {key: raw[key] for key in ("source_key", "content_fingerprint")}
                    cache = SourceResultCache(root, doc.id, _local_only=True)
                    if cache.identity(source) != folder.name:
                        raise ValueError("Legacy cache identity changed")
                    manifest = cache._manifest(source)
                    refs = {}
                    for stage, info in manifest["stages"].items():
                        cache._verify_stage(source, stage, info, allow_partial=True)
                        for record_kind in info.get("counts", {}):
                            for _ in cache.iter_stage_records(source, stage, record_kind, allow_partial=True):
                                pass
                    for name in _names(manifest):
                        path = folder / name
                        expected = name.split("-", 1)[-1][:-6] if name.endswith(".jsonl") else manifest["original"]["sha256"]
                        refs[name] = self.file("uploads" if not generation else "okf", path, expected=expected)
                    ref = self.generated({"version": 2, "cache_id": folder.name, "manifest": manifest,
                                          "files": {name: asdict(value) for name, value in refs.items()}})
                    prefix = f"source-cache/{folder.name}/{ref.object_id}/"
                    for name, member in {**refs, "manifest": ref}.items():
                        self.bind("source", doc.id, generation, prefix + name, member)
                    self.heads.append(dict(doc_id=doc.id, generation_id=generation, cache_id=folder.name,
                        source_key=source["source_key"], content_fingerprint=source["content_fingerprint"], object_id=ref.object_id))
                    self.update(models.Document, {"id": doc.id}, source_cache_version=2)

    def _reviews(self, session):
        from .review_files import _inventory
        for row in session.scalars(select(models.DocumentProcessingReview)):
            if row.artifact_version == 2:
                continue
            if row.artifact_version != 1:
                raise ValueError("Unknown review protocol")
            root = processing_review_paths(self.settings, row.doc_id, row.id) / "attachments"
            refs = {name: self.file("uploads", root / name, expected=fingerprint) for name, fingerprint in _inventory(row).items()}
            ref = self.generated({"version": 2, "doc_id": row.doc_id, "review_id": row.id, "source_file_hash": row.source_file_hash,
                                  "files": {name: asdict(member) for name, member in refs.items()}})
            revision = uuid5(UUID(self.id), "review/" + row.id).hex
            for name, member in {**refs, "_manifest": ref}.items():
                self.bind("source", row.doc_id, None, f"review/{row.id}/{revision}/{name}", member)
            self.update(models.DocumentProcessingReview, {"doc_id": row.doc_id}, artifact_version=2, artifact_revision=revision)

    def _staging(self, session):
        from app.services.staging import StagingStore
        from .staging import _metadata, _signature
        for row in session.scalars(select(models.DocumentStaging)):
            data = deepcopy(row.chunks_data or {})
            if (len(set(row.processed_chunks or [])) != len(row.processed_chunks or [])
                    or any(type(index) is not int or not 0 <= index < row.total_chunks
                           or not (data.get(str(index)) or {}).get("file") for index in row.processed_chunks or [])):
                raise ValueError("Accepted checkpoint inventory is incomplete")
            if row.checkpoint_version == 2:
                from .staging import _signature, _metadata
                snapshot = {name: getattr(row, name) for name in ("parser_version", "source_file_hash", "processing_scope_hash")}
                for key, info in data.items():
                    if info:
                        identity = dict(doc_id=row.doc_id, generation_id=row.generation_id, chunk_index=int(key),
                                        source_signature=info["checkpoint"]["source_signature"])
                        pointer = session.get(models.CheckpointBinding, identity)
                        if pointer is None or pointer.object_id != info["checkpoint"]["object_id"]:
                            raise ValueError("Checkpoint binding mismatch")
                        old_ref = _ref(self.rows[pointer.object_id])
                        with self.task.store(self.task.source["store"]).open(old_ref) as source:
                            body = json.load(source)
                        if (body.get("version") != 1 or body.get("identity") != identity
                                or _signature(snapshot, int(key), body) != identity["source_signature"]
                                or _metadata(body["info"]) != {k: v for k, v in info.items() if k != "checkpoint"}):
                            raise ValueError("Portable checkpoint identity changed")
                        info["checkpoint"]["object_id"] = self.blob(info["checkpoint"]["object_id"]).object_id
                self.update(models.DocumentStaging, {"doc_id": row.doc_id}, chunks_data=data)
                continue
            if row.checkpoint_version != 1:
                raise ValueError("Unknown checkpoint protocol")
            generation = row.generation_id
            if generation:
                owner = session.get(models.DocumentGeneration, generation)
                if owner is None or owner.doc_id != row.doc_id or owner.phase not in {"preparing", "ready", "active"}:
                    raise ValueError("Resolve retired/abandoned checkpoint owners before migration")
            if generation is None:
                generation = uuid5(UUID(self.id), "checkpoint/" + row.doc_id).hex
                state = session.get(models.DocumentGenerationState, row.doc_id)
                if state and state.candidate_generation_id:
                    generation = state.candidate_generation_id
                else:
                    self.inserts.append({"table": "document_generations", "values": {"id": generation, "doc_id": row.doc_id, "phase": "preparing"}})
                    if state:
                        self.update(models.DocumentGenerationState, {"doc_id": row.doc_id}, candidate_generation_id=generation)
                    else:
                        self.inserts.append({"table": "document_generation_states", "values": {"doc_id": row.doc_id, "candidate_generation_id": generation}})
            owner = StagingStore(row.doc_id, staging_root=self.settings.staging_dir / row.doc_id)
            snapshot = {name: getattr(row, name) for name in ("parser_version", "source_file_hash", "processing_scope_hash")}
            packed = {}
            for key, info in sorted(data.items(), key=lambda item: int(item[0])):
                index = int(key)
                if str(index) != key or not 0 <= index < row.total_chunks:
                    raise ValueError("Invalid legacy checkpoint index")
                info = owner._unpack_info(info)
                if data[key].get("table_refs_file"):
                    self.track("staging", owner.dir / data[key]["table_refs_file"])
                text = owner.dir / f"chunk_{index:02d}.md"
                raw = text.read_text(encoding="utf8") if text.exists() else None
                if text.exists():
                    self.track("staging", text)
                concepts = None
                if info.get("file"):
                    if info["file"] != f"chunk_{index:02d}.json":
                        raise ValueError("Unsafe checkpoint file")
                    concepts = json.loads((owner.dir / info["file"]).read_text(encoding="utf8"))
                    self.track("staging", owner.dir / info["file"])
                    from app.models.schemas import Concept
                    values = [Concept(**value) for value in concepts]
                    if len(values) != info.get("concepts_count") or len(values) != len(info.get("slugs", [])):
                        raise ValueError("Legacy checkpoint metadata mismatch")
                if index in row.processed_chunks and concepts is None:
                    raise ValueError("Accepted checkpoint file missing")
                body = {"version": 1, "info": info, "text": raw, "concepts": concepts}
                signature = _signature(snapshot, index, body)
                identity = dict(doc_id=row.doc_id, generation_id=generation, chunk_index=index, source_signature=signature)
                body["identity"] = identity
                ref = self.generated(body)
                self.checkpoints.append({**identity, "object_id": ref.object_id})
                packed[key] = {**_metadata(info), "checkpoint": {"object_id": ref.object_id, "source_signature": signature}}
            self.update(models.DocumentStaging, {"doc_id": row.doc_id}, chunks_data=packed, checkpoint_version=2, generation_id=generation)

    def _jobs(self, session):
        for job in session.scalars(select(models.Job)):
            if job.job_type != "bulk_export":
                continue
            params = deepcopy(job.params or {})
            changed = False
            for document in params.get("documents", []):
                if job.status not in {"queued", "running"}:
                    continue  # Historical snapshots are not live artifact references.
                doc_id = document["doc_id"]
                if document.get("source_blob"):
                    ref = self.blob(document["source_blob"]["object_id"])
                else:
                    original = self.bindings.get(("original", doc_id, "", "original"))
                    if original is None:
                        raise ValueError("Queued export original is unavailable")
                    ref = BlobRef(**self.objects[original["object_id"]]["ref"])
                    basename = document["source_basename"]
                    if Path(basename).name != basename or not basename.startswith(doc_id + "."):
                        raise ValueError("Unsafe queued export snapshot")
                    stat = (self.settings.uploads_dir / basename).stat()
                    if (stat.st_size, stat.st_mtime_ns) != (document["size_bytes"], document["mtime_ns"]):
                        raise ValueError("Queued export snapshot changed; resolve it before migration")
                document["source_blob"] = asdict(ref)
                self.bind("export", str(job.id), None, "source-" + doc_id, ref)
                changed = True
            if changed:
                self.update(models.Job, {"id": job.id}, params=params)
            result = deepcopy(job.result or {})
            if job.status == "completed" and result.get("artifact_status") == "available" and result.get("artifact_version", 1) == 1:
                for part in result["parts"]:
                    name = part["filename"]
                    _logical("bundle/" + name)
                    if Path(name).name != name:
                        raise ValueError("Unsafe export filename")
                    ref = self.file("exports", self.settings.exports_dir / f"{job.id}.ready" / name)
                    if ref.size != part["size_bytes"]:
                        raise ValueError("Export size changed")
                    part["sha256"] = ref.sha256
                    self.bind("export", str(job.id), None, name, ref)
                result["artifact_version"] = 2
                self.update(models.Job, {"id": job.id}, result=result)
        for row in session.scalars(select(models.DiagnosticBundle)):
            if isinstance(row.manifest,dict) and 'frozen_bundle' in row.manifest:
                self._frozen_diagnostic(session,row)
                continue
            if row.status == "ready" and row.artifact_version == 1:
                ref = self.file("diagnostics", self.settings.diagnostics_dir / "bundles" / (row.id + ".zip"), expected=row.sha256)
                if ref.size != row.size_bytes:
                    raise ValueError("Diagnostic archive size changed")
                self.bind("diagnostic_bundle", row.id, None, "bundle.zip", ref)
                self.update(models.DiagnosticBundle, {"id": row.id}, artifact_version=2)

    def _frozen_diagnostic(self,session,row):
        from app.services.diagnostics.frozen_bundle import frozen_receipt, frozen_spec
        from app.services.work.store import _spec
        if row.status!='queued':raise ValueError('Reconcile attempted frozen diagnostic work before migration')
        receipt=frozen_receipt(session,row,self.settings)
        expected=frozen_spec(session,row,self.settings)
        receipt['ref']=asdict(self.blob(receipt['ref']['object_id']))
        self.update(models.DiagnosticBundle,{'id':row.id},manifest={'frozen_bundle':receipt})
        from sqlalchemy import and_, or_
        matches=list(session.scalars(select(models.WorkItem).where(or_(
            models.WorkItem.dedupe_key==expected.dedupe_key,
            and_(models.WorkItem.kind=='diagnostic_bundle',models.WorkItem.payload_json['bundle_id'].as_string()==row.id))).limit(2)))
        if len(matches)>1:raise ValueError('Ambiguous frozen diagnostic work')
        for work in matches:
            if (_spec(work)!=expected or work.status!='queued' or work.owner_id is not None or work.lease_until is not None
                    or work.attempts!=0 or work.fence!=0 or work.cancel_requested or work.finished_at is not None
                    or session.get(models.WorkProgress,work.id) is not None):
                raise ValueError('Reconcile attempted frozen diagnostic work before migration')
            self.update(models.WorkItem,{'id':work.id},payload_json={**expected.payload,'input_hash':canonical_hash(receipt)})

    def build(self, session):
        self.session = session
        documents = list(session.scalars(select(models.Document).order_by(models.Document.id)))
        generations = list(session.scalars(select(models.DocumentGeneration).order_by(models.DocumentGeneration.id)))
        from app.services.generation_files import _validate_ids
        for doc in documents:
            _validate_ids(doc.id, "0" * 32)
            if doc.legacy_artifacts_version not in {1, 2} or (doc.legacy_artifacts_version == 2) != (doc.legacy_artifacts_id is not None):
                raise ValueError("Legacy snapshot protocol/pointer mismatch")
        for generation in generations:
            if generation.migration_artifacts_version not in {1, 2} or (generation.migration_artifacts_version == 2) != (generation.migration_artifacts_id is not None):
                raise ValueError("Generation snapshot protocol/pointer mismatch")
        self.old_bindings = list(session.scalars(select(models.ArtifactBinding).where(models.ArtifactBinding.owner_type != "migration")))
        self.rows = {row.object_id: row for row in session.scalars(select(models.ArtifactObject))}
        for row in generations:
            if row.publication_version == 2:
                binding = next((b for b in self.old_bindings if b.owner_type == "generation" and b.owner_id == row.doc_id
                                and b.generation_id == row.id and b.logical_name == "publication.json"), None)
                if binding:
                    self.root_types[binding.object_id] = ("publication", row.doc_id, row.id, "", "publication.json")
        old_heads = list(session.scalars(select(models.SourceCacheHead)))
        for row in old_heads:
            self.cache_owners[row.object_id] = row
            prefix = f"source-cache/{row.cache_id}/{row.object_id}/"
            self.root_types[row.object_id] = ("cache", row.doc_id, row.generation_id or None, prefix, prefix + "manifest")
        for row in session.scalars(select(models.DocumentProcessingReview).where(models.DocumentProcessingReview.artifact_version == 2)):
            prefix = f"review/{row.id}/{row.artifact_revision}/"
            ref = next((b.object_id for b in self.old_bindings if b.owner_type == "source" and b.owner_id == row.doc_id
                        and b.logical_name == prefix + "_manifest"), None)
            if ref is None:
                raise ValueError("Portable review manifest missing")
            self.review_owners[ref] = row
            self.root_types[ref] = ("review", row.doc_id, None, prefix, prefix + "_manifest")
        for row, field, generation, prefix in [(d, "legacy_artifacts_id", None, "legacy/") for d in documents] + [
                (g, "migration_artifacts_id", g.id, "migration/") for g in generations]:
            if getattr(row, field):
                doc_id = row.id if isinstance(row, models.Document) else row.doc_id
                self.root_types[getattr(row, field)] = ("snapshot", doc_id, generation, prefix, prefix + "_manifest")
                ref = self.blob(getattr(row, field))
                self.update(type(row), {"id": row.id}, **{field: ref.object_id})
        for binding in self.old_bindings:
            ref = self.blob(binding.object_id)
            name = binding.logical_name
            if binding.owner_type == "source" and name.startswith("source-cache/"):
                parts = name.split("/")
                if len(parts) != 4:
                    raise ValueError("Invalid source cache binding name")
                parts[2] = self.blob(parts[2]).object_id
                name = "/".join(parts)
            self.bind(binding.owner_type, binding.owner_id, binding.generation_id, name, ref)
        for head in old_heads:
            self.heads.append(dict(doc_id=head.doc_id, generation_id=head.generation_id, cache_id=head.cache_id,
                source_key=head.source_key, content_fingerprint=head.content_fingerprint, object_id=self.blob(head.object_id).object_id))
        for checkpoint in session.scalars(select(models.CheckpointBinding)):
            self.checkpoints.append({name: getattr(checkpoint, name) for name in ("doc_id", "generation_id", "chunk_index", "source_signature")}
                                    | {"object_id": self.blob(checkpoint.object_id).object_id})
        for doc in documents:
            bound = any(b.owner_type == "original" and b.owner_id == doc.id for b in self.old_bindings)
            if not bound:
                ref = self.file("uploads", self.settings.uploads_dir / (doc.id + Path(doc.filename).suffix.lower()),
                                expected=doc.file_hash, media=doc.content_type or "application/octet-stream")
                self.bind("original", doc.id, None, "original", ref)
            state = session.get(models.DocumentGenerationState, doc.id)
            if not state or not state.active_generation_id:
                if doc.legacy_artifacts_id is None:
                    self.snapshot(doc.id, None, self._tree(doc.id), legacy=True)
        self._generations(session, generations)
        self._caches(session, documents, generations)
        self._reviews(session)
        self._staging(session)
        self._jobs(session)
        from .diagnostic_migration import compile_diagnostics
        diagnostics=compile_diagnostics(self,session)
        objects = list(self.objects.values())
        return {"id": self.id, "objects": objects, "bindings": list(self.bindings.values()),
                "source_files": list(self.source_files.values()), "source_blobs": list(self.source_blobs.values()),
                "updates": list(self.updates.values()), "inserts": self.inserts, "checkpoints": self.checkpoints, "heads": self.heads,
                **({'diagnostics':diagnostics} if diagnostics is not None else {}),
                "counts": {"documents": len(documents), "generations": len(generations), "objects": len(objects),
                           "bytes": sum(item["ref"]["size"] for item in objects), "bindings": len(self.bindings)}}


def apply_plan(session, plan, *, diagnostic_store=None, diagnostic_limits=None):
    refs = {item["ref"]["object_id"]: BlobRef(**item["ref"]) for item in plan["objects"]}
    for object_id, ref in refs.items():
        row = session.get(models.ArtifactObject, object_id)
        if row is None or row.state != "ready" or _ref(row) != ref:
            raise ValueError("Target object metadata changed before cutover")
    for item in plan["inserts"]:
        from datetime import datetime
        from sqlalchemy import DateTime
        table=Base.metadata.tables[item['table']]
        values={name:datetime.fromisoformat(value) if isinstance(table.c[name].type,DateTime) and isinstance(value,str)
            else value for name,value in item['values'].items()}
        session.execute(table.insert().values(**values))
    if plan.get('diagnostics'):
        from .diagnostic_migration import apply_diagnostics
        if diagnostic_store is None or diagnostic_limits is None:raise ValueError('Verified diagnostic import context required')
        apply_diagnostics(session,plan['diagnostics'],diagnostic_store,diagnostic_limits)
    session.execute(delete(models.ArtifactBinding).where(models.ArtifactBinding.owner_type != "migration"))
    session.execute(delete(models.CheckpointBinding))
    session.execute(delete(models.SourceCacheHead))
    for values in plan["bindings"]:
        _identity(values["owner_type"], values["owner_id"], values["generation_id"] or None, values["logical_name"])
        if values["object_id"] not in refs:
            raise ValueError("Unverified target binding")
        session.add(models.ArtifactBinding(**values))
    session.add_all(models.CheckpointBinding(**values) for values in plan["checkpoints"])
    session.add_all(models.SourceCacheHead(**values) for values in plan["heads"])
    for item in plan["updates"]:
        table = Base.metadata.tables[item["table"]]
        from datetime import datetime
        from sqlalchemy import DateTime
        values = {name: datetime.fromisoformat(value) if isinstance(table.c[name].type, DateTime) and isinstance(value, str)
                  else value for name, value in item["values"].items()}
        query = table.update().where(*(table.c[key] == value for key, value in item["pk"].items())).values(**values)
        session.execute(query)
    session.flush()
    session.execute(delete(models.ArtifactBinding).where(models.ArtifactBinding.owner_type == "migration",
                                                       models.ArtifactBinding.owner_id == plan["id"]))
