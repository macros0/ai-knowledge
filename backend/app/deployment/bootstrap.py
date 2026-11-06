"""Offline fresh-install tools; no environment or runtime identity bypass."""
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from pydantic import model_validator
from sqlalchemy import inspect, literal, select, text

from app.config import Settings
from app.db.models import ArtifactStorageControl, ReplicaRuntimeIdentity, MaintenanceState
from app.services.artifacts.control import descriptor, migration_access
from app.services.artifacts.contract import BlobConflict, BlobNotFound
from app.services.work.store import _now
from .runtime_identity import SCHEMA_REVISION, identity_document

CHECKS = ["conditional_put", "cold_read", "range_read", "delete"]
RECEIPT_KEYS = {"version", "installation_id", "namespace", "store_id", "backend",
    "descriptor_sha256", "selection_digest", "checks", "qualification"}


class OperatorSettings(Settings):
    """Only this explicit offline constructor can prepare a missing document.

    All structural/auth/configuration validators are inherited. API/worker startup
    revalidates the actual identity file even if passed an OperatorSettings object.
    """
    @model_validator(mode="after")
    def validate_common_runtime_identity(self):
        return self


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _receipt(settings, installation_id, selection_digest):
    if (settings.okf_deployment_schema != "replicas-v1" or not settings.deployment_namespace
            or not isinstance(installation_id, str) or not isinstance(selection_digest, str)
            or not re.fullmatch(r"[0-9a-f]{32}", installation_id)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", selection_digest)):
        raise ValueError("Explicit replica installation/selection identity required")
    return {"version": 1, "installation_id": installation_id, "namespace": settings.deployment_namespace,
        "store_id": settings.artifact_store_id, "backend": settings.artifact_backend,
        "descriptor_sha256": sha256(canonical(descriptor(settings))).hexdigest(),
        "selection_digest": selection_digest, "checks": CHECKS, "qualification": "basic-bootstrap-only"}


def probe_artifacts(settings, *, installation_id, selection_digest):
    """Only newly generated probe IDs are written/deleted; never inventory data."""
    from app.services.artifacts import get_blob_store
    receipt = _receipt(settings, installation_id, selection_digest)
    payload = os.urandom(16384)
    ref = None
    with migration_access():
        store = get_blob_store(settings)
        try:
            object_id = uuid4().hex
            ref = store.put(io.BytesIO(payload), object_id=object_id, media_type="application/octet-stream")
            if store.put(io.BytesIO(payload), object_id=object_id, media_type=ref.media_type) != ref:
                raise ValueError("Artifact idempotent publication failed")
            try:
                store.put(io.BytesIO(b"different"), object_id=object_id, media_type=ref.media_type)
            except BlobConflict:
                pass
            else:
                raise ValueError("Artifact conditional publication unsupported")
            cold = get_blob_store(settings)
            if cold.stat(ref) != ref:
                raise ValueError("Artifact cold stat failed")
            with cold.open(ref) as stream:
                if stream.read() != payload:
                    raise ValueError("Artifact cold read failed")
            with cold.open(ref, start=3, end=17) as stream:
                if stream.read() != payload[3:17]:
                    raise ValueError("Artifact range read failed")
        finally:
            if ref is not None:
                store.delete(ref)
        try:
            store.stat(ref)
        except BlobNotFound:
            pass
        else:
            raise ValueError("Artifact deletion not visible")
    return receipt


def install_identity(session, settings, receipt):
    """Caller owns a trusted migration transaction; existing data is never adopted."""
    if (not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS or type(receipt["version"]) is not int
            or receipt != _receipt(settings, receipt.get("installation_id", ""), receipt.get("selection_digest", ""))):
        raise ValueError("Storage probe receipt does not match the installation")
    connection = session.connection()
    if (not inspect(connection).has_table("alembic_version")
            or connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() != [SCHEMA_REVISION]):
        raise ValueError("Explicit replica schema migration required before bootstrap")
    document = identity_document(settings, installation_id=receipt["installation_id"],
        storage_receipt_digest="sha256:" + sha256(canonical(receipt)).hexdigest())
    control = session.scalar(select(ArtifactStorageControl).where(ArtifactStorageControl.id == 1).with_for_update())
    if control is None or control.maintenance_token:
        raise ValueError("Installation control is absent or undergoing maintenance")
    existing = session.get(ReplicaRuntimeIdentity, 1)
    if existing is not None:
        maintenance = session.get(MaintenanceState, 1)
        if (existing.installation_id != document["installation_id"] or existing.fingerprint != document["runtime_fingerprint"]
                or existing.storage_receipt_digest != document["storage_receipt_digest"]
                or existing.schema_revision != SCHEMA_REVISION or control.installation_id != existing.installation_id
                or control.active_store_id != settings.artifact_store_id or control.active_descriptor != descriptor(settings)
                or maintenance is None or maintenance.installation_id != existing.installation_id
                or maintenance.enabled or maintenance.epoch != 0 or maintenance.operation_id is not None):
            raise ValueError("Installed runtime identity differs; coordinated maintenance is required")
        return document
    assert_fresh_installation(connection)
    control.installation_id = receipt["installation_id"]
    control.active_store_id, control.active_descriptor = settings.artifact_store_id, descriptor(settings)
    session.add(ReplicaRuntimeIdentity(id=1, installation_id=document["installation_id"],
        fingerprint=document["runtime_fingerprint"], storage_receipt_digest=document["storage_receipt_digest"],
        schema_revision=SCHEMA_REVISION, created_at=_now(session)))
    session.add(MaintenanceState(id=1, installation_id=document["installation_id"], epoch=0,
        enabled=False, intended_counts_json={}, writer_receipts_json={}))
    session.flush()
    return document


def initialize_empty_vector_store(settings):
    """Fresh installation only; a populated collection belongs to O2 adoption."""
    from app.services.vector_store import VectorStore, PAYLOAD_INDEX_FIELDS, SPARSE_VECTOR_NAME
    from qdrant_client import models as qm
    store = VectorStore(settings=settings)
    if store.client.collection_exists(store.collection):
        if store.client.count(store.collection, exact=True).count:
            raise ValueError("A nonempty vector collection requires maintenance adoption")
        vectors = store.client.get_collection(store.collection).config.params.vectors
        if not isinstance(vectors, qm.VectorParams) or vectors.size != settings.embedding_dimensions or vectors.distance != qm.Distance.COSINE:
            raise ValueError("Existing vector collection has a different embedding identity")
    store.ensure_collection()
    info = store.client.get_collection(store.collection)
    sparse = (info.config.params.sparse_vectors or {}).get(SPARSE_VECTOR_NAME)
    if sparse is None or sparse.modifier != qm.Modifier.IDF or any(
            name not in info.payload_schema or info.payload_schema[name].data_type != qm.PayloadSchemaType(kind)
            for name, kind in PAYLOAD_INDEX_FIELDS.items()):
        raise ValueError("Vector collection/index schema did not pass verification")


def assert_fresh_installation(connection):
    """Permit only exact migration seeds; edited reference data is not fresh."""
    from app.db.base import Base
    from app.db.models import Locale, Stopword, ReferenceResourceState, GlossaryState
    from app.services.stopwords import DEFAULT_LOCALES, default_stopwords
    from app.services.reference_resources import ReferenceKey
    names = set(inspect(connection).get_table_names())
    if names - set(Base.metadata.tables) - {"alembic_version"}:
        raise ValueError("Unknown/nonempty installation requires maintenance adoption")
    allowed = {"alembic_version", "artifact_storage_control", "glossary_state", "work_admission_state",
        "locales", "stopwords", "reference_resource_states"}
    for name, table in Base.metadata.tables.items():
        if name in names and name not in allowed and connection.execute(
                select(literal(1)).select_from(table).limit(1)).first() is not None:
            raise ValueError("A nonempty installation requires maintenance adoption")
    def rows(model):
        return list(connection.execute(select(model.__table__)).mappings()) if model.__tablename__ in names else []
    locales = rows(Locale)
    if locales and ({(row["code"], row["name"], row["status"]) for row in locales} != set(DEFAULT_LOCALES)
            or any(row["ui_dictionary_version"] is not None for row in locales)):
        raise ValueError("A nonempty locale configuration requires maintenance adoption")
    defaults = default_stopwords()
    expected_words = {(locale, kind, word) for locale, kinds in defaults.items() for kind, words in kinds.items() for word in words}
    words = rows(Stopword)
    if words and ({(row["locale"], row["kind"], row["word"]) for row in words} != expected_words
            or any(row["created_by"] != "seed" or row["updated_by"] is not None for row in words)):
        raise ValueError("A nonempty stopword configuration requires maintenance adoption")
    expected_keys = {ReferenceKey(kind, code).canonical for code, _n, _s in DEFAULT_LOCALES for kind in ("locale", "ui_dictionary")}
    expected_keys |= {ReferenceKey("stopword_set", code, kind=kind).canonical for code, kinds in defaults.items() for kind in kinds}
    references = rows(ReferenceResourceState)
    if references and ({row["resource_key"] for row in references} != expected_keys or any(
            row["revision"] != 1 or row["lease_epoch"] != 0 or row["activity_sequence"] != 0
            or any(value is not None for key, value in row.items() if key not in {"resource_key", "revision", "lease_epoch", "activity_sequence"})
            for row in references)):
        raise ValueError("A nonempty reference configuration requires maintenance adoption")
    if any(row["revision"] != 0 or not row["identities_ready"] for row in rows(GlossaryState)):
        raise ValueError("A nonempty glossary requires maintenance adoption")
    if "artifact_storage_control" in names:
        controls = connection.execute(select(ArtifactStorageControl.__table__)).mappings()
        if any(row["active_store_id"] is not None or row["active_descriptor"] is not None or row["readonly_roots"]
                or row["maintenance_token"] or row["migration_id"] for row in controls):
            raise ValueError("Selected/nonempty storage requires maintenance adoption")


def write_identity(path, document):
    """Create-only atomic publication; a committed DB record can be retried."""
    path = Path(path)
    raw = canonical(document) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError("Refusing to replace an existing identity document")
        return
    with tempfile.NamedTemporaryFile(prefix=".identity-", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != raw:
                raise ValueError("Concurrent identity publication differs") from None
    finally:
        temporary.unlink()
