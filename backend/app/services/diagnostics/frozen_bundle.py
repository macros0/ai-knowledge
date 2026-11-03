"""Version2 completion publishes an S4-frozen ZIP, never current recorder input."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import file_digest
import json
from zipfile import BadZipFile, ZipFile

from app.db.models import DiagnosticBundle
from app.db.session import session_scope
from app.services import audit
from app.services.artifacts.contract import BlobIntegrityError, BlobRef, BlobUnavailable
from app.services.artifacts.job_files import lock_job, pin_file
from app.services.artifacts.publication import canonical_hash
from app.services.artifacts.repository import attach_blob, resolve_blob
from app.services.work.context import coordinator_session, work_execution
from app.services.work.handlers import TransientWorkError
from app.services.work.models import LostOwnership, WorkSpec
from app.services.work.store import _locked, _now, _utc, finish

ENTRIES=('summary.txt','manifest.json','events/backend.jsonl','events/frontend.jsonl','events/browser.jsonl',
    'snapshots/runtime.json','snapshots/operations.json')


def frozen_receipt(session,row,settings):
    try:
        if not isinstance(row.manifest,dict) or set(row.manifest)!={'frozen_bundle'}:raise ValueError()
        value=deepcopy(row.manifest['frozen_bundle'])
        if (not isinstance(value,dict) or set(value)!={'version','ref','cutoff_at','request_sha256','manifest'}
                or type(value['version']) is not int or value['version']!=1 or row.artifact_version!=2
                or value['request_sha256']!=canonical_hash(row.request)
                or _utc(datetime.fromisoformat(value['cutoff_at']))!=_utc(row.cutoff_at)
                or not isinstance(value['manifest'],dict) or value['manifest'].get('format_version')!=2
                or value['manifest'].get('cutoff_at')!=value['cutoff_at']
                or not isinstance(value['manifest'].get('counts'),dict)):
            raise ValueError()
        ref=BlobRef(**value['ref'])
        if ref.media_type!='application/zip' or not 0<ref.size<=settings.diagnostics_limits().bundle_bytes or ref.store_id!=settings.artifact_store_id:
            raise ValueError()
        if resolve_blob(session,owner_type='diagnostic_bundle',owner_id=row.id,generation_id=None,logical_name='frozen-input.zip')!=ref:
            raise ValueError()
        return value
    except (ValueError,TypeError,KeyError,AttributeError):
        raise BlobIntegrityError('Frozen diagnostic input is invalid or changed') from None


def frozen_spec(session,row,settings):
    receipt=frozen_receipt(session,row,settings)
    return WorkSpec('diagnostic_bundle',2,{'bundle_id':row.id,'input_hash':canonical_hash(receipt)},f'diagnostic-bundle/{row.id}')


def _archive(path,receipt,limits):
    try:
        with ZipFile(path) as archive:
            if (tuple(archive.namelist())!=ENTRIES or sum(item.file_size for item in archive.infolist())>limits.total_bytes
                    or any(item.flag_bits&1 for item in archive.infolist())
                    or archive.getinfo('manifest.json').file_size>1024*1024):raise ValueError()
            if json.loads(archive.read('manifest.json'))!=receipt['manifest'] or archive.testzip() is not None:raise ValueError()
    except (BadZipFile,OSError,ValueError,KeyError,UnicodeError):
        raise BlobIntegrityError('Frozen diagnostic archive is invalid or changed') from None


def complete_frozen_bundle(work,settings):
    payload=work.spec.payload
    if (work.spec.kind!='diagnostic_bundle' or work.spec.payload_version!=2 or work.spec.doc_id is not None
            or set(payload)!={'bundle_id','input_hash'}):raise ValueError('Invalid frozen diagnostic work')
    bundle_id=payload['bundle_id'];limits=settings.diagnostics_limits()
    with coordinator_session(work) as session:
        row=lock_job(session,DiagnosticBundle,bundle_id)
        if row is None or row.status not in {'queued','building'}:raise LostOwnership('Diagnostic request is retired')
        receipt=frozen_receipt(session,row,settings)
        if canonical_hash(receipt)!=payload['input_hash']:raise LostOwnership('Frozen diagnostic input changed')
        row.status='building'
    with work_execution(work):
        try:
            ref=BlobRef(**receipt['ref']);stack,path=pin_file(settings,ref)
            try:
                with path.open('rb') as source:
                    if path.stat().st_size!=ref.size or file_digest(source,'sha256').hexdigest()!=ref.sha256:
                        raise BlobIntegrityError('Frozen archive changed')
                _archive(path,receipt,limits)
            finally:stack.close()
            with session_scope() as session:
                row=lock_job(session,DiagnosticBundle,bundle_id)
                if row is None or row.status!='building' or frozen_receipt(session,row,settings)!=receipt:
                    raise LostOwnership('Frozen diagnostic request is retired')
                attach_blob(session,ref=ref,owner_type='diagnostic_bundle',owner_id=bundle_id,generation_id=None,logical_name='bundle.zip')
                row.status='ready';row.finished_at=_now(session)
                row.expires_at=row.finished_at+timedelta(seconds=limits.bundle_seconds)
                row.size_bytes,row.sha256=ref.size,ref.sha256
                row.manifest=deepcopy(receipt['manifest']);row.counts=deepcopy(receipt['manifest']['counts'])
                audit.record_in_session(session,action_type='diagnostic_bundle_ready',user_id='system',username='system',
                    target_type='diagnostic_bundle',target_id=bundle_id,new_value={'size_bytes':ref.size,'sha256':ref.sha256},settings=settings)
                finish(session,work,outcome='succeeded')
        except LostOwnership:raise
        except Exception as exc:  # noqa: BLE001 -- public errors never include ZIP/provider/source values.
            transient=isinstance(exc,BlobUnavailable)
            with session_scope() as session:
                item,now=_locked(session,work)
                if not transient or item.attempts>=settings.work_max_attempts:
                    row=lock_job(session,DiagnosticBundle,bundle_id)
                    if row is not None and row.status=='building':
                        row.status='failed';row.error_code='diagnostic_bundle_failed';row.finished_at=now
                        audit.record_in_session(session,action_type='diagnostic_bundle_failed',user_id='system',username='system',
                            target_type='diagnostic_bundle',target_id=bundle_id,new_value={'error_code':row.error_code},settings=settings)
            if transient:raise TransientWorkError('storage_unavailable') from None
            raise
