"""Frozen legacy inputs remain executable after the original disk is offline."""
from dataclasses import replace
from io import BytesIO
import json
from uuid import uuid4
from zipfile import ZipFile

import pytest

from app.auth.models import User
from app.db.models import DiagnosticBundle, WorkItem
from app.db.session import session_scope
from app.services.artifacts.repository import resolve_blob
from app.services.work.maintenance_control import leave_maintenance
from app.services.work.models import LostOwnership, WorkSpec
from tests.test_artifact_legacy_diagnostics import corpus as corpus, legacy as legacy, migrate
from tests.test_replica_compact_adoption import WRITERS, operator, source_snapshot
from tests.test_replica_compact_handoff import apply, plan


def held(legacy,monkeypatch):
    from app.deployment.bootstrap import write_identity
    from app.deployment.runtime_identity import validate_identity
    from app.services.work.runtime import build_handlers
    settings,receipt,_=migrate(legacy)
    if legacy.get('database_url'):settings.database_url=legacy['database_url']
    source=source_snapshot(settings);manifest=plan(settings,source)
    assert manifest['entries'][0]['recipe']=='diagnostic_frozen.v1'
    assert 2 in build_handlers().supported['diagnostic_bundle']
    with operator(settings) as op:
        identity=apply(op,settings,source,receipt,manifest)
        assert apply(op,settings,source,receipt,manifest)==identity
        with op.transaction() as session:leave_maintenance(session,op.operation_id,writer_receipts=WRITERS)
    write_identity(settings.runtime_identity_file,identity)
    settings._runtime_identity=validate_identity(settings);settings._maintenance_epoch=1;settings.app_role='worker'
    monkeypatch.setattr('app.config.get_settings',lambda:settings)
    def forbidden(*_args,**_kwargs):pytest.fail('Frozen worker recollected current diagnostic input')
    monkeypatch.setattr('app.services.diagnostics.bundle_queue.collect_prepared_snapshot',forbidden)
    return settings


def take():
    from app.services.work.store import claim
    with session_scope() as session:
        work=claim(session,owner_id='cold-diagnostic-worker',supported={'diagnostic_bundle':{2}},lease_seconds=120)
    assert work is not None
    return work


def test_frozen_worker_uses_original_cutoff_after_source_disk_is_unavailable(legacy,monkeypatch):
    from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
    from app.services.diagnostics.replica_store import ReplicaDiagnosticStore
    settings=held(legacy,monkeypatch)
    old=legacy['source'].diagnostics_dir
    old.rename(old.with_name('retained-stopped-journal'))
    if legacy.get('frontend_root'):
        legacy['frontend_root'].rename(legacy['frontend_root'].with_name('retained-frontend-journal'))
    with ReplicaDiagnosticStore(settings,instance_id=str(uuid4())) as store:
        work=take();DiagnosticBundleQueue(store).execute_work(work)
        with session_scope() as session:
            assert session.get(WorkItem,work.work_id).status=='succeeded'
            row=session.get(DiagnosticBundle,legacy['bundle_id'])
            assert row.status=='ready' and row.manifest['cutoff_at']==legacy['cutoff'].isoformat()
        reader=DiagnosticBundleQueue(store)
        lease=reader.acquire_download(legacy['bundle_id'],User(user_id='operator',username='Operator',roles=['admin']))
        with ZipFile(BytesIO(b''.join(lease.chunks()))) as archive:
            assert archive.testzip() is None
            events=[json.loads(line) for name in ('events/backend.jsonl','events/frontend.jsonl','events/browser.jsonl')
                for line in archive.read(name).splitlines()]
            assert {value['event_id'] for value in events}==legacy['expected_ids']
            assert legacy['later_id'] not in {value['event_id'] for value in events}


def test_changed_frozen_archive_cannot_be_published_ready(legacy,monkeypatch):
    from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
    from app.services.diagnostics.replica_store import ReplicaDiagnosticStore
    settings=held(legacy,monkeypatch)
    with session_scope() as session:
        ref=resolve_blob(session,owner_type='diagnostic_bundle',owner_id=legacy['bundle_id'],generation_id=None,logical_name='frozen-input.zip')
    # Explicit offline provider corruption; metadata/digests are not rewritten.
    (settings.artifact_root/ref.key).write_bytes(b'corrupted frozen input')
    with ReplicaDiagnosticStore(settings,instance_id=str(uuid4())) as store:
        with pytest.raises((ValueError,OSError)):
            DiagnosticBundleQueue(store).execute_work(take())
        with session_scope() as session:
            assert session.get(DiagnosticBundle,legacy['bundle_id']).status!='ready'
            assert resolve_blob(session,owner_type='diagnostic_bundle',owner_id=legacy['bundle_id'],generation_id=None,logical_name='bundle.zip') is None


