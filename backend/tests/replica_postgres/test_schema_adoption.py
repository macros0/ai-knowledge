"""Real PostgreSQL whole-chain populated schema update, without create_all/stamp."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.models import (ArtifactStorageControl, AuditLog, AuthSession, Document,
    MaintenanceState, ReplicaCapacityResource, ReplicaCapacitySlot)
from app.db.session import get_engine
from app.deployment.runtime_identity import SCHEMA_REVISION, identity_document
from app.services.artifacts.control import descriptor
from app.services.work.maintenance_control import (MaintenanceOperator, MaintenanceRefused,
    assert_writes_allowed, leave_maintenance)
from tests.support.replica_postgres import disposable_postgres
from tests.test_replica_bootstrap import bootstrap_settings as bootstrap_settings
from tests.test_replica_maintenance import stopped

PREVIOUS = 'rpl04_runtime_identity'
COUNTS = {'frontend': 2, 'api': 2, 'worker': 2}


@pytest.fixture
def predecessor(bootstrap_settings):
    with disposable_postgres():
        engine = get_engine()
        with engine.begin() as conn:
            schema = conn.scalar(text('SELECT current_schema()'))
            assert re.fullmatch(r'artifact_[0-9a-f]{32}', schema)
            conn.execute(DropSchema(schema, cascade=True))
            conn.execute(CreateSchema(schema))
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / 'alembic.ini'))
        config.set_main_option('script_location', str(root / 'alembic'))
        settings = bootstrap_settings
        old = identity_document(settings, installation_id='1' * 32, storage_receipt_digest='sha256:' + '2' * 64)
        old['runtime_fingerprint'] = '2' * 64
        settings.runtime_image_id = 'schema-upgrade@sha256:' + '9' * 64
        with engine.begin() as conn:
            config.attributes['connection'] = conn
            command.upgrade(config, PREVIOUS)
            assert 'replica_maintenance_state' not in inspect(conn).get_table_names()
            assert 'maintenance_epoch' not in {col['name'] for col in inspect(conn).get_columns('work_items')}
            conn.execute(ArtifactStorageControl.__table__.update().values(installation_id='1' * 32,
                active_store_id=settings.artifact_store_id, active_descriptor=descriptor(settings)))
            conn.execute(text('INSERT INTO replica_runtime_identity VALUES (1,:installation,:fingerprint,:receipt,:revision,CURRENT_TIMESTAMP)'),
                {'installation': old['installation_id'], 'fingerprint': old['runtime_fingerprint'],
                    'receipt': old['storage_receipt_digest'], 'revision': PREVIOUS})
            conn.execute(Document.__table__.insert().values(id='preserved', filename='keep.docx', status='done'))
            conn.execute(text("INSERT INTO work_items(id,kind,payload_version,payload_json,dedupe_key) VALUES(:id,'probe',1,'{}','preserved')"),
                {'id': '4' * 32})
            now = datetime.now(timezone.utc)
            conn.execute(AuthSession.__table__.insert().values(id='retained-session', external_id='retained-user',
                identity={}, created_at=now, expires_at=now + timedelta(days=1)))
        yield engine, config, settings, old


def migrate(engine, config):
    with engine.begin() as conn:
        config.attributes['connection'] = conn
        command.upgrade(config, 'head')
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
        assert conn.execute(text('SELECT status,maintenance_epoch FROM work_items')).one() == ('queued', 0)


def test_pg_populated_real_schema_upgrade_adopt_release_preserves_data(predecessor):
    from app.deployment.runtime_upgrade import adopt_schema_upgrade
    engine, config, settings, old = predecessor
    with engine.connect() as conn:
        documents = conn.execute(text('SELECT * FROM documents')).all()
    migrate(engine, config)
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='pg-schema-test', settings=settings) as op:
        with op.transaction() as session:
            document = adopt_schema_upgrade(session, settings, old, operation_id=operation,
                previous_revision=PREVIOUS, intended_counts=COUNTS, writer_receipts=stopped())
        with op.transaction() as session:
            assert adopt_schema_upgrade(session, settings, old, operation_id=operation,
                previous_revision=PREVIOUS, intended_counts=COUNTS, writer_receipts=stopped()) == document
            assert session.get(MaintenanceState, 1).enabled is True
            assert session.scalar(select(AuthSession.id)) == 'retained-session'
            assert session.scalar(select(text('count(*)')).select_from(AuditLog)) == 2
        with Session(engine) as session, pytest.raises(MaintenanceRefused, match='active'):
            assert_writes_allowed(session, 2, require_installed=True)
        with op.transaction() as session:
            assert leave_maintenance(session, operation, writer_receipts=stopped()) == 2
    with Session(engine) as session:
        assert assert_writes_allowed(session, 2, require_installed=True) == 2
    with Session(engine) as session, pytest.raises(MaintenanceRefused, match='retired'):
        assert_writes_allowed(session, 0, require_installed=True)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT * FROM documents')).all() == documents
        assert conn.execute(text('SELECT status,maintenance_epoch,attempts FROM work_items')).one() == ('queued', 0, 0)
    with pytest.raises(RuntimeError, match='recovery'), engine.begin() as conn:
        config.attributes['connection'] = conn
        command.downgrade(config, PREVIOUS)


@pytest.mark.parametrize('failure', ['audit', 'unknown_provider'])
def test_pg_schema_adoption_failure_keeps_migration_fence(predecessor, monkeypatch, failure):
    from app.deployment.runtime_upgrade import adopt_schema_upgrade
    engine, config, settings, old = predecessor
    migrate(engine, config)
    if failure == 'audit':
        def unavailable(*_args, **_kwargs):
            raise RuntimeError('audit unavailable')
        monkeypatch.setattr('app.services.audit.record_in_session', unavailable)
    else:
        with engine.begin() as conn:
            conn.execute(ReplicaCapacityResource.__table__.insert().values(resource='llm', capacity_limit=1))
            conn.execute(ReplicaCapacitySlot.__table__.insert().values(id=uuid4().hex,
                resource='llm', slot=0, owner_id='uncertain', fence=1, release_required=True))
    operation = uuid4().hex
    with MaintenanceOperator(engine, operation_id=operation, owner_id='pg-schema-test', settings=settings) as op:
        with pytest.raises((RuntimeError, MaintenanceRefused)), op.transaction() as session:
            adopt_schema_upgrade(session, settings, old, operation_id=operation,
                previous_revision=PREVIOUS, intended_counts=COUNTS, writer_receipts=stopped())
    with engine.connect() as conn:
        assert conn.execute(text('SELECT epoch,operation_id,enabled FROM replica_maintenance_state')).one() == (1, '0' * 32, True)
        assert conn.scalar(text('SELECT fingerprint FROM replica_runtime_identity')) == old['runtime_fingerprint']
        assert conn.scalar(text('SELECT count(*) FROM audit_log')) == 0


def test_pg_conflicting_storage_operation_rolls_back_real_schema_ddl(predecessor):
    engine, config, _settings, _old = predecessor
    with engine.begin() as conn:
        conn.execute(ArtifactStorageControl.__table__.update().values(maintenance_token='7' * 32))
    with pytest.raises(RuntimeError, match='Storage operation'), engine.begin() as conn:
        config.attributes['connection'] = conn
        command.upgrade(config, 'head')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == PREVIOUS
        assert 'replica_maintenance_state' not in inspect(conn).get_table_names()
        assert 'maintenance_epoch' not in {col['name'] for col in inspect(conn).get_columns('work_items')}
        assert conn.scalar(text('SELECT count(*) FROM documents')) == 1
