"""Owned offline adoption after verified S4 cutover; never release or startup bypass.

Physical writer stops, independent restoration and provider/corpus-byte checks
belong to the trusted coordinator. This phase installs only a held SQL identity.
"""
from hashlib import sha256
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, inspect, or_, select, text

from app.db.base import Base
from app.db.models import (ArtifactStorageControl, ArtifactStorageMigration, AuditLog, AuditOutbox, ChatRun,
    Document, DocumentGenerationState, MaintenanceState, ReplicaCapacitySlot, ReplicaRuntimeIdentity, WorkItem)
from app.services.artifacts.control import descriptor
from app.services.artifacts.publication import canonical_hash
from app.services.work.maintenance_control import MaintenanceRefused, REQUIRED_WRITERS, _require_operator
from app.services.work.models import WorkSpec
from .bootstrap import _receipt, canonical
from .runtime_identity import SCHEMA_REVISION, identity_document

Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Hex32 = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
Count = Annotated[int, Field(ge=0, le=2**63-1)]
AUDIT_TARGET = 'replica_compact_adoption'
EXCLUDED = {'artifact_storage_control', 'replica_runtime_identity', 'replica_maintenance_state'}


class CompactSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, hide_input_in_errors=True)
    schema_version: Literal[1]
    installation_id: Hex32
    schema_revision: Literal[SCHEMA_REVISION]
    store_id: Annotated[str, Field(min_length=1, max_length=128)]
    migration_id: Hex32
    selection_sha256: Digest
    plan_sha256: Digest
    backup_sha256: Digest
    sql_sha256: Digest
    document_count: Count
    legacy_document_count: Count
    queued_work_count: Count
    audit_log_upper_id: Count
    audit_outbox_upper_id: Count

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('Integer version required')
        return value


def _expected(value):
    try:
        return CompactSource.model_validate(value.model_dump() if isinstance(value,CompactSource) else value)
    except (ValidationError, ValueError, TypeError):
        raise MaintenanceRefused('Invalid bounded compact source proof') from None


def _schema(session):
    connection = session.connection()
    inspector = inspect(connection)
    if set(inspector.get_table_names()) != set(Base.metadata.tables) | {'alembic_version'}:
        raise MaintenanceRefused('Exact known current application schema required')
    for name, table in Base.metadata.tables.items():
        if ({column['name'] for column in inspector.get_columns(name)} != set(table.columns.keys())
                or set(inspector.get_pk_constraint(name).get('constrained_columns') or []) != set(table.primary_key.columns.keys())):
            raise MaintenanceRefused('Unknown or incomplete application columns required migration review')
    if session.execute(text('SELECT version_num FROM alembic_version')).scalars().all() != [SCHEMA_REVISION]:
        raise MaintenanceRefused('Explicit current Alembic schema required')


def _selection(session, settings):
    control = session.get(ArtifactStorageControl,1)
    if (settings.okf_deployment_schema != 'replicas-v1' or settings.artifact_backend not in {'rwx','s3'}
            or not settings.runtime_image_id or control is None or not control.migration_id
            or control.active_store_id != settings.artifact_store_id or control.active_descriptor != descriptor(settings)):
        raise MaintenanceRefused('A selected completed shared-artifact migration is required')
    history = session.get(ArtifactStorageMigration,control.migration_id)
    if (history is None or history.installation_id != control.installation_id or history.status != 'cutover'
            or history.target_store_id != control.active_store_id or history.verified_hash != history.plan_hash):
        raise MaintenanceRefused('Artifact migration outcome is not verified')
    return control,history


def _snapshot(session, settings, *, bounds=None, handoff_ids=()):
    control,history = _selection(session,settings)
    caps = bounds or {'audit_log':session.scalar(select(func.max(AuditLog.id))) or 0,
        'audit_outbox':session.scalar(select(func.max(AuditOutbox.id))) or 0}
    digest = sha256()
    for name,table in sorted(Base.metadata.tables.items()):
        if name in EXCLUDED:
            continue
        digest.update(name.encode()+b'\0')
        statement = select(table).order_by(*table.primary_key.columns)
        if name in caps:
            statement = statement.where(table.c.id<=caps[name])
        if handoff_ids and name in {'work_items','document_work_inputs'}:
            statement=statement.where(table.c['id' if name=='work_items' else 'work_id'].not_in(handoff_ids))
        for row in session.execute(statement.execution_options(yield_per=100)).mappings():
            digest.update(canonical_hash(dict(row)).encode())
    selection = {name:getattr(control,name) for name in (
        'installation_id','active_store_id','active_descriptor','readonly_roots','migration_id')}
    return _expected(dict(schema_version=1,installation_id=control.installation_id,schema_revision=SCHEMA_REVISION,
        store_id=control.active_store_id,migration_id=history.id,selection_sha256=canonical_hash(selection),
        plan_sha256=history.plan_hash,backup_sha256=history.backup_hash,sql_sha256=digest.hexdigest(),
        document_count=session.scalar(select(func.count()).select_from(Document)),
        legacy_document_count=session.scalar(select(func.count()).select_from(Document).outerjoin(
            DocumentGenerationState,DocumentGenerationState.doc_id==Document.id).where(DocumentGenerationState.active_generation_id.is_(None))),
        queued_work_count=session.scalar(select(func.count()).select_from(WorkItem).where(
            WorkItem.status=='queued',WorkItem.id.not_in(handoff_ids))),
        audit_log_upper_id=caps['audit_log'],audit_outbox_upper_id=caps['audit_outbox']))


