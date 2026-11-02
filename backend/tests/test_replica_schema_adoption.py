"""An actual rpl04 -> rpl05 fence needs explicit, audited offline adoption."""
import copy
from datetime import datetime, timezone
import json
from uuid import uuid4

from alembic import command
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import (ArtifactStorageControl, AuditLog, AuditOutbox, ChatMessage, ChatRun, ChatSession, Document, MaintenanceState,
    ReplicaCapacityResource, ReplicaCapacitySlot, ReplicaRuntimeIdentity)
from app.services.artifacts.control import descriptor
from app.services.work.maintenance_control import MaintenanceOperator, MaintenanceRefused, assert_writes_allowed
from tests.test_replica_maintenance_migration import old_schema
from tests.test_replica_runtime_identity import configured

PREVIOUS = 'rpl04_runtime_identity'
COUNTS = {'frontend': 2, 'api': 2, 'worker': 2}


@pytest.fixture
def migrated(tmp_path):
    from app.deployment.bootstrap import OperatorSettings
    values, old = configured(tmp_path)
    settings = OperatorSettings(**values)
    old['runtime_fingerprint'] = '2' * 64
    settings.runtime_image_id = 'new-runtime@sha256:' + '9' * 64
    engine, config = old_schema(tmp_path)
    with engine.begin() as conn:
        conn.execute(ArtifactStorageControl.__table__.insert().values(id=1,
            installation_id=old['installation_id'], active_store_id=settings.artifact_store_id,
            active_descriptor=descriptor(settings), readonly_roots=[]))
        conn.execute(text('INSERT INTO replica_runtime_identity VALUES (1,:installation,:fingerprint,:receipt,:revision,CURRENT_TIMESTAMP)'),
            {'installation': old['installation_id'], 'fingerprint': old['runtime_fingerprint'],
                'receipt': old['storage_receipt_digest'], 'revision': PREVIOUS})
        conn.execute(Document.__table__.insert().values(id='preserved', filename='keep.docx', status='done'))
        conn.execute(text("INSERT INTO work_items(id,kind,payload_version,payload_json,dedupe_key) VALUES(:id,'probe',1,'{}','preserved')"),
            {'id': '4' * 32})
        config.attributes['connection'] = conn
        command.stamp(config, PREVIOUS)
        command.upgrade(config, 'head')
    yield engine, settings, old
    engine.dispose()


def adopt(session, settings, old, operation, **kwargs):
    from app.deployment import runtime_upgrade
    assert hasattr(runtime_upgrade, 'adopt_schema_upgrade'), 'Migrated schema-fence adoption is missing'
    from app.services.work.maintenance_control import REQUIRED_WRITERS
    return runtime_upgrade.adopt_schema_upgrade(session, settings, old, operation_id=operation,
        previous_revision=kwargs.pop('previous_revision', PREVIOUS),
        intended_counts=kwargs.pop('intended_counts', dict(COUNTS)),
        writer_receipts=kwargs.pop('writer_receipts', {name: 'stopped' for name in REQUIRED_WRITERS}), **kwargs)


