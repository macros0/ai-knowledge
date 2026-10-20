"""Migration must preserve delivery identity and functional history before switching sinks."""
import json
from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import create_engine, inspect, select, text
from app.db.models import AuditLog
from app.services.audit_hold import hold_pending, validate_outbox_contract
from app.services.audit_schema import upgrade_audit_schema, validate_audit_schema

V1_DDL = """CREATE TABLE audit_outbox (
 audit_id INTEGER NOT NULL PRIMARY KEY, payload TEXT NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'pending', attempt_count INTEGER NOT NULL DEFAULT 0,
 next_attempt_at DATETIME NOT NULL, last_error_code VARCHAR(32), stdout_written_at DATETIME,
 CONSTRAINT ck_audit_outbox_status CHECK (status IN ('pending','held','stdout_written')),
 CONSTRAINT ck_audit_outbox_attempt_count CHECK (attempt_count >= 0),
 FOREIGN KEY(audit_id) REFERENCES audit_log(id))"""


def v1(connection):
    AuditLog.__table__.create(connection)
    connection.execute(text(V1_DDL))
    connection.execute(text('CREATE INDEX ix_audit_outbox_eligible ON audit_outbox(status,next_attempt_at,audit_id)'))


def seed(connection, identity=41, status='pending', event_id=None, payload=None):
    event_id = event_id or str(UUID(int=identity))
    created = datetime(2026, 10, 20, 1, 2, 3, tzinfo=timezone.utc)
    payload = payload or json.dumps({'schema_version': 1, 'event_id': event_id,
                                    'timestamp': created.isoformat(), 'actor': {'name': 'Иван'}}, ensure_ascii=False)
    connection.execute(AuditLog.__table__.insert().values(id=identity, created_at=created,
        event_id=event_id, action_type='document_upload'))
    connection.execute(text('INSERT INTO audit_outbox VALUES (:id,:payload,:status,3,:next,:error,:written)'),
        {'id': identity, 'payload': payload, 'status': status, 'next': '2026-10-22 03:04:05',
         'error': 'restored_backup' if status == 'held' else None,
         'written': '2026-10-21 00:01:02' if status == 'stdout_written' else None})
    return payload


def test_v1_upgrade_preserves_exact_bytes_states_and_independent_insert(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'schema-v2.db'}")
    with engine.begin() as c:
        v1(c)
        for identity, status in enumerate(('pending', 'held', 'stdout_written'), 41):
            seed(c, identity, status)
        before = c.execute(text('SELECT * FROM audit_outbox ORDER BY audit_id')).all()
        upgrade_audit_schema(c)
        assert 'audit_id' not in {col['name'] for col in inspect(c).get_columns('audit_outbox')}
        assert not inspect(c).get_foreign_keys('audit_outbox')
        assert validate_outbox_contract(c) == 2
        validate_audit_schema(c)
        after = c.execute(text('SELECT id,payload,status,attempt_count,next_attempt_at,last_error_code,stdout_written_at FROM audit_outbox ORDER BY id')).all()
        assert after == before
        assert c.scalar(text('SELECT event_id FROM audit_outbox WHERE id=41')) == str(UUID(int=41))
        assert datetime.fromisoformat(c.scalar(text('SELECT created_at FROM audit_outbox WHERE id=41'))) == datetime(2026,10,20,1,2,3)
        upgrade_audit_schema(c)
        assert c.execute(text('SELECT id,payload,status,attempt_count,next_attempt_at,last_error_code,stdout_written_at FROM audit_outbox ORDER BY id')).all() == before
        from app.db.models import AuditOutbox
        c.execute(AuditOutbox.__table__.insert().values(event_id=str(UUID(int=999)), created_at=datetime.now(timezone.utc),payload='{}'))
        assert c.scalar(text('SELECT max(id) FROM audit_outbox')) == 44
    assert hold_pending(engine) == 2