def capture_compact_source(session, settings):
    """Read a known source; normal fresh bootstrap still refuses these rows."""
    try:
        if session.new or session.dirty or session.deleted:
            raise MaintenanceRefused('Source capture requires a clean read transaction')
        _schema(session)
        if (session.get(ReplicaRuntimeIdentity,1) is not None or session.get(MaintenanceState,1) is not None
                or session.get(ArtifactStorageControl,1).maintenance_token is not None):
            raise MaintenanceRefused('This source is installed, held or undergoing another operation')
        return _snapshot(session,settings).model_dump()
    except MaintenanceRefused:
        raise
    except Exception:  # noqa: BLE001 -- source/configuration/SQL exceptions must not echo private values.
        raise MaintenanceRefused('Compact source is invalid or unavailable') from None


def _pending(session,settings):
    if (session.scalar(select(WorkItem.id).where(or_(WorkItem.status=='running',WorkItem.owner_id.is_not(None),
            WorkItem.lease_until.is_not(None),WorkItem.maintenance_epoch!=0)).limit(1))
            or session.scalar(select(ChatRun.id).where(ChatRun.status=='running').limit(1))
            or session.scalar(select(ReplicaCapacitySlot.id).where(or_(ReplicaCapacitySlot.owner_id.is_not(None),
                ReplicaCapacitySlot.release_required.is_(True))).limit(1))):
        raise MaintenanceRefused('Running, uncertain or unconverted legacy intent requires explicit reconciliation')
    from app.services.work.runtime import build_handlers
    supported = build_handlers().supported
    for item in session.scalars(select(WorkItem).where(WorkItem.status=='queued').execution_options(yield_per=100)):
        if item.payload_version not in supported.get(item.kind,set()):
            raise MaintenanceRefused('Queued work version is unsupported by this runtime')
        try:
            WorkSpec(item.kind,item.payload_version,item.payload_json,item.dedupe_key,item.doc_id)
        except (ValueError,TypeError):
            raise MaintenanceRefused('Queued work payload requires explicit migration') from None
    from .compact_handoff import validate_pending_intents
    validate_pending_intents(session,settings)


def _audit_extent(session, expected, proof, *, own):
    events=set()
    logs=list(session.scalars(select(AuditLog).where(AuditLog.id>expected.audit_log_upper_id)))
    outbox=list(session.scalars(select(AuditOutbox).where(AuditOutbox.id>expected.audit_outbox_upper_id)))
    if not own:
        if logs or outbox:
            raise MaintenanceRefused('Audit source changed after capture')
        return
    if len(logs)>1 or len(outbox)>1:
        raise MaintenanceRefused('Compact adoption audit is ambiguous')
    for row in logs:
        if (row.action_type!='replica_maintenance_enter' or row.target_type!=AUDIT_TARGET or row.target_id!=proof
                or row.new_value!={'epoch':1} or not row.event_id):
            raise MaintenanceRefused('Unexpected audit after captured compact source')
        events.add(row.event_id)
    for row in outbox:
        try:event=json.loads(row.payload)
        except (ValueError,TypeError):raise MaintenanceRefused('Invalid compact adoption audit') from None
        if (not isinstance(event,dict) or event.get('action')!='replica_maintenance_enter' or event.get('target')!={'type':AUDIT_TARGET,'id':proof}
                or event.get('new_value')!={'epoch':1} or event.get('event_id')!=row.event_id):
            raise MaintenanceRefused('Unexpected audit after captured compact source')
        events.add(row.event_id)
    if len(events)!=1:
        raise MaintenanceRefused('A durable unique compact adoption audit is required')


