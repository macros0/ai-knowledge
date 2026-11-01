"""Authenticated format3 archives of a fenced source; provider orchestration is separate."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import stat
import struct
from typing import Annotated, Literal
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from filelock import FileLock
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.services.diagnostics.paths import assert_no_links, canonical_root

CHUNK = 4 * 1024 * 1024
MAX_MANIFEST = 64 * 1024 * 1024
MAX_OBJECT = 1024**5
MAGIC = b"OKF3"
Hex32 = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
Component = Literal["postgres", "qdrant", "artifacts"]


class BackupError(ValueError):
    """Public fixed-message refusal; never wrap a raw source/provider exception."""


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Writers(Model):
    api: Literal["stopped", "drained"]
    worker: Literal["stopped", "drained"]
    audit_dispatcher: Literal["stopped", "drained"]
    diagnostic_children: Literal["stopped", "drained"]
    provider_requests: Literal["stopped", "drained"]
    maintenance_jobs: Literal["stopped", "drained"]


class SourceState(Model):
    installation_id: Hex32
    namespace: Annotated[str, Field(pattern=r"^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$")]
    operation_id: Hex32
    epoch: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
    consistent_at: AwareDatetime
    failure_domain: Name
    unknown_writers: Count
    provider_requests: Count
    writers: Writers
    snapshot_id: Name

    @model_validator(mode="after")
    def quiescent(self):
        if self.unknown_writers or self.provider_requests:
            raise ValueError("Source writers are not verified quiescent")
        return self


class Destination(Model):
    backend: Literal["filesystem"] = "filesystem"
    store_id: Name
    failure_domain: Name
    platform_evidence_sha256: Digest


class Storage(Model):
    component: Component
    provider: Literal["managed", "external"]
    recipe: Name
    scope_sha256: Digest


class Counts(Model):
    documents: Count
    concepts: Count
    chunks: Count
    blobs: Count
    work_items: Count
    audit_events: Count


class ExpectedObject(Model):
    id: Hex32
    component: Component
    expected_size: Count | None = None
    expected_sha256: Digest | None = None

    @model_validator(mode="after")
    def accepted_artifact(self):
        if self.component == "artifacts" and (self.expected_size is None or self.expected_sha256 is None):
            raise ValueError("Accepted artifact checksum and size required")
        return self


class BackupPlan(Model):
    format: Literal[3]
    backup_id: Hex32
    schema_revision: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,32}$")]
    started_at: AwareDatetime
    source: SourceState
    destination: Destination
    images: dict[Literal["frontend", "api", "worker"], Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]]
    storage: tuple[Storage, ...]
    model_vector_sha256: Digest
    counts: Counts
    component_receipts: dict[Component, Digest]
    key_reference: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:/-]{1,256}$")]
    inventory: Annotated[tuple[ExpectedObject, ...], Field(min_length=2, max_length=100000)]

    @model_validator(mode="after")
    def complete_scope(self):
        if (self.backup_id != self.source.operation_id or self.started_at > self.source.consistent_at
                or self.source.failure_domain == self.destination.failure_domain
                or set(self.images) != {"frontend", "api", "worker"}
                or len(self.storage) != 3 or {s.component for s in self.storage} != {"postgres", "qdrant", "artifacts"}
                or set(self.component_receipts) != {"postgres", "qdrant", "artifacts"}
                or len({e.id for e in self.inventory}) != len(self.inventory)
                or sum(e.component == "postgres" for e in self.inventory) != 1
                or sum(e.component == "qdrant" for e in self.inventory) != 1
                or sum(e.component == "artifacts" for e in self.inventory) != self.counts.blobs):
            raise ValueError("Incomplete or non-independent backup scope")
        return self


class ObjectReceipt(Model):
    component: Component
    size: Count
    sha256: Digest
    ciphertext_size: Count
    ciphertext_sha256: Digest

    @model_validator(mode="after")
    def provider_export_not_empty(self):
        if self.component != "artifacts" and self.size == 0:
            raise ValueError("Provider export is empty")
        return self


class BackupManifest(Model):
    format: Literal[3] = 3
    plan: BackupPlan
    status: Literal["incomplete", "complete"]
    completed_at: AwareDatetime | None = None
    destination_verified_at: AwareDatetime | None = None
    objects: dict[Hex32, ObjectReceipt] = Field(default_factory=dict)
    authentication: Digest

    @model_validator(mode="after")
    def closure(self):
        inventory = {e.id: e for e in self.plan.inventory}
        if set(self.objects) - set(inventory) or any(
                receipt.component != inventory[key].component for key, receipt in self.objects.items()):
            raise ValueError("Backup object inventory differs")
        if self.status == "complete" and (set(self.objects) != set(inventory)
                or self.completed_at is None or self.destination_verified_at is None
                or self.completed_at < self.plan.source.consistent_at
                or self.destination_verified_at > self.completed_at
                or self.destination_verified_at < self.plan.source.consistent_at):
            raise ValueError("Backup completion evidence missing")
        return self


def canonical(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _derive(key, backup_id, *, entry_id=None, file_salt=b""):
    if not isinstance(key, bytes) or len(key) != 32:
        raise BackupError("A separate 32-byte backup key is required")
    salt = bytes.fromhex(backup_id) + (bytes.fromhex(entry_id) if entry_id else b"") + file_salt
    purpose = b"okf.replica.backup.payload.v3" if entry_id else b"okf.replica.backup.manifest.v3"
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=purpose).derive(key)


def _seal(manifest, key):
    value = manifest.model_dump(mode="json")
    value.pop("authentication")
    authentication = hmac.new(_derive(key, manifest.plan.backup_id), canonical(value), hashlib.sha256).hexdigest()
    return BackupManifest.model_validate({**value, "authentication": authentication})


def _regular_open(path, root):
    assert_no_links(path, root)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        before = os.fstat(descriptor)
        after = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)):
            raise BackupError("Backup file is not a stable regular file")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _load(root, key):
    with _regular_open(root / "manifest.json", root) as stream:
        raw = stream.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise BackupError("Backup manifest limit exceeded")
    manifest = BackupManifest.model_validate_json(raw)
    if not hmac.compare_digest(_seal(manifest, key).authentication, manifest.authentication):
        raise BackupError("Backup manifest authentication failed")
    return manifest


def _sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _aad(plan_digest, entry, counter, final):
    return canonical({"format": 3, "plan": plan_digest, "id": entry.id,
        "component": entry.component, "frame": counter, "final": final})


def _inspect_object(root, plan, entry, key, *, target=None):
    path = root / "objects" / (entry.id + ".bin")
    plain_hash, cipher_hash = hashlib.sha256(), hashlib.sha256()
    total, cipher_size, counter = 0, 0, 0
    plan_digest = hashlib.sha256(canonical(plan)).hexdigest()
    with _regular_open(path, root) as stream:
        def read(size):
            nonlocal cipher_size
            data = stream.read(size)
            cipher_hash.update(data)
            cipher_size += len(data)
            if len(data) != size:
                raise BackupError("Backup object is truncated")
            return data
        if read(4) != MAGIC:
            raise BackupError("Unsupported backup object")
        file_salt = read(16)
        aes = AESGCM(_derive(key, plan.backup_id, entry_id=entry.id, file_salt=file_salt))
        while True:
            length, final = struct.unpack(">IB", read(5))
            if not 16 <= length <= CHUNK + 16 or final not in {0, 1} or counter >= 2**32:
                raise BackupError("Invalid backup frame")
            data = aes.decrypt(counter.to_bytes(12, "big"), read(length), _aad(plan_digest, entry, counter, bool(final)))
            plain_hash.update(data)
            total += len(data)
            if total > MAX_OBJECT or not final and len(data) != CHUNK:
                raise BackupError("Invalid backup frame size")
            if target is not None:
                remaining = memoryview(data)
                while remaining:
                    written = target.write(remaining)
                    if type(written) is not int or not 0 < written <= len(remaining):
                        raise BackupError("Restore output did not accept the complete frame")
                    remaining = remaining[written:]
            counter += 1
            if final:
                if stream.read(1):
                    raise BackupError("Backup object has trailing data")
                break
    receipt = ObjectReceipt(component=entry.component, size=total, sha256=plain_hash.hexdigest(),
        ciphertext_size=cipher_size, ciphertext_sha256=cipher_hash.hexdigest())
    if (entry.expected_size is not None and receipt.size != entry.expected_size
            or entry.expected_sha256 is not None and receipt.sha256 != entry.expected_sha256):
        raise BackupError("Accepted source checksum or size differs")
    return receipt


def verify_backup(root, key, *, require_complete=True):
    try:
        root = canonical_root(root)
        assert_no_links(root, root)
        manifest = _load(root, key)
        if require_complete and manifest.status != "complete":
            raise BackupError("Backup is incomplete")
        entries = {e.id: e for e in manifest.plan.inventory}
        for entry_id, receipt in manifest.objects.items():
            if _inspect_object(root, manifest.plan, entries[entry_id], key) != receipt:
                raise BackupError("Backup object verification failed")
        return manifest
    except Exception:
        raise BackupError("Backup verification failed") from None


class BackupReader:
    """Decode for a coordinator's held new target; never activate it here.

    Whole-archive authentication precedes any output. Every copied frame is
    authenticated again; callers must keep the new target held on any failure
    and call verify before accepting the complete restored component set.
    """
    def __init__(self, root, key):
        self.root, self._key = canonical_root(root), key
        self.manifest = verify_backup(self.root, key)

    def copy(self, entry_id, target):
        try:
            entry = next(e for e in self.manifest.plan.inventory if e.id == entry_id)
            receipt = _inspect_object(self.root, self.manifest.plan, entry, self._key, target=target)
            if receipt != self.manifest.objects[entry_id]:
                raise BackupError("Restored object differs from the verified archive")
            return receipt
        except Exception:
            raise BackupError("Backup object decoding failed") from None

    def verify(self):
        current = verify_backup(self.root, self._key)
        if current != self.manifest:
            raise BackupError("Backup archive changed during restore")
        return current


class BackupWriter:
    """Only encrypted bytes enter the archive; no live source is opened here.

    source_guard must revalidate the retained offline operation and frozen source.
    Platform independence evidence and writer receipts come from the coordinator,
    not from observing filenames or trusting a successful export exit code.
    """
    def __init__(self, root, plan, key, *, source_guard, destination_guard=None, resume=False):
        self.root = canonical_root(root)
        self.plan = BackupPlan.model_validate_json(plan.model_dump_json())
        self._plan_bytes = canonical(self.plan)
        self._plan_digest = hashlib.sha256(self._plan_bytes).hexdigest()
        self._key, self._guard, self._resume = key, source_guard, resume
        self._destination_guard = destination_guard
        _derive(key, self.plan.backup_id)
        self._lock, self._open = None, False
        self.manifest = BackupManifest(plan=self.plan, status="incomplete", authentication="0" * 64)

    def _check_source(self):
        if canonical(self.plan) != self._plan_bytes or self._guard() != self.plan.source:
            raise BackupError("Frozen source ownership or identity changed")
        if self._destination_guard is None or self._destination_guard() != self.plan.destination:
            raise BackupError("Independent destination binding is not verified")

    def _persist(self, manifest):
        manifest = _seal(manifest, self._key)
        raw = canonical(manifest) + b"\n"
        if len(raw) > MAX_MANIFEST:
            raise BackupError("Backup manifest limit exceeded")
        destination = self.root / "manifest.json"
        if destination.exists():
            existing = _load(self.root, self._key)
            if canonical(existing.plan) != self._plan_bytes or existing.status == "complete":
                raise BackupError("Existing backup manifest cannot be replaced")
        path = self.root / (".manifest-" + uuid4().hex + ".part")
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        assert_no_links(destination, self.root)
        os.replace(path, destination)
        _sync_directory(self.root)
        self.manifest = manifest

    def __enter__(self):
        try:
            self._check_source()
            assert_no_links(self.root / "objects", self.root)
            assert_no_links(self.root, self.root)
            if self._resume:
                if not self.root.is_dir():
                    raise BackupError("Backup resume directory missing")
            else:
                self.root.mkdir(mode=0o700)
                (self.root / "objects").mkdir(mode=0o700)
            assert_no_links(self.root / "objects", self.root)
            assert_no_links(self.root / ".writer.lock", self.root)
            self._lock = FileLock(self.root / ".writer.lock", timeout=0)
            self._lock.acquire()
            if self._resume:
                self.manifest = verify_backup(self.root, self._key, require_complete=False)
                if canonical(self.manifest.plan) != self._plan_bytes or self.manifest.status != "incomplete":
                    raise BackupError("Only the same incomplete backup can resume")
                # A crash can publish an authenticated object before its receipt
                # reaches the manifest. Reconstruct it; never overwrite it.
                objects = dict(self.manifest.objects)
                for entry in self.plan.inventory:
                    if entry.id not in objects and (self.root / "objects" / (entry.id + ".bin")).exists():
                        objects[entry.id] = _inspect_object(self.root, self.plan, entry, self._key)
                self.manifest = self.manifest.model_copy(update={"objects": objects})
            self._persist(self.manifest)
            self._open = True
            return self
        except Exception:
            if self._lock is not None:
                self._lock.release()
            raise BackupError("Backup initialization failed") from None

    def add(self, entry_id, source):
        try:
            if not self._open or self.manifest.status != "incomplete":
                raise BackupError("Backup writer is not open")
            self._check_source()
            assert_no_links(self.root / "objects", self.root)
            entry = next(e for e in self.plan.inventory if e.id == entry_id)
            if entry_id in self.manifest.objects:
                return self.manifest.objects[entry_id]
            file_salt = os.urandom(16)
            aes = AESGCM(_derive(self._key, self.plan.backup_id, entry_id=entry.id, file_salt=file_salt))
            path = self.root / "objects" / (entry.id + "." + uuid4().hex + ".part")
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            plain_hash, cipher_hash = hashlib.sha256(), hashlib.sha256()
            total, cipher_size, counter = 0, 0, 0
            def read():
                block = source.read(CHUNK)
                if not isinstance(block, bytes) or len(block) > CHUNK:
                    raise BackupError("Invalid source stream")
                return block
            with os.fdopen(descriptor, "wb") as target:
                def write(data):
                    nonlocal cipher_size
                    target.write(data)
                    cipher_hash.update(data)
                    cipher_size += len(data)
                write(MAGIC + file_salt)
                block = read()
                while True:
                    next_block = read() if block else b""
                    final = not next_block
                    total += len(block)
                    if (total > MAX_OBJECT or counter >= 2**32
                            or entry.expected_size is not None and total > entry.expected_size):
                        raise BackupError("Backup object limit exceeded")
                    plain_hash.update(block)
                    encrypted = aes.encrypt(counter.to_bytes(12, "big"), block,
                        _aad(self._plan_digest, entry, counter, final))
                    write(struct.pack(">IB", len(encrypted), int(final)) + encrypted)
                    counter += 1
                    if final:
                        break
                    block = next_block
                target.flush()
                os.fsync(target.fileno())
            receipt = ObjectReceipt(component=entry.component, size=total, sha256=plain_hash.hexdigest(),
                ciphertext_size=cipher_size, ciphertext_sha256=cipher_hash.hexdigest())
            if (entry.expected_size is not None and total != entry.expected_size
                    or entry.expected_sha256 is not None and receipt.sha256 != entry.expected_sha256):
                raise BackupError("Source checksum or size differs")
            self._check_source()
            assert_no_links(self.root / "objects", self.root)
            destination = self.root / "objects" / (entry.id + ".bin")
            # Atomic create-only publication: an old or foreign object cannot
            # be replaced, even after an ambiguous previous publication.
            os.link(path, destination)
            path.unlink()
            self.manifest = self.manifest.model_copy(update={"objects": {**self.manifest.objects, entry.id: receipt}})
            return receipt
        except Exception:
            raise BackupError("Backup object export failed") from None

    def finish(self, *, now=None):
        try:
            if not self._open or self.manifest.status != "incomplete":
                raise BackupError("Backup writer is not open")
            self._check_source()
            if set(self.manifest.objects) != {e.id for e in self.plan.inventory}:
                raise BackupError("Backup inventory is incomplete")
            _sync_directory(self.root / "objects")
            self._persist(self.manifest)
            # A fresh reader opens every destination object and authenticates
            # all frames, counts and full plaintext/ciphertext hashes.
            verify_backup(self.root, self._key, require_complete=False)
            self._check_source()
            completed_at = now or datetime.now(timezone.utc)
            candidate = BackupManifest(plan=self.plan, status="complete", completed_at=completed_at,
                destination_verified_at=completed_at, objects=self.manifest.objects, authentication="0" * 64)
            self._persist(candidate)
            return self.manifest
        except Exception:
            raise BackupError("Backup completion refused") from None

    def __exit__(self, *_exc):
        try:
            if self.manifest.status == "incomplete":
                self._persist(self.manifest)
        finally:
            self._open = False
            if self._lock is not None:
                self._lock.release()


def recovery_point_age(manifest, *, now):
    """Use only after verification; copy finish time never refreshes the RPO."""
    if manifest.status != "complete" or now.tzinfo is None:
        raise BackupError("Verified complete backup and aware time required")
    age = (now - manifest.plan.source.consistent_at).total_seconds()
    if age < 0:
        raise BackupError("Recovery point time is in the future")
    return age