def test_v2_hold_needs_no_audit_log(tmp_path):
    from app.db.models import AuditOutbox
    engine = create_engine(f"sqlite:///{tmp_path / 'standalone.db'}")
    with engine.begin() as c:
        AuditOutbox.__table__.create(c)
        c.execute(AuditOutbox.__table__.insert().values(event_id=str(UUID(int=1)), created_at=datetime.now(timezone.utc),payload='untouched'))
        assert validate_outbox_contract(c) == 2
    assert hold_pending(engine) == 1
    with engine.connect() as c:
        assert c.execute(text('SELECT payload,status FROM audit_outbox')).one() == ('untouched', 'held')


@pytest.mark.parametrize('bad', ['event_id', 'timestamp', 'json'])
def test_invalid_v1_identity_fails_before_any_mutation(tmp_path, bad):
    engine = create_engine(f"sqlite:///{tmp_path / 'invalid.db'}")
    with engine.begin() as c:
        v1(c)
        payload = seed(c)
        if bad == 'json': payload = '{invalid'
        else:
            body = json.loads(payload)
            body['event_id' if bad == 'event_id' else 'timestamp'] = str(UUID(int=999)) if bad != 'timestamp' else '2020-01-01T00:00:00+00:00'
            payload = json.dumps(body)
        c.execute(text('UPDATE audit_outbox SET payload=:payload'), {'payload': payload})
        before = c.execute(text('SELECT * FROM audit_outbox')).all()
        with pytest.raises(ValueError): upgrade_audit_schema(c)
        assert c.execute(text('SELECT * FROM audit_outbox')).all() == before
        assert 'stopword_import_snapshots' not in inspect(c).get_table_names()
        assert 'audit_id' in {col['name'] for col in inspect(c).get_columns('audit_outbox')}


def test_backfill_preserves_snapshot_ids_and_latest_receipt(tmp_path):
    from app.db.models import StopwordImportSnapshot, GlossaryMigrationReceipt
    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    with engine.begin() as c:
        v1(c)
        for identity, old, new in ((55,['а'],['б']), (71,[],['в'])):
            c.execute(AuditLog.__table__.insert().values(id=identity,created_at=datetime(2026,10,20),
                username='admin',action_type='stopwords_import',target_type='locale',target_id='ru',
                old_value={'kind':'bm25','words':old},new_value={'kind':'bm25','words':new},meta={'mode':'replace'}))
        for identity, result in ((72,'1'*64),(73,'2'*64)):
            c.execute(AuditLog.__table__.insert().values(id=identity,created_at=datetime(2026,10,20),
                action_type='glossary_identity_migration',target_id='a'*64,
                meta={'plan_checksum':'a'*64,'result_checksum':result}))
        upgrade_audit_schema(c)
        rows=c.execute(StopwordImportSnapshot.__table__.select().order_by(StopwordImportSnapshot.id)).mappings().all()
        assert [(r['id'], r['locale'], r['old_words'], r['new_words'], r['kind']) for r in rows] == [(55,'ru',['а'],['б'],'bm25'), (71,'ru',[],['в'],'bm25')]
        assert rows[0]['import_meta'] == {'mode':'replace'}
        assert c.scalar(GlossaryMigrationReceipt.__table__.select().with_only_columns(GlossaryMigrationReceipt.result_checksum)) == '2'*64
        c.execute(StopwordImportSnapshot.__table__.insert().values(created_at=datetime.now(timezone.utc),locale='ru',kind='bm25',old_words=[],new_words=[]))
        assert c.scalar(text('SELECT max(id) FROM stopword_import_snapshots')) == 72
        upgrade_audit_schema(c)
        assert c.scalar(text('SELECT count(*) FROM stopword_import_snapshots')) == 3


def test_incomplete_latest_receipt_rejected_before_legacy_alter(tmp_path):
    from tests.test_audit_schema import LEGACY
    engine = create_engine(f"sqlite:///{tmp_path / 'bad-receipt.db'}")
    with engine.begin() as c:
        c.execute(text(LEGACY))
        c.execute(text("INSERT INTO audit_log (id,created_at,action_type,target_id,meta) VALUES (1,'2026-10-20','glossary_identity_migration',:checksum,:meta)"), {'checksum':'a'*64,'meta':json.dumps({'result_checksum':'1'*64})})
        c.execute(text("INSERT INTO audit_log (id,created_at,action_type,target_id,meta) VALUES (2,'2026-10-20','glossary_identity_migration',:checksum,'{}')"), {'checksum':'a'*64})
        with pytest.raises(ValueError): upgrade_audit_schema(c)
        assert 'event_id' not in {col['name'] for col in inspect(c).get_columns('audit_log')}
        assert 'audit_outbox' not in inspect(c).get_table_names()