def adopt_compact_runtime(session, settings, expected, storage_receipt, *, operation_id, intended_counts, writer_receipts, handoff=None):
    """Install identity and epoch1 atomically, with no activation or work execution."""
    op = _require_operator(session,operation_id)
    source = _expected(expected)
    from .compact_handoff import apply_handoff, compile_handoff, reviewed_plan, verify_handoff
    reviewed=reviewed_plan(handoff,source) if handoff is not None else None
    if (op.settings is not settings or operation_id=='0'*32 or settings.runtime_identity_file is None
            or type(intended_counts) is not dict or set(intended_counts)!={'frontend','api','worker'}
            or any(type(n) is not int or not 0<=n<=64 for n in intended_counts.values())
            or type(writer_receipts) is not dict or set(writer_receipts)!=REQUIRED_WRITERS
            or any(type(value) is not str or value not in {'stopped','drained'} for value in writer_receipts.values())):
        raise MaintenanceRefused('Explicit offline ownership, counts and every verified writer receipt required')
    try:
        valid_receipt = (type(storage_receipt) is dict and type(storage_receipt.get('version')) is int
            and storage_receipt==_receipt(settings,source.installation_id,storage_receipt.get('selection_digest','')))
    except (ValueError,TypeError):valid_receipt=False
    if not valid_receipt:
        raise MaintenanceRefused('Storage probe receipt does not match the target')
    _schema(session)
    control=session.scalar(select(ArtifactStorageControl).where(ArtifactStorageControl.id==1).with_for_update())
    if control is None or control.maintenance_token not in {None,operation_id}:
        raise MaintenanceRefused('Another storage operation owns this source')
    if session.get_bind().dialect.name=='postgresql':
        names=sorted(Base.metadata.tables)
        session.execute(text('LOCK TABLE '+','.join('"'+name+'"' for name in names)+' IN SHARE ROW EXCLUSIVE MODE'))
    installed=session.get(ReplicaRuntimeIdentity,1,with_for_update=True)
    state=session.get(MaintenanceState,1,with_for_update=True)
    bounds={'audit_log':source.audit_log_upper_id,'audit_outbox':source.audit_outbox_upper_id}
    retry=installed is not None or state is not None
    handoff_ids=[entry.work_id for entry in reviewed.entries] if reviewed is not None else []
    if _snapshot(session,settings,bounds=bounds,handoff_ids=handoff_ids if retry else ())!=source:
        raise MaintenanceRefused('Compact source differs from the reviewed snapshot')
    intents=compile_handoff(session,settings,source,reviewed,retry=retry) if reviewed is not None else []
    document=identity_document(settings,installation_id=source.installation_id,
        storage_receipt_digest='sha256:'+sha256(canonical(storage_receipt)).hexdigest())
    proof_inputs=dict(source=source.model_dump(),identity=document,operation_id=operation_id,owner_id=op.owner_id,
        intended_counts=intended_counts,writer_receipts=writer_receipts)
    if reviewed is not None:proof_inputs['handoff_sha256']=canonical_hash(reviewed.model_dump())
    proof=canonical_hash(proof_inputs)
    if retry:
        if (installed is None or state is None or not state.enabled or state.epoch!=1
                or state.installation_id!=source.installation_id or state.operation_id!=operation_id
                or state.owner_id!=op.owner_id or state.operation_kind!='upgrade'
                or state.intended_counts_json!=intended_counts or state.writer_receipts_json!=writer_receipts
                or control.maintenance_token!=operation_id or installed.installation_id!=source.installation_id
                or installed.fingerprint!=document['runtime_fingerprint'] or installed.schema_revision!=SCHEMA_REVISION
                or installed.storage_receipt_digest!=document['storage_receipt_digest']):
            raise MaintenanceRefused('Existing installation is not this exact held compact adoption')
        verify_handoff(session,intents,state.started_at)
        _pending(session,settings)
        _audit_extent(session,source,proof,own=True)
        return document
    _audit_extent(session,source,proof,own=False)
    if control.maintenance_token is not None:
        raise MaintenanceRefused('Unowned initial storage token')
    from app.services.work.store import _now
    from app.services import audit
    from app.services.audit_event import AuditContext
    now=_now(session)
    apply_handoff(session,intents,now)
    _pending(session,settings)
    session.add(ReplicaRuntimeIdentity(id=1,installation_id=source.installation_id,
        fingerprint=document['runtime_fingerprint'],storage_receipt_digest=document['storage_receipt_digest'],
        schema_revision=SCHEMA_REVISION,created_at=now))
    session.add(MaintenanceState(id=1,installation_id=source.installation_id,epoch=1,enabled=True,
        operation_id=operation_id,operation_kind='upgrade',owner_id=op.owner_id,started_at=now,
        intended_counts_json=dict(intended_counts),writer_receipts_json=dict(writer_receipts)))
    control.maintenance_token=operation_id
    audit.record_in_session(session,action_type='replica_maintenance_enter',user_id='system',username='system',
        target_type=AUDIT_TARGET,target_id=proof,new_value={'epoch':1},
        event_context=AuditContext(actor_type='system'),settings=settings)
    session.flush()
    _audit_extent(session,source,proof,own=True)
    verify_handoff(session,intents,now)
    if _snapshot(session,settings,bounds=bounds,handoff_ids=handoff_ids)!=source:
        raise MaintenanceRefused('Logical source changed during compact adoption')
    return document
