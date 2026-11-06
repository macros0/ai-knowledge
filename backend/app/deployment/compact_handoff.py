"""Versioned offline recipes: preserve accepted SQL, add only pristine work/input.

No handlers, providers, files or queues are executed here. Physical source stops
and restoration remain separate coordinator prerequisites to held adoption.
"""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Annotated, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select

from app.db.models import (DiagnosticBundle, Document, DocumentGeneration, DocumentGenerationState,
    DocumentProcessingReview, DocumentScopeChange, DocumentStaging, DocumentWorkInput, Job, WorkItem)
from app.services.artifacts.contract import BlobRef
from app.services.artifacts.publication import canonical_hash
from app.services.artifacts.repository import resolve_blob
from app.services.work.maintenance_control import MaintenanceRefused
from app.services.work.models import WorkSpec
from app.services.work.store import _utc

MAX_INTENTS=4096
DOCUMENT_PENDING={'queued','processing','splitting','indexing'}
PARENT_KINDS={'bulk','export','reference_translation','glossary_translation'}
BULK_TYPES={'bulk_delete','bulk_regenerate','bulk_resume'}
Digest=Annotated[str,Field(pattern=r'^[0-9a-f]{64}$')]
Hex32=Annotated[str,Field(pattern=r'^[0-9a-f]{32}$')]