def test_schema_upgrade_adoption_preserves_corpus_and_queue_and_stays_held(migrated):
    engine, settings, old = migrated
    operation = uuid4().hex
    with engine.connect() as conn:
        before_docs = conn.execute(text('SELECT * FROM documents')).all()
        before_work = conn.execute(text('SELECT * FROM work_items')).all()
    with MaintenanceOperator(engine, operation_id=operation, owner_id='schema-test', settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            document = adopt(session, settings, old, operation)
        with op.transaction() as session:
            assert adopt(session, settings, old, operation) == document
            state = session.get(MaintenanceState, 1)
            assert state.enabled and state.epoch == 2
            assert state.operation_id == operation and state.owner_id == 'schema-test'
            assert state.intended_counts_json == COUNTS
            assert session.get(ArtifactStorageControl, 1).maintenance_token == operation
            assert session.get(ReplicaRuntimeIdentity, 1).fingerprint == document['runtime_fingerprint']
            assert session.scalar(select(text('count(*)')).select_from(AuditLog)) == 2
    assert document['installation_id'] == old['installation_id']
    assert document['runtime_fingerprint'] != old['runtime_fingerprint']
    with engine.connect() as conn:
        assert conn.execute(text('SELECT * FROM documents')).all() == before_docs
        assert conn.execute(text('SELECT * FROM work_items')).all() == before_work
    with Session(engine) as session, pytest.raises(MaintenanceRefused, match='active'):
        assert_writes_allowed(session, 2, require_installed=True)


@pytest.mark.parametrize('fault', ['epoch', 'owner', 'operation', 'released', 'control', 'schema',
    'fingerprint', 'installation', 'descriptor', 'running_work', 'owned_slot'])
def test_schema_adoption_refuses_uncertain_or_foreign_migrated_state(migrated, fault):
    engine, settings, old = migrated
    with engine.begin() as conn:
        if fault in {'epoch', 'owner', 'operation', 'released'}:
            column, value = {'epoch': ('epoch', 4), 'owner': ('owner_id', 'another'),
                'operation': ('operation_id', '9' * 32), 'released': ('enabled', False)}[fault]
            conn.execute(MaintenanceState.__table__.update().values(**{column: value}))
        elif fault == 'control':
            conn.execute(ArtifactStorageControl.__table__.update().values(maintenance_token='9' * 32))
        elif fault == 'schema':
            conn.execute(text("UPDATE alembic_version SET version_num='rpl04_runtime_identity'"))
        elif fault in {'fingerprint', 'installation'}:
            old = {**old, ('runtime_fingerprint' if fault == 'fingerprint' else 'installation_id'): '9' * (64 if fault == 'fingerprint' else 32)}
        elif fault == 'descriptor':
            settings.artifact_s3_bucket = 'foreign'
        elif fault == 'running_work':
            conn.execute(text("UPDATE work_items SET status='running',owner_id='unknown'"))
        else:
            conn.execute(ReplicaCapacityResource.__table__.insert().values(resource='llm', capacity_limit=1))
            conn.execute(ReplicaCapacitySlot.__table__.insert().values(id=uuid4().hex,
                resource='llm', slot=0, owner_id='unknown', fence=1, release_required=True))
        before = tuple(conn.execute(text('SELECT * FROM replica_maintenance_state')).one())
        fingerprint = conn.scalar(text('SELECT fingerprint FROM replica_runtime_identity'))
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='schema-test', settings=settings, allow_sqlite=True) as op:
        with pytest.raises(ValueError), op.transaction() as session:
            adopt(session, settings, old, operation)
    with engine.connect() as conn:
        assert tuple(conn.execute(text('SELECT * FROM replica_maintenance_state')).one()) == before
        assert conn.scalar(text('SELECT fingerprint FROM replica_runtime_identity')) == fingerprint
        assert conn.scalar(text('SELECT count(*) FROM audit_log')) == 0


@pytest.mark.parametrize('arguments', [
    {'previous_revision': 'unknown'}, {'intended_counts': {}}, {'intended_counts': {**COUNTS, 'api': True}},
    {'writer_receipts': {}}, {'writer_receipts': {'api': 'assumed'}},
])
def test_schema_adoption_requires_exact_transition_and_explicit_writer_receipts(migrated, arguments):
    engine, settings, old = migrated
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='schema-test', settings=settings, allow_sqlite=True) as op:
        with pytest.raises(ValueError), op.transaction() as session:
            adopt(session, settings, old, operation, **arguments)


def test_schema_adoption_requires_owned_operator(migrated):
    engine, settings, old = migrated
    with Session(engine) as session, pytest.raises(ValueError):
        adopt(session, settings, old, uuid4().hex)


def test_schema_adoption_audit_failure_rolls_back_marker_and_identity(migrated, monkeypatch):
    engine, settings, old = migrated
    def fail(*_args, **_kwargs):
        raise RuntimeError('audit unavailable')
    monkeypatch.setattr('app.services.audit.record_in_session', fail)
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='schema-test', settings=settings, allow_sqlite=True) as op:
        with pytest.raises(RuntimeError, match='audit unavailable'), op.transaction() as session:
            adopt(session, settings, old, operation)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT operation_id FROM replica_maintenance_state')) == '0' * 32
        assert conn.scalar(text('SELECT fingerprint FROM replica_runtime_identity')) == old['runtime_fingerprint']