def test_legacy_version1_callback_never_recollects_a_frozen_request(legacy,monkeypatch):
    from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
    from app.services.diagnostics.replica_store import ReplicaDiagnosticStore
    settings=held(legacy,monkeypatch);work=take()
    old=replace(work,spec=WorkSpec('diagnostic_bundle',1,{'bundle_id':legacy['bundle_id']},work.spec.dedupe_key))
    with ReplicaDiagnosticStore(settings,instance_id=str(uuid4())) as store:
        with pytest.raises((ValueError,LostOwnership)):DiagnosticBundleQueue(store).execute_work(old)
    with session_scope() as session:assert session.get(DiagnosticBundle,legacy['bundle_id']).status=='queued'


def test_frozen_session_filter_keeps_its_original_semantics(legacy,monkeypatch):
    with session_scope() as session:session.get(DiagnosticBundle,legacy['bundle_id']).request={'session_id':legacy['session_id']}
    legacy['expected_ids']={legacy['captured_id']}
    test_frozen_worker_uses_original_cutoff_after_source_disk_is_unavailable(legacy,monkeypatch)


def test_explicit_frontend_spool_survives_source_off_without_recollection(legacy,monkeypatch):
    from datetime import timedelta
    from tests.test_internal_diagnostics import event
    from app.services.artifacts.migration import StorageMigration
    root=legacy['root']/'frontend-spool';directory=root/'events/baseline';directory.mkdir(parents=True)
    value=event();value['timestamp_utc']=(legacy['cutoff']-timedelta(seconds=1)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    path=directory/('1234567890123_'+str(uuid4())+'.jsonl')
    raw=(json.dumps(dict(reversed(list(value.items()))))+'\n').encode();path.write_bytes(raw)
    legacy['expected_ids'].add(value['event_id']);legacy['frontend_root']=root
    original=StorageMigration.__init__
    def selected(task,*args,**kwargs):original(task,*args,legacy_frontend_root=root,**kwargs)
    monkeypatch.setattr(StorageMigration,'__init__',selected)
    test_frozen_worker_uses_original_cutoff_after_source_disk_is_unavailable(legacy,monkeypatch)
    assert (root.with_name('retained-frontend-journal')/path.relative_to(root)).read_bytes()==raw


def test_late_frozen_worker_cannot_publish_after_replacement_claim(legacy,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime, timedelta, timezone
    from app.services.diagnostics import frozen_bundle
    from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
    from app.services.diagnostics.replica_store import ReplicaDiagnosticStore
    settings=held(legacy,monkeypatch);old=take();replacement=[]
    real_archive=frozen_bundle._archive
    def supersede(*args):
        real_archive(*args)
        def replace_owner():
            with session_scope() as session:
                session.get(WorkItem,old.work_id).lease_until=datetime.now(timezone.utc)-timedelta(seconds=1)
            return take()
        with ThreadPoolExecutor(max_workers=1) as pool:replacement.append(pool.submit(replace_owner).result(timeout=10))
    monkeypatch.setattr(frozen_bundle,'_archive',supersede)
    with ReplicaDiagnosticStore(settings,instance_id=str(uuid4())) as store:
        with pytest.raises(LostOwnership):DiagnosticBundleQueue(store).execute_work(old)
        with session_scope() as session:
            assert session.get(DiagnosticBundle,legacy['bundle_id']).status=='building'
            assert resolve_blob(session,owner_type='diagnostic_bundle',owner_id=legacy['bundle_id'],generation_id=None,logical_name='bundle.zip') is None
        monkeypatch.setattr(frozen_bundle,'_archive',real_archive)
        DiagnosticBundleQueue(store).execute_work(replacement[0])
        with session_scope() as session:
            assert session.get(WorkItem,old.work_id).status=='succeeded'
            assert session.get(DiagnosticBundle,legacy['bundle_id']).status=='ready'