@pytest.mark.parametrize('version', [1, 2])
def test_restore_hold_recognizes_both_versions_without_replaying_written(tmp_path, version):
    engine = create_engine(f"sqlite:///{tmp_path / 'hold.db'}")
    with engine.begin() as c:
        v1(c)
        for identity, status in enumerate(('pending', 'held', 'stdout_written'), 41):
            seed(c, identity, status)
        if version == 2:
            upgrade_audit_schema(c)
        assert validate_outbox_contract(c) == version
        before = c.execute(text('SELECT payload,attempt_count,next_attempt_at,stdout_written_at FROM audit_outbox ORDER BY 1')).all()
    assert hold_pending(engine) == 1
    assert hold_pending(engine) == 0
    with engine.connect() as c:
        assert c.execute(text('SELECT payload,attempt_count,next_attempt_at,stdout_written_at FROM audit_outbox ORDER BY 1')).all() == before
        assert list(c.scalars(text('SELECT status FROM audit_outbox ORDER BY 1'))) == ['held', 'held', 'stdout_written']


@pytest.mark.parametrize('drift', ['extra_column', 'missing_event_unique', 'partial_event_unique', 'unique_delivery_index', 'extra_unique_constraint', 'extra_unique_index'])
def test_v2_unknown_drift_refuses_hold_and_upgrade_unchanged(tmp_path, drift):
    from app.db.models import AuditOutbox
    from sqlalchemy.schema import CreateTable
    engine = create_engine(f"sqlite:///{tmp_path / 'drift.db'}")
    with engine.begin() as c:
        AuditLog.__table__.create(c)
        ddl = str(CreateTable(AuditOutbox.__table__).compile(c))
        if drift in {'missing_event_unique', 'partial_event_unique'}:
            ddl = ddl.replace(', \n\tUNIQUE (event_id)', '')
        if drift == 'extra_unique_constraint':
            ddl = ddl.replace('PRIMARY KEY (id),', 'PRIMARY KEY (id), UNIQUE (status),')
        c.execute(text(ddl))
        if drift == 'extra_unique_index':
            c.execute(text('CREATE UNIQUE INDEX extra_status ON audit_outbox(status)'))
        c.execute(text(('CREATE UNIQUE INDEX' if drift == 'unique_delivery_index' else 'CREATE INDEX') +
                       ' ix_audit_outbox_eligible ON audit_outbox(status,next_attempt_at,id)'))
        if drift == 'extra_column':
            c.execute(text('ALTER TABLE audit_outbox ADD COLUMN surprise INTEGER'))
        if drift == 'partial_event_unique':
            c.execute(text("CREATE UNIQUE INDEX partial_id ON audit_outbox(event_id) WHERE status='pending'"))
        c.execute(text("INSERT INTO audit_outbox (id,event_id,created_at,payload,status,attempt_count,next_attempt_at) VALUES (1,:event,'2026-10-20','secret','pending',0,'2026-10-20')"), {'event':str(UUID(int=1))})
        before = c.execute(text('SELECT * FROM audit_outbox')).all()
        with pytest.raises(ValueError): upgrade_audit_schema(c)
        assert c.execute(text('SELECT * FROM audit_outbox')).all() == before
    with pytest.raises(ValueError): hold_pending(engine)
    with engine.connect() as c:
        assert c.scalar(text('SELECT status FROM audit_outbox')) == 'pending'


def test_sqlite_ddl_and_backfill_rollback_together_on_late_failure(tmp_path, monkeypatch):
    import app.services.audit_schema as schema
    engine = create_engine(f"sqlite:///{tmp_path / 'rollback.db'}")
    with engine.begin() as c:
        v1(c)
        payload = seed(c)
    with pytest.raises(RuntimeError):
        with engine.begin() as c:
            monkeypatch.setattr(schema, '_backfill_domain', lambda *args: (_ for _ in ()).throw(RuntimeError('late injected failure')))
            upgrade_audit_schema(c)
    with engine.connect() as c:
        assert 'audit_id' in {col['name'] for col in inspect(c).get_columns('audit_outbox')}
        assert 'audit_outbox_v1_upgrade' not in inspect(c).get_table_names()
        assert c.execute(text('SELECT payload,status,attempt_count FROM audit_outbox')).one() == (payload, 'pending', 3)
        assert validate_outbox_contract(c) == 1


