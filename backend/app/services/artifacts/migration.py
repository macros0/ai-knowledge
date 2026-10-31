"""Explicit offline transfer; immutable copy receipts precede atomic selection."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from filelock import FileLock
from sqlalchemy import select, update

from app.db import models
from app.db.session import session_scope
from app.services.generation_files import _reject_links
from .contract import BlobRef
from .control import STORE_FIELDS, descriptor, migration_access
from .publication import _write_canonical, canonical_hash
from .repository import attach_blob, resolve_blob

EXCLUDED = {"artifact_storage_control", "artifact_storage_migrations", "artifact_gc_state", "artifact_orphans", "artifact_read_leases"}
CORPUS = ("documents", "document_generation_states", "document_generations", "document_staging", "document_update_attempts",
    "document_processing_reviews", "document_scope_changes", "document_sources", "document_chunks", "okf_concepts", "okf_attachments",
    "document_tables", "document_table_rows", "document_chunk_projections", "document_chunk_fragments", "document_tags",
    "artifact_source_caches", "artifact_checkpoints", "jobs", "diagnostic_bundles", "upload_receipts", "artifact_bindings", "artifact_objects")


def installation_id():
    with migration_access(), session_scope() as session:
        row = session.get(models.ArtifactStorageControl, 1)
        if row is None:
            raise ValueError("Apply storage migration schema before planning")
        return row.installation_id


def source_revision(session):
    roots = select(models.ArtifactBinding.object_id).where(models.ArtifactBinding.owner_type != "migration").union(
        select(models.CheckpointBinding.object_id), select(models.SourceCacheHead.object_id),
        select(models.Document.legacy_artifacts_id).where(models.Document.legacy_artifacts_id.is_not(None)),
        select(models.DocumentGeneration.migration_artifacts_id).where(models.DocumentGeneration.migration_artifacts_id.is_not(None)))
    digest = hashlib.sha256()
    from app.db.base import Base
    for name in sorted(CORPUS):
        table = Base.metadata.tables[name]
        query = select(table).order_by(*table.primary_key.columns)
        if name == "artifact_bindings":
            query = query.where(table.c.owner_type != "migration")
        elif name == "artifact_objects":
            query = query.where(table.c.object_id.in_(roots))
        digest.update(name.encode())
        for row in session.execute(query.execution_options(yield_per=100)).mappings():
            digest.update(canonical_hash(dict(row)).encode())
    return digest.hexdigest()


def _read(path):
    _reject_links(path)
    with path.open("rb") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError("Invalid migration manifest")
    return value


def _digest(path):
    _reject_links(path)
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


class StorageMigration:
    def __init__(self, settings, source_manifest, target_manifest, plan_path, *, stores=None, credentials=None):
        self.runtime_settings = settings
        self.settings = settings.model_copy(deep=True)
        self.source = deepcopy(source_manifest)
        self.target = deepcopy(target_manifest)
        self.path = Path(plan_path).absolute()
        _reject_links(self.path)
        self.stores, self.credentials = stores or {}, credentials or {}

    def _normalize(self, manifest):
        if set(manifest) != {"version", "installation_id", "store"} or type(manifest["version"]) is not int or manifest["version"] != 1:
            raise ValueError("Invalid storage manifest version")
        store = manifest["store"]
        if not isinstance(store, dict) or set(store) - STORE_FIELDS.keys() or not {"backend", "store_id"} <= set(store):
            raise ValueError("Credentials/unknown fields are forbidden in a storage manifest")
        BlobRef("0" * 32, "objects/" + "0" * 32, "0" * 64, 0, "application/octet-stream", store["store_id"])
        if store["backend"] in {"local", "rwx"} and (not isinstance(store.get("root"), str) or not Path(store["root"]).is_absolute()):
            raise ValueError("Explicit absolute artifact root required")
        if store["backend"] == "s3":
            from .s3 import validate_endpoint
            validate_endpoint(store.get("endpoint"), store.get("bucket"), store.get("prefix", ""),
                              store.get("addressing_style", "path"), store.get("allow_http", False))
        return descriptor(self._settings(store))

    def _settings(self, store):
        values = {STORE_FIELDS[key]: Path(value) if key in {"root", "ca_bundle"} else value for key, value in store.items()}
        secrets = self.credentials.get(store["store_id"], {})
        if set(secrets) - {"artifact_s3_access_key", "artifact_s3_secret_key", "artifact_s3_session_token"}:
            raise ValueError("Unknown private provider credential field")
        return self.settings.model_copy(update={**values, **secrets})

    def store(self, store):
        if store["store_id"] not in self.stores:
            from . import get_blob_store
            self.stores[store["store_id"]] = get_blob_store(self._settings(store))
        return self.stores[store["store_id"]]

    def _identity(self):
        identity = installation_id()
        if self.source.get("installation_id") != identity or self.target.get("installation_id") != identity:
            raise ValueError("Wrong installation")
        source, target = self._normalize(self.source), self._normalize(self.target)
        if source["store_id"] == target["store_id"] or source == target:
            raise ValueError("Target store must have an independent identity")
        if source != descriptor(self.settings):
            raise ValueError("Source configuration does not match manifest")
        roots = [self.settings.data_dir.absolute(), self.settings.diagnostics_dir.absolute()]
        roots += [Path(item["root"]) for item in (source, target) if "root" in item]
        if any(self.path.is_relative_to(root) for root in roots):
            raise ValueError("Transfer plan/work directory must be outside runtime source/target roots")
        if target["backend"] in {"local", "rwx"}:
            root = Path(target["root"])
            with migration_access(), session_scope() as session:
                retired = session.get(models.ArtifactStorageControl, 1).readonly_roots
            for old in (self.settings.data_dir, self.settings.diagnostics_dir,
                        Path(source["root"]) if "root" in source else self.settings.data_dir, *(Path(path) for path in retired)):
                _reject_links(Path(old))
                if root.is_relative_to(Path(old).absolute()) or Path(old).absolute().is_relative_to(root):
                    raise ValueError("Source and target roots overlap")
        if source["backend"] == target["backend"] == "s3" and (source.get("endpoint"), source.get("bucket")) == (target.get("endpoint"), target.get("bucket")):
            a, b = source.get("prefix", ""), target.get("prefix", "")
            if a.startswith(b) or b.startswith(a):
                raise ValueError("Source and target prefixes overlap")
        return identity, source, target

    def plan(self):
        with migration_access():
            identity, source, target = self._identity()
            if self.path.exists():
                plan = _read(self.path)
                if (plan.get("installation_id") != identity or plan.get("source") != source or plan.get("target") != target
                        or canonical_hash({k: v for k, v in plan.items() if k != "plan_hash"}) != plan.get("plan_hash")):
                    raise ValueError("Migration plan changed")
                return plan
            with session_scope() as session:
                control = session.get(models.ArtifactStorageControl, 1)
                if control.maintenance_token or control.active_store_id not in {None, source["store_id"]}:
                    raise ValueError("Another storage migration/selection is active")
                revision = source_revision(session)
                from .migration_plan import Compiler
                compiler = Compiler(self, uuid4().hex)
                plan = compiler.build(session)
                if revision != source_revision(session):
                    raise ValueError("Source changed while planning")
            plan.update(version=1, installation_id=identity, source=source, target=target, source_sql_revision=revision)
            plan["source_revision"] = canonical_hash({"sql": revision, "files": plan["source_files"], "blobs": plan["source_blobs"]})
            plan["plan_hash"] = canonical_hash(plan)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _write_canonical(self.path, plan)
            return plan

    def active_store_id(self):
        with migration_access(), session_scope() as session:
            return session.get(models.ArtifactStorageControl, 1).active_store_id

    def _backup(self, plan, path):
        if path is None:
            raise ValueError("Independent backup evidence required")
        proof = _read(Path(path))
        if (proof.get("version") != 1 or proof.get("installation_id") != plan["installation_id"]
                or proof.get("source_revision") != plan["source_revision"] or not isinstance(proof.get("files"), list)
                or {item.get("role") for item in proof["files"]} != {"database", "artifacts"}):
            raise ValueError("Backup evidence does not match source revision")
        roots = [self.settings.data_dir.absolute(), self.settings.diagnostics_dir.absolute()]
        roots += [Path(d["root"]) for d in (plan["source"], plan["target"]) if "root" in d]
        for item in proof["files"]:
            file = Path(item["path"]).absolute()
            if any(file.is_relative_to(root) for root in roots) or not file.is_file() or file.stat().st_size != item["size"] or _digest(file) != item["sha256"]:
                raise ValueError("Backup evidence is missing, changed or inside a runtime root")
        return canonical_hash(proof)

    def _lock(self, session, plan):
        session.execute(update(models.ArtifactStorageControl).where(models.ArtifactStorageControl.id == 1)
                        .values(id=models.ArtifactStorageControl.id).execution_options(synchronize_session=False))
        control = session.get(models.ArtifactStorageControl, 1, populate_existing=True)
        if control.installation_id != plan["installation_id"] or control.maintenance_token not in {None, plan["id"]}:
            raise ValueError("Migration ownership changed")
        return control

    def _admit(self, plan, proof):
        with session_scope() as session:
            control = self._lock(session, plan)
            receipt = session.get(models.ArtifactStorageMigration, plan["id"])
            if receipt:
                if receipt.plan_hash != plan["plan_hash"] or receipt.backup_hash != proof:
                    raise ValueError("Migration receipt mismatch")
                return receipt.status
            if control.active_store_id not in {None, plan["source"]["store_id"]} or source_revision(session) != plan["source_sql_revision"]:
                raise ValueError("Source revision/selection changed")
            from .lifecycle import _now
            if session.scalar(select(models.ArtifactReadLease.object_id).where(models.ArtifactReadLease.expires_at > _now(session)).limit(1)):
                raise ValueError("Active artifact readers must drain before migration")
            control.active_store_id, control.active_descriptor = plan["source"]["store_id"], plan["source"]
            control.maintenance_token = plan["id"]
            session.add(models.ArtifactStorageMigration(id=plan["id"], installation_id=plan["installation_id"],
                plan_hash=plan["plan_hash"], source_revision=plan["source_revision"], source_store_id=plan["source"]["store_id"],
                target_store_id=plan["target"]["store_id"], status="copying", backup_hash=proof, receipts={}))
            return "copying"

    @contextmanager
    def _operation(self, *, apply, writers_stopped, backup):
        if self.settings.environment == "production" or self.settings.app_role != "combined":
            raise ValueError("Storage migration preview requires non-production combined execution until platform acceptance")
        if not apply or not writers_stopped:
            raise ValueError("Mutation requires --apply and stopped-writer confirmation")
        self._identity()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = Path(str(self.path) + ".lock")
        _reject_links(lock)
        with migration_access(), FileLock(str(lock), timeout=0):
            plan = self.plan()
            proof = self._backup(plan, backup)
            yield plan, self._admit(plan, proof)

    def _assert_source(self, session, plan, *, full=True):
        control = self._lock(session, plan)
        if (control.maintenance_token != plan["id"] or control.active_store_id != plan["source"]["store_id"]
                or full and source_revision(session) != plan["source_sql_revision"]):
            raise ValueError("Source changed since migration plan")

    def _open_source(self, item):
        source = item["source"]
        if source["kind"] == "blob":
            return self.store(self.source["store"]).open(BlobRef(**source["ref"]))
        roots = {"uploads": self.settings.uploads_dir, "okf": self.settings.okf_dir,
                 "staging": self.settings.staging_dir, "diagnostics": self.settings.diagnostics_dir,
                 "exports": self.settings.exports_dir, "generated": self.path.parent}
        root = roots[source["kind"]].absolute()
        path = root / source["path"]
        if not path.absolute().is_relative_to(root) or Path(source["path"]).is_absolute() or ".." in Path(source["path"]).parts:
            raise ValueError("Migration path escapes root")
        _reject_links(path)
        return path.open("rb")

    def copy(self, *, apply=False, writers_stopped=False, backup=None):
        with self._operation(apply=apply, writers_stopped=writers_stopped, backup=backup) as (plan, status):
            if status == "cutover":
                return self._committed(plan)
            with session_scope() as session:
                self._assert_source(session, plan)
            target = self.store(plan["target"])
            from .gc import initialize_gc
            initialize_gc(self._settings(plan["target"]), store=target, explicit=True)
            for item in plan["objects"]:
                ref = BlobRef(**item["ref"])
                with session_scope() as session:
                    self._assert_source(session, plan, full=False)
                    receipt = session.get(models.ArtifactStorageMigration, plan["id"])
                    done = receipt.receipts.get(ref.object_id)
                if done:
                    if done != asdict(ref):
                        raise ValueError("Copied object receipt changed")
                    target.stat(ref)
                    continue
                with self._open_source(item) as source:
                    if item["source"]["kind"] != "blob":
                        digest, size = hashlib.sha256(), 0
                        while block := source.read(64 * 1024):
                            digest.update(block)
                            size += len(block)
                        if (digest.hexdigest(), size) != (ref.sha256, ref.size):
                            raise ValueError("Source bytes changed before copy")
                        source.seek(0)
                    actual = target.put(source, object_id=ref.object_id, media_type=ref.media_type)
                if actual != ref:
                    raise ValueError("Copied object checksum mismatch")
                target.stat(ref)
                with session_scope() as session:
                    self._assert_source(session, plan, full=False)
                    attach_blob(session, ref=ref, owner_type="migration", owner_id=plan["id"], generation_id=None, logical_name=ref.object_id)
                    row = session.get(models.ArtifactStorageMigration, plan["id"])
                    row.receipts = {**row.receipts, ref.object_id: asdict(ref)}
                    row.status, row.verified_hash = "copying", None
                    session.info["artifact_storage_copy"] = True
            with session_scope() as session:
                self._assert_source(session, plan)
            return {"copied": len(plan["objects"])}

    def _verify(self, plan):
        with session_scope() as session:
            self._assert_source(session, plan)
            receipt = session.get(models.ArtifactStorageMigration, plan["id"])
            if set(receipt.receipts) != {item["ref"]["object_id"] for item in plan["objects"]}:
                raise ValueError("Copy is incomplete")
            for item in plan["objects"]:
                ref = BlobRef(**item["ref"])
                if receipt.receipts[ref.object_id] != asdict(ref) or resolve_blob(session, owner_type="migration",
                        owner_id=plan["id"], generation_id=None, logical_name=ref.object_id) != ref:
                    raise ValueError("Migration pin/receipt missing")
        target = self.store(plan["target"])
        from .gc import initialize_gc
        initialize_gc(self._settings(plan["target"]), store=target)
        for check in plan["source_files"]:
            with self._open_source(check) as source:
                digest, size = hashlib.sha256(), 0
                while block := source.read(64 * 1024):
                    digest.update(block)
                    size += len(block)
            if (digest.hexdigest(), size) != (check["sha256"], check["size"]):
                raise ValueError("Original source file changed after planning")
        for value in plan["source_blobs"]:
            self.store(plan["source"]).stat(BlobRef(**value))
        for item in plan["objects"]:
            with self._open_source(item) as source:
                digest, size = hashlib.sha256(), 0
                while block := source.read(64 * 1024):
                    size += len(block)
                    digest.update(block)
            if (digest.hexdigest(), size) != (item["ref"]["sha256"], item["ref"]["size"]):
                raise ValueError("Source bytes changed")
            target.stat(BlobRef(**item["ref"]))
        with session_scope() as session:
            self._assert_source(session, plan)
            receipt = session.get(models.ArtifactStorageMigration, plan["id"])
            receipt.status, receipt.verified_hash = "verified", plan["plan_hash"]
        return {"verified": len(plan["objects"])}

    def verify(self, *, apply=False, writers_stopped=False, backup=None):
        with self._operation(apply=apply, writers_stopped=writers_stopped, backup=backup) as (plan, status):
            return self._committed(plan) if status == "cutover" else self._verify(plan)

    def _committed(self, plan):
        with session_scope() as session:
            control = session.get(models.ArtifactStorageControl, 1)
            if control.migration_id != plan["id"] or control.active_descriptor != plan["target"]:
                raise ValueError("This migration is no longer the active selection")
        return {"cutover": True, "store_id": plan["target"]["store_id"]}

    def cutover(self, *, apply=False, writers_stopped=False, backup=None):
        with self._operation(apply=apply, writers_stopped=writers_stopped, backup=backup) as (plan, status):
            if status == "cutover":
                return self._committed(plan)
            self._verify(plan)  # Never trust a previous verify result after an interruption.
            from .migration_plan import apply_plan
            with session_scope() as session:
                control = self._lock(session, plan)
                if session.get_bind().dialect.name == "postgresql":
                    from sqlalchemy import text
                    session.execute(text("LOCK TABLE " + ",".join('"' + name + '"' for name in CORPUS) + " IN SHARE ROW EXCLUSIVE MODE"))
                self._assert_source(session, plan)
                apply_plan(session, plan)
                receipt = session.get(models.ArtifactStorageMigration, plan["id"])
                if receipt.status != "verified" or receipt.verified_hash != plan["plan_hash"]:
                    raise ValueError("Unverified target cannot become active")
                control.active_store_id, control.active_descriptor = plan["target"]["store_id"], plan["target"]
                control.readonly_roots = sorted(set([*control.readonly_roots, str(self.settings.data_dir.absolute()), str(self.settings.diagnostics_dir.absolute()),
                                          *([plan["source"]["root"]] if "root" in plan["source"] else [])]))
                control.maintenance_token, control.migration_id, receipt.status = None, plan["id"], "cutover"
                session.info["artifact_storage_cutover"] = True
            return self._committed(plan)