class HandoffEntry(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    recipe: Literal['bulk.v1','export.v1','reference_translation.v1','glossary_translation.v1',
        'diagnostic_bundle.v1','diagnostic_frozen.v1','document_resume.v1','scope_change.v1']
    source_id: Annotated[str,Field(pattern=r'^[A-Za-z0-9-]{1,64}$')]
    input_sha256: Digest
    work_id: Hex32


class CompactHandoff(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True,hide_input_in_errors=True)
    schema_version: Literal[1]
    installation_id: Hex32
    source_sql_sha256: Digest
    source_selection_sha256: Digest
    entries: Annotated[list[HandoffEntry],Field(max_length=MAX_INTENTS)]

    @field_validator('schema_version',mode='before')
    @classmethod
    def exact_version(cls,value):
        if type(value) is not int:raise ValueError('Integer version required')
        return value

    @model_validator(mode='after')
    def unique_entries(self):
        if (len({entry.work_id for entry in self.entries})!=len(self.entries)
                or len({(entry.recipe,entry.source_id) for entry in self.entries})!=len(self.entries)):
            raise ValueError('Unique handoff intents required')
        return self


def reviewed_plan(value,source):
    try:
        result=CompactHandoff.model_validate(value.model_dump() if isinstance(value,CompactHandoff) else value)
        if (result.installation_id,result.source_sql_sha256,result.source_selection_sha256)!=(
                source.installation_id,source.sql_sha256,source.selection_sha256):raise ValueError()
        return result
    except (ValueError,TypeError):
        raise MaintenanceRefused('Invalid source-bound legacy handoff plan') from None


def _row(row):
    return {column.name:getattr(row,column.name) for column in row.__table__.columns}


def _original(session,settings,doc_id):
    document=session.get(Document,doc_id)
    ref=resolve_blob(session,owner_type='original',owner_id=doc_id,generation_id=None,logical_name='original')
    if (document is None or document.deleted_at is not None or ref is None or ref.store_id!=settings.artifact_store_id
            or document.file_hash not in {None,ref.sha256}):
        raise MaintenanceRefused('Accepted original binding requires reconciliation')
    return document,ref


def _scope(scope,source_hash):
    if scope is None:return
    from docparser.attachment_policy import ParserProcessingPolicy
    from app.services.processing_scope import selection_hash
    ParserProcessingPolicy(scope)
    if (scope.get('source_file_hash')!=source_hash or scope.get('selection_hash')!=selection_hash(
            source_hash,scope.get('parser_version'),scope.get('traversal_limits'),scope.get('decisions',[]))):
        raise MaintenanceRefused('Accepted policy hash requires reconciliation')


def _related(session,kind,key,value,excluded):
    field=WorkItem.payload_json[key]
    field=field.as_integer() if type(value) is int else field.as_string()
    statement=select(WorkItem).where(WorkItem.kind==kind,field==value)
    if excluded:statement=statement.where(WorkItem.id.not_in(excluded))
    return list(session.scalars(statement.limit(2)))


def _backed(session,spec,excluded):
    key=next(key for key in ('job_id','bundle_id','plan_id','doc_id') if key in spec.payload)
    rows=_related(session,spec.kind,key,spec.payload[key],excluded)
    if not rows:return False
    if len(rows)!=1:raise MaintenanceRefused('Ambiguous accepted work requires reconciliation')
    row=rows[0]
    if (row.status!='queued' or row.payload_version!=spec.payload_version or row.payload_json!=spec.payload
            or row.doc_id!=spec.doc_id or row.cancel_requested):
        raise MaintenanceRefused('Accepted work does not match the queued intent')
    return True


def _export(session,settings,job):
    params=job.params
    documents=params.get('documents');parts=params.get('parts')
    if not isinstance(documents,list) or not documents or not isinstance(parts,list) or not parts:raise ValueError()
    accepted={}
    for value in documents:
        doc_id=value['doc_id'];_,original=_original(session,settings,doc_id)
        ref=BlobRef(**value['source_blob'])
        pinned=resolve_blob(session,owner_type='export',owner_id=str(job.id),generation_id=None,logical_name='source-'+doc_id)
        basename=value['source_basename']
        if (doc_id in accepted or ref!=original or pinned!=ref or value['size_bytes']!=ref.size
                or type(value['size_bytes']) is not int or type(value['mtime_ns']) is not int
                or not isinstance(basename,str) or '/' in basename or '\\' in basename
                or not basename.startswith(doc_id+'.') or PurePosixPath(basename).name!=basename):raise ValueError()
        accepted[doc_id]=value
    seen=[]
    for number,part in enumerate(parts,1):
        ids=part['document_ids']
        if (type(part['number']) is not int or part['number']!=number or not isinstance(ids,list) or not ids
                or any(doc_id not in accepted or accepted[doc_id]['part_number']!=number for doc_id in ids)
                or part['source_bytes']!=sum(accepted[doc_id]['size_bytes'] for doc_id in ids)):raise ValueError()
        seen.extend(ids)
    if len(seen)!=len(accepted) or set(seen)!=set(accepted):raise ValueError()


def _public_job(session,settings,job):
    if (job.status!='queued' or job.started_at is not None or job.finished_at is not None
            or job.result not in (None,{}) or job.error or not isinstance(job.params,dict)):
        raise MaintenanceRefused('Started or uncertain public jobs require explicit reconciliation')
    if ((job.approved_by is None)!=(job.approved_at is None)
            or job.approved_by is not None and job.approved_by==job.created_by):
        raise MaintenanceRefused('Recorded approval requires explicit reconciliation')
    if job.job_type in BULK_TYPES:
        from app.services.generation_files import _validate_ids
        ids=job.params.get('doc_ids')
        if not isinstance(ids,list) or not ids:raise ValueError()
        for doc_id in ids:_validate_ids(doc_id,'0'*32)
        return 'bulk.v1',WorkSpec('bulk',1,{'job_id':job.id},f'bulk/{job.id}')
    if job.job_type=='bulk_export':
        _export(session,settings,job)
        return 'export.v1',WorkSpec('export',1,{'job_id':job.id},f'export/{job.id}')
    if job.job_type in {'reference_translation','glossary_translation'}:
        params=job.params
        if any(not isinstance(params.get(key),str) or not 1<=len(params[key])<=255 for key in ('user_id','username')):raise ValueError()
        if job.job_type=='reference_translation':
            from app.services.glossary.normalization import validate_locale
            from app.services.reference_translation import CONFIG
            validate_locale(params['locale'],allow_und=False)
            entities=params['entities']
            if (not isinstance(entities,list) or not 1<=len(entities)<=3 or len(set(entities))!=len(entities)
                    or any(entity not in CONFIG for entity in entities)):raise ValueError()
        else:
            from app.models.glossary import GlossaryTranslationBackfillRequest
            GlossaryTranslationBackfillRequest(locale=params['locale'],term_ids=params['term_ids'],
                expected_translation_versions=params['expected_translation_versions'])
        return job.job_type+'.v1',WorkSpec(job.job_type,1,{'job_id':job.id,'input_hash':canonical_hash(params)},
            f'translation/{job.id}/compact-v1')
    raise MaintenanceRefused('Unknown queued public job recipe')


def _document(session,settings,document,source):
    from app.services.work.admission import document_request_key
    document,ref=_original(session,settings,document.id)
    staging=session.get(DocumentStaging,document.id)
    if staging is None and (document.processed_chunks or document.current_chunk is not None
            or document.status in {'splitting','indexing'}):
        raise MaintenanceRefused('Progress without an accepted checkpoint requires reconciliation')
    state=session.get(DocumentGenerationState,document.id)
    generation_id=staging.generation_id if staging else state.candidate_generation_id if state else None
    generation=session.get(DocumentGeneration,generation_id) if generation_id else None
    if (staging and (staging.checkpoint_version!=2 or not generation or staging.source_file_hash!=ref.sha256)
            or generation and (generation.doc_id!=document.id or generation.phase not in {'preparing','ready','active'}
                or generation.work_id is not None)):
        raise MaintenanceRefused('Accepted document checkpoint owner requires reconciliation')
    review=session.get(DocumentProcessingReview,document.id)
    if review and (review.state in {'draft','awaiting_similar','validating'} or generation is None):
        raise MaintenanceRefused('No immutable accepted document policy is available')
    if generation is None and state and state.active_generation_id:
        raise MaintenanceRefused('Accepted update policy requires explicit reconciliation')
    scope=deepcopy(generation.processing_scope_json) if generation else None
    _scope(scope,ref.sha256)
    if staging and staging.processing_scope_hash!=(scope or {}).get('selection_hash'):raise ValueError()
    request=generation.scope_request_id if generation else None
    # Continuation is explicit and preserves migrated checkpoints. It does not
    # invent a new full-regenerate request or borrow an unconfirmed draft.
    key=document_request_key(document.id,'resume','compact-handoff/'+(source.sql_sha256 if source else 'validation'))
    return WorkSpec('document',1,{'doc_id':document.id,'action':'resume','source_file_hash':ref.sha256},key,document.id),dict(
        work_id=None,doc_id=document.id,processing_scope_json=scope,scope_request_id=request)


def _scope_job(session,settings,row):
    from app.services.processing_scope_change import SCOPE_CHANGE_ANALYSIS_VERSION, ScopeChangePlan
    value=row.plan_json
    if (row.state!='queued' or row.owner_token is not None or row.lease_expires_at is not None
            or row.candidate_generation_id is not None or not re.fullmatch(r'[0-9a-f]{32}',row.request_id or '')
            or value.get('schema_version')!=1 or value.get('analysis_version')!=SCOPE_CHANGE_ANALYSIS_VERSION):
        raise MaintenanceRefused('Owned or incompatible scope intent requires reconciliation')
    preview=ScopeChangePlan(**value['preview'])
    document,ref=_original(session,settings,row.doc_id)
    state=session.get(DocumentGenerationState,row.doc_id)
    base=session.get(DocumentGeneration,preview.base_generation_id) if preview.base_generation_id else None
    review=session.get(DocumentProcessingReview,row.doc_id)
    if (preview.mode not in {'fast_exclude','reuse_only','incremental_generate'} or preview.mapping_status!='verified'
            or state is None or state.active_generation_id!=preview.base_generation_id or base is None or base.doc_id!=row.doc_id
            or review is None or review.state!='submitted' or review.request_id!=row.request_id
            or review.id!=value['review_id'] or review.revision!=value['revision']
            or review.selection_hash!=value['snapshot']['selection_hash'] or document.status not in DOCUMENT_PENDING):raise ValueError()
    _scope(value['snapshot'],ref.sha256)
    return WorkSpec('scope_change',1,{'doc_id':row.doc_id,'plan_id':row.id,'request_id':row.request_id,
        'source_file_hash':ref.sha256,'input_hash':canonical_hash(value)},f'scope-change/{row.id}/{row.request_id}',row.doc_id)


@dataclass
class Intent:
    entry: HandoffEntry
    spec: WorkSpec
    frozen: dict | None = None


def _collect(session,settings,source=None,*,excluded=(),allow_orphans=True):
    intents=[]
    def add(recipe,row,spec,frozen=None):
        if _backed(session,spec,excluded):return
        if not allow_orphans:raise MaintenanceRefused('Unconverted legacy intent requires a reviewed handoff')
        if len(intents)>=MAX_INTENTS:raise MaintenanceRefused('Legacy handoff exceeds the bounded protocol capacity')
        digest=canonical_hash(dict(row=_row(row),payload=spec.payload,frozen=frozen))
        identity=uuid5(UUID(source.installation_id),f'compact-handoff/v1/{source.sql_sha256}/{recipe}/{row.id}/{digest}').hex
        intents.append(Intent(HandoffEntry(recipe=recipe,source_id=str(row.id),input_sha256=digest,work_id=identity),spec,frozen))

    # Approval is never recomputed from today's thresholds or granted by migration.
    for job in session.scalars(select(Job).where(Job.status.in_(['queued','running','awaiting_approval'])).order_by(Job.id)):
        if job.status=='awaiting_approval':
            for kind in PARENT_KINDS|{'bulk_item'}:
                if _related(session,kind,'job_id',job.id,excluded):raise MaintenanceRefused('Unapproved public work must not execute')
            continue
        recipe,spec=_public_job(session,settings,job)
        add(recipe,job,spec)
    for bundle in session.scalars(select(DiagnosticBundle).where(DiagnosticBundle.status.in_(['queued','building'])).order_by(DiagnosticBundle.id)):
        from app.models.diagnostics import BundleRequest
        if (bundle.status!='queued' or bundle.finished_at is not None or bundle.size_bytes or bundle.sha256
                or bundle.artifact_version!=2 or not isinstance(bundle.request,dict)
                or not bundle.request.get('session_id') and not all(bundle.request.get(key) for key in ('from_utc','to_utc'))):
            raise MaintenanceRefused('Started or incomplete diagnostic intent requires reconciliation')
        UUID(bundle.id);BundleRequest.model_validate(bundle.request)
        if isinstance(bundle.manifest,dict) and 'frozen_bundle' in bundle.manifest:
            from app.services.diagnostics.frozen_bundle import frozen_spec
            add('diagnostic_frozen.v1',bundle,frozen_spec(session,bundle,settings))
        else:add('diagnostic_bundle.v1',bundle,WorkSpec('diagnostic_bundle',1,{'bundle_id':bundle.id},f'diagnostic-bundle/{bundle.id}'))
    scoped=set()
    for row in session.scalars(select(DocumentScopeChange).where(
            DocumentScopeChange.state.not_in(['preview','published','failed','canceled'])).order_by(DocumentScopeChange.id)):
        if row.doc_id in scoped:raise MaintenanceRefused('Ambiguous scope operation requires reconciliation')
        scoped.add(row.doc_id)
        add('scope_change.v1',row,_scope_job(session,settings,row))
    if session.scalar(select(DocumentProcessingReview.doc_id).where(DocumentProcessingReview.state=='validating').limit(1)):
        raise MaintenanceRefused('Scope validation must finish before adoption')
    for document in session.scalars(select(Document).where(Document.status.in_(DOCUMENT_PENDING)).order_by(Document.id)):
        if document.id in scoped:continue
        # Existing durable admission already carries its immutable input.
        rows=_related(session,'document','doc_id',document.id,excluded)
        if rows:
            if len(rows)!=1 or rows[0].doc_id!=document.id or rows[0].status!='queued':raise ValueError()
            row=rows[0];frozen=session.get(DocumentWorkInput,row.id)
            _,ref=_original(session,settings,document.id)
            if (row.payload_json!={'doc_id':document.id,'action':row.payload_json.get('action'),'source_file_hash':ref.sha256}
                    or row.payload_json.get('action') not in {'process','resume','regenerate'} or frozen is None or frozen.doc_id!=document.id):raise ValueError()
            _scope(frozen.processing_scope_json,ref.sha256)
            continue
        spec,frozen=_document(session,settings,document,source)
        add('document_resume.v1',document,spec,frozen)
    return sorted(intents,key=lambda intent:(intent.entry.recipe,intent.entry.source_id))


def validate_pending_intents(session,settings):
    try:_collect(session,settings,allow_orphans=False)
    except MaintenanceRefused:raise
    except Exception:raise MaintenanceRefused('Legacy intent is invalid or requires reconciliation') from None  # noqa: BLE001


def _model(source,intents):
    return CompactHandoff(schema_version=1,installation_id=source.installation_id,source_sql_sha256=source.sql_sha256,
        source_selection_sha256=source.selection_sha256,entries=[intent.entry for intent in intents])


def plan_compact_handoff(session,settings,source):
    from .compact_adoption import _expected, capture_compact_source
    try:
        source=_expected(source)
        if capture_compact_source(session,settings)!=source.model_dump():raise ValueError()
        result=_model(source,_collect(session,settings,source)).model_dump()
        if capture_compact_source(session,settings)!=source.model_dump():raise ValueError()
        return result
    except MaintenanceRefused:raise
    except Exception:raise MaintenanceRefused('Legacy handoff source is invalid or changed') from None  # noqa: BLE001


def compile_handoff(session,settings,source,reviewed,*,retry=False):
    try:
        plan=reviewed_plan(reviewed,source)
        excluded=[entry.work_id for entry in plan.entries] if retry else []
        intents=_collect(session,settings,source,excluded=excluded)
        if _model(source,intents)!=plan:raise ValueError()
        return intents
    except MaintenanceRefused:raise
    except Exception:raise MaintenanceRefused('Reviewed legacy handoff differs from the complete source') from None  # noqa: BLE001


def apply_handoff(session,intents,now):
    for intent in intents:
        if (session.get(WorkItem,intent.entry.work_id) is not None
                or session.scalar(select(WorkItem.id).where(WorkItem.dedupe_key==intent.spec.dedupe_key)) is not None):
            raise MaintenanceRefused('Handoff work identity already belongs to another admission')
        spec=intent.spec
        session.add(WorkItem(id=intent.entry.work_id,kind=spec.kind,payload_version=spec.payload_version,
            payload_json=deepcopy(spec.payload),dedupe_key=spec.dedupe_key,doc_id=spec.doc_id,status='queued',owner_id=None,
            fence=0,maintenance_epoch=0,lease_until=None,cancel_requested=False,attempts=0,available_at=now,created_at=now))
    # These mappers have no ORM relationships. Flush their FK parents before
    # inputs; both flushes still belong to the same adoption transaction.
    session.flush()
    for intent in intents:
        if intent.frozen is not None:
            session.add(DocumentWorkInput(**{**deepcopy(intent.frozen),'work_id':intent.entry.work_id}))
    session.flush()


def verify_handoff(session,intents,now):
    for intent in intents:
        row=session.get(WorkItem,intent.entry.work_id)
        spec=intent.spec
        if (row is None or (row.kind,row.payload_version,row.payload_json,row.dedupe_key,row.doc_id)!=(
                spec.kind,spec.payload_version,spec.payload,spec.dedupe_key,spec.doc_id)
                or row.status!='queued' or row.owner_id is not None or row.lease_until is not None or row.cancel_requested
                or row.fence!=0 or row.maintenance_epoch!=0 or row.attempts!=0 or row.finished_at is not None or row.error_code is not None
                or _utc(row.created_at)!=_utc(now) or _utc(row.available_at)!=_utc(now)):
            raise MaintenanceRefused('Handoff work has changed or already executed')
        frozen=session.get(DocumentWorkInput,row.id)
        expected={**intent.frozen,'work_id':row.id} if intent.frozen is not None else None
        if (_row(frozen) if frozen is not None else None)!=expected:
            raise MaintenanceRefused('Handoff frozen input changed or is missing')