def test_frozen_066_ignores_future_runtime_model_changes(tmp_path, monkeypatch):
    from app.db.models import AuditOutbox
    from app.services.audit_schema_v1 import upgrade_audit_schema as upgrade_frozen
    from app.services.audit_schema_v1 import validate_audit_schema as validate_frozen
    from tests.test_audit_schema import LEGACY
    engine = create_engine(f"sqlite:///{tmp_path / 'frozen.db'}")
    # Swap the runtime model's table entirely: 066 must not consult it.
    monkeypatch.setattr(AuditOutbox, '__table__', None)
    with engine.begin() as c:
        c.execute(text(LEGACY))
        upgrade_frozen(c)
        validate_frozen(c)
        assert validate_outbox_contract(c) == 1
        assert 'audit_id' in {col['name'] for col in inspect(c).get_columns('audit_outbox')}


@pytest.fixture
def postgres_schema_engine():
    import os
    from uuid import uuid4
    from sqlalchemy.engine import make_url
    url = os.environ.get('AUDIT_TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Disposable PostgreSQL not provided')
    parsed = make_url(url)
    assert parsed.get_backend_name() == 'postgresql' and parsed.database.startswith('audit_test_')
    schema = 'schema_migration_' + uuid4().hex
    admin = create_engine(parsed)
    with admin.begin() as c:
        c.execute(text('CREATE SCHEMA ' + schema))
    engine = create_engine(parsed.update_query_dict({'options': '-csearch_path=' + schema}))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as c:
            c.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
        admin.dispose()


def test_postgres_v1_upgrade_preserves_bytes_states_history_and_sequences(postgres_schema_engine):
    from app.db.models import AuditOutbox, StopwordImportSnapshot, GlossaryMigrationReceipt
    from app.services.audit_schema_v1_models import AuditLog as FrozenLog, AuditOutbox as FrozenOutbox
    engine = postgres_schema_engine
    with engine.begin() as c:
        FrozenLog.__table__.create(c)
        FrozenOutbox.__table__.create(c)
        for identity, status in enumerate(('pending', 'held', 'stdout_written'), 41):
            seed(c, identity, status)
        c.execute(AuditLog.__table__.insert().values(id=80, created_at=datetime(2026,10,20),
            action_type='stopwords_import', target_id='ru', username='admin',
            old_value={'words':['а'], 'kind':'marker'}, new_value={'words':['б'], 'kind':'marker'}))
        c.execute(AuditLog.__table__.insert().values(id=81, created_at=datetime(2026,10,20),
            action_type='glossary_identity_migration', target_id='a'*64,
            meta={'plan_checksum':'a'*64,'result_checksum':'b'*64}))
        before = c.execute(text('SELECT audit_id,payload,status,attempt_count,next_attempt_at,last_error_code,stdout_written_at FROM audit_outbox ORDER BY audit_id')).all()
        upgrade_audit_schema(c)
        validate_audit_schema(c)
        assert c.execute(text('SELECT id,payload,status,attempt_count,next_attempt_at,last_error_code,stdout_written_at FROM audit_outbox ORDER BY id')).all() == before
        assert c.scalar(select(StopwordImportSnapshot.old_words)) == ['а']
        assert c.scalar(select(GlossaryMigrationReceipt.result_checksum)) == 'b'*64
        queue_id = c.scalar(AuditOutbox.__table__.insert().values(event_id=str(UUID(int=999)),payload='independent',created_at=datetime.now(timezone.utc)).returning(AuditOutbox.id))
        assert queue_id == 44
        snapshot_id = c.scalar(StopwordImportSnapshot.__table__.insert().values(locale='ru',kind='marker',old_words=[],new_words=[]).returning(StopwordImportSnapshot.id))
        assert snapshot_id == 81
        upgrade_audit_schema(c)
    assert hold_pending(engine) == 2