@pytest.mark.parametrize('fault', ['source', 'counts', 'receipts', 'operation'])
def test_replay_cannot_adopt_different_source_or_proof(migrated, fault):
    engine, settings, old = migrated
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='schema-test', settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            adopt(session, settings, old, operation)
        arguments = {}
        if fault == 'source':
            old = {**old, 'runtime_fingerprint': '9' * 64}
        elif fault == 'counts':
            arguments['intended_counts'] = {**COUNTS, 'worker': 1}
        elif fault == 'receipts':
            from app.services.work.maintenance_control import REQUIRED_WRITERS
            arguments['writer_receipts'] = {name: 'drained' for name in REQUIRED_WRITERS}
        else:
            operation = '7' * 32
        with pytest.raises(ValueError), op.transaction() as session:
            adopt(session, settings, old, operation, **arguments)


def test_schema_adoption_source_document_is_not_mutated(migrated):
    engine, settings, old = migrated
    before = copy.deepcopy(old)
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='schema-test', settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            document = adopt(session, settings, old, operation)
    assert old == before and 'CANARY_PG_PASSWORD' not in json.dumps(document)


@pytest.mark.parametrize('failure', ['ownerless_uncertain_slot', 'running_chat'])
def test_schema_adoption_refuses_other_durable_provider_uncertainty(migrated, failure):
    engine, settings, old = migrated
    with engine.begin() as conn:
        if failure == 'ownerless_uncertain_slot':
            conn.execute(ReplicaCapacityResource.__table__.insert().values(resource='llm', capacity_limit=1))
            conn.execute(ReplicaCapacitySlot.__table__.insert().values(id=uuid4().hex,
                resource='llm', slot=0, owner_id=None, fence=1, release_required=True))
        else:
            now, chat_id = datetime.now(timezone.utc), str(uuid4())
            conn.execute(ChatSession.__table__.insert().values(id=chat_id, user_id='prior-user'))
            message = conn.execute(ChatMessage.__table__.insert().values(session_id=chat_id, role='assistant')).inserted_primary_key[0]
            conn.execute(ChatRun.__table__.insert().values(id=uuid4().hex, actor_id='prior-user',
                attempt_id=str(uuid4()), request_hash='3' * 64, session_id=chat_id, message_id=message,
                owner_id='unknown', fence=1, lease_until=now, status='running', capacity_id=uuid4().hex,
                capacity_fence=1, created_at=now))
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='schema-test', settings=settings, allow_sqlite=True) as op:
        with pytest.raises(ValueError), op.transaction() as session:
            adopt(session, settings, old, operation)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT operation_id FROM replica_maintenance_state')) == '0' * 32


@pytest.mark.parametrize('db_enabled', [True, False])
def test_schema_adoption_replay_uses_single_event_from_db_and_or_outbox(migrated, db_enabled):
    engine, settings, old = migrated
    settings.audit_db_enabled, settings.audit_stdout_mode = db_enabled, 'durable'
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='schema-test', settings=settings, allow_sqlite=True) as op:
        with op.transaction() as session:
            expected = adopt(session, settings, old, operation)
        with op.transaction() as session:
            assert adopt(session, settings, old, operation) == expected
            assert session.scalar(select(text('count(*)')).select_from(AuditOutbox)) == 2
            assert session.scalar(select(text('count(*)')).select_from(AuditLog)) == (2 if db_enabled else 0)


def test_schema_adoption_refuses_unknown_storage_migration_history(migrated):
    engine, settings, old = migrated
    with engine.begin() as conn:
        conn.execute(ArtifactStorageControl.__table__.update().values(migration_id='8' * 32))
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='schema-test', settings=settings, allow_sqlite=True) as op:
        with pytest.raises(ValueError), op.transaction() as session:
            adopt(session, settings, old, operation)