def test_postgres_queue_ddl_rolls_back_on_late_failure(postgres_schema_engine, monkeypatch):
    import app.services.audit_schema as schema
    from app.services.audit_schema_v1_models import AuditLog as FrozenLog, AuditOutbox as FrozenOutbox
    engine = postgres_schema_engine
    with engine.begin() as c:
        FrozenLog.__table__.create(c)
        FrozenOutbox.__table__.create(c)
        payload = seed(c)
    with pytest.raises(RuntimeError):
        with engine.begin() as c:
            monkeypatch.setattr(schema, '_backfill_domain', lambda *args: (_ for _ in ()).throw(RuntimeError('late injected failure')))
            upgrade_audit_schema(c)
    with engine.connect() as c:
        assert validate_outbox_contract(c) == 1
        assert c.scalar(text('SELECT payload FROM audit_outbox')) == payload
        assert 'audit_outbox_v1_upgrade' not in inspect(c).get_table_names()


@pytest.mark.parametrize('missing', ['stopword_import_snapshots', 'glossary_migration_receipts'])
def test_runtime_domain_schema_refuses_missing_table(tmp_path, missing):
    from app.db.models import StopwordImportSnapshot, GlossaryMigrationReceipt
    import app.services.audit_schema as schema
    engine = create_engine(f"sqlite:///{tmp_path / 'runtime-domain.db'}")
    for model in (StopwordImportSnapshot, GlossaryMigrationReceipt):
        if model.__tablename__ != missing:
            model.__table__.create(engine)
    with engine.connect() as c:
        with pytest.raises(ValueError): schema.validate_audit_domain_schema(c)


def test_v2_upgrade_repairs_missing_domain_tables_and_preserves_standalone_event(tmp_path):
    from app.db.models import AuditOutbox
    engine = create_engine(f"sqlite:///{tmp_path / 'v2-domain.db'}")
    with engine.begin() as c:
        AuditLog.__table__.create(c)
        AuditOutbox.__table__.create(c)
        c.execute(AuditOutbox.__table__.insert().values(event_id=str(UUID(int=100)),created_at=datetime.now(timezone.utc),payload='untouched'))
        upgrade_audit_schema(c)
        assert {'stopword_import_snapshots', 'glossary_migration_receipts'} <= set(inspect(c).get_table_names())
        assert c.scalar(select(AuditOutbox.payload)) == 'untouched'



def test_postgres_preexisting_snapshot_sequence_rollback_is_transactional(postgres_schema_engine, monkeypatch):
    import app.services.audit_schema as schema
    from app.db.models import StopwordImportSnapshot, GlossaryMigrationReceipt
    from app.services.audit_schema_v1_models import AuditLog as FrozenLog, AuditOutbox as FrozenOutbox
    engine = postgres_schema_engine
    with engine.begin() as c:
        FrozenLog.__table__.create(c)
        FrozenOutbox.__table__.create(c)
        StopwordImportSnapshot.__table__.create(c)
        GlossaryMigrationReceipt.__table__.create(c)
        first = c.scalar(StopwordImportSnapshot.__table__.insert().values(locale='ru',kind='marker',old_words=[],new_words=[]).returning(StopwordImportSnapshot.id))
        assert first == 1
        c.execute(AuditLog.__table__.insert().values(id=80, created_at=datetime(2026,10,20),
            action_type='stopwords_import', target_id='ru', old_value={'words':[],'kind':'marker'},new_value={'words':['а'],'kind':'marker'}))
    original_backfill = schema._backfill_domain
    def late_failure(*args):
        original_backfill(*args)
        raise RuntimeError('after sequence repair')
    monkeypatch.setattr(schema, '_backfill_domain', late_failure)
    with pytest.raises(RuntimeError):
        with engine.begin() as c:
            upgrade_audit_schema(c)
    with engine.begin() as c:
        assert validate_outbox_contract(c) == 1
        next_id = c.scalar(StopwordImportSnapshot.__table__.insert().values(locale='ru',kind='marker',old_words=[],new_words=[]).returning(StopwordImportSnapshot.id))
        assert next_id == 2
