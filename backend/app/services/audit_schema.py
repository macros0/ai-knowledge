"""Strict, transactional v1-to-v2 upgrade without re-encoding queued events."""
from datetime import datetime, timezone
import json
import re
from uuid import UUID

from sqlalchemy import MetaData, Table, inspect, select, text

from app.db.models import AuditLog, AuditOutbox, GlossaryMigrationReceipt, StopwordImportSnapshot
from app.services.audit_hold import validate_outbox_contract
from app.services.audit_schema_v1 import FIELDS, _compatible_columns
from app.services.audit_schema_v1 import upgrade_audit_schema as upgrade_v1
from app.services.audit_schema_v1 import validate_audit_schema as validate_v1

_DOMAIN_MODELS = (StopwordImportSnapshot, GlossaryMigrationReceipt)


def _validate_log(connection):
    inspector = inspect(connection)
    if 'audit_log' not in inspector.get_table_names():
        raise ValueError('Audit migration required')
    _compatible_columns(connection, 'audit_log', {c.name: c for c in AuditLog.__table__.c})
    if inspector.get_pk_constraint('audit_log').get('constrained_columns') != ['id']:
        raise ValueError('Unknown audit schema')
    unique = inspector.get_unique_constraints('audit_log') + inspector.get_indexes('audit_log')
    if not any(c.get('column_names') == ['event_id'] and c.get('unique', True)
               and not any(v is not None for k, v in (c.get('dialect_options') or {}).items()
                           if k.endswith('_where')) for c in unique):
        raise ValueError('Audit unique index required')


def validate_audit_schema(connection):
    _validate_log(connection)
    if validate_outbox_contract(connection) != 2:
        raise ValueError('Audit v2 migration required')


def validate_audit_domain_schema(connection):
    """Production startup gate for working history independent of audit sinks."""
    names = inspect(connection).get_table_names()
    if not all(model.__table__.name in names for model in _DOMAIN_MODELS):
        raise ValueError('Audit functional history migration required')
    _validate_domain_tables(connection, [], [])


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError('Invalid audit migration timestamp')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _json_object(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError('Invalid audit migration object')
    return value


def _validate_queue_rows(connection):
    """Read/validate every source before the first schema mutation."""
    metadata = MetaData()
    queue = Table('audit_outbox', metadata, autoload_with=connection)
    log = Table('audit_log', metadata, autoload_with=connection)
    seen = set()
    statement = select(queue, log.c.event_id.label('source_event_id'),
                       log.c.created_at.label('source_created_at')).select_from(
                           queue.outerjoin(log, queue.c.audit_id == log.c.id))
    try:
        for row in connection.execute(statement).mappings():
            body = _json_object(row['payload'])
            event_id = body.get('event_id')
            if (not isinstance(event_id, str) or str(UUID(event_id)) != event_id
                    or event_id != row['source_event_id'] or event_id in seen):
                raise ValueError('Invalid audit migration identity')
            timestamp = body.get('timestamp')
            parsed = datetime.fromisoformat(timestamp)
            if parsed.tzinfo is None or _utc(parsed) != _utc(row['source_created_at']):
                raise ValueError('Invalid audit migration timestamp')
            if (row['status'] not in {'pending', 'held', 'stdout_written'}
                    or type(row['attempt_count']) is not int or row['attempt_count'] < 0):
                raise ValueError('Invalid audit migration state')
            _utc(row['next_attempt_at'])
            if row['stdout_written_at'] is not None:
                _utc(row['stdout_written_at'])
            seen.add(event_id)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('Invalid audit outbox source data; migration unchanged') from None


def _domain_sources(connection):
    metadata = MetaData()
    log = Table('audit_log', metadata, autoload_with=connection)
    snapshots = []
    receipts = {}
    statement = select(log).where(log.c.action_type.in_(
        ['stopwords_import', 'glossary_identity_migration'])).order_by(log.c.id)
    try:
        for row in connection.execute(statement).mappings():
            if row['action_type'] == 'stopwords_import':
                before, after = _json_object(row['old_value']), _json_object(row['new_value'])
                kind, locale = before.get('kind'), row['target_id']
                if (not isinstance(locale, str) or not locale or len(locale) > 16
                        or kind not in {'bm25', 'marker'} or after.get('kind') != kind
                        or not all(isinstance(value, list) and all(isinstance(word, str) for word in value)
                                   for value in (before.get('words'), after.get('words')))):
                    raise ValueError('Invalid stopword snapshot')
                meta = row['meta']
                if meta is not None:
                    meta = _json_object(meta)
                snapshots.append(dict(id=row['id'], created_at=_utc(row['created_at']), username=row['username'],
                                      locale=locale, kind=kind, old_words=before['words'], new_words=after['words'],
                                      import_meta=meta))
            else:
                # Preserve the prior latest-by-id policy, even if that latest row is invalid.
                receipts[row['target_id']] = row
        results = []
        for checksum, row in receipts.items():
            meta = _json_object(row['meta'])
            result = meta.get('result_checksum')
            if (not isinstance(checksum, str) or not re.fullmatch(r'[0-9a-f]{64}', checksum)
                    or not isinstance(result, str) or not re.fullmatch(r'[0-9a-f]{64}', result)
                    or meta.get('plan_checksum', checksum) != checksum):
                raise ValueError('Invalid glossary receipt')
            results.append(dict(checksum=checksum, result_checksum=result, created_at=_utc(row['created_at'])))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('Invalid audit functional history; migration unchanged') from None
    return snapshots, results


def _validate_domain_tables(connection, snapshots, receipts):
    inspector = inspect(connection)
    for model, source, key in ((StopwordImportSnapshot, snapshots, 'id'),
                               (GlossaryMigrationReceipt, receipts, 'checksum')):
        table = model.__table__
        if table.name not in inspector.get_table_names():
            continue
        _compatible_columns(connection, table.name, {c.name: c for c in table.c})
        if (inspector.get_pk_constraint(table.name).get('constrained_columns') != [key]
                or inspector.get_foreign_keys(table.name)
                or inspector.get_unique_constraints(table.name)
                or any(index.get('unique') for index in inspector.get_indexes(table.name))
                or inspector.get_check_constraints(table.name)
                or any(column['nullable'] != table.c[column['name']].nullable
                       for column in inspector.get_columns(table.name))):
            raise ValueError('Unknown audit functional history schema')
        if not source:
            continue
        existing = {r[key]: r for r in connection.execute(select(table)).mappings()}
        for row in source:
            other = existing.get(row[key])
            if other is not None and any(
                    _utc(other[name]) != _utc(value) if name == 'created_at' else other[name] != value
                    for name, value in row.items()):
                raise ValueError('Conflicting audit functional history; migration unchanged')


def _repair_sequence(connection, table, column='id'):
    if connection.dialect.name == 'postgresql':
        # RESTART, unlike setval(), rolls back with this migration transaction.
        sequence = connection.scalar(text('SELECT pg_get_serial_sequence(:table,:column)'),
                                     {'table': table, 'column': column})
        if sequence is None:
            raise ValueError('Audit identity sequence required')
        namespace, name = connection.execute(text("""SELECT n.nspname,c.relname
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.oid=CAST(:sequence AS regclass)"""), {'sequence': sequence}).one()
        quote = connection.dialect.identifier_preparer.quote
        qualified = f'{quote(namespace)}.{quote(name)}'
        last_value, is_called = connection.execute(text(f'SELECT last_value,is_called FROM {qualified}')).one()
        maximum = connection.scalar(text(f'SELECT MAX({column}) FROM {table}'))
        next_value = max(1, (maximum or 0) + 1, last_value + int(is_called))
        connection.execute(text(f'ALTER SEQUENCE {qualified} RESTART WITH {next_value}'))


def _upgrade_queue(connection):
    # The replacement's name/indices are created only after old indices are removed.
    # INSERT SELECT keeps payload and all delivery columns byte-for-byte in the DB.
    connection.execute(text('ALTER TABLE audit_outbox RENAME TO audit_outbox_v1_upgrade'))
    if connection.dialect.name == 'postgresql':
        primary = inspect(connection).get_pk_constraint('audit_outbox_v1_upgrade')['name']
        quoted = connection.dialect.identifier_preparer.quote(primary)
        connection.execute(text(f'ALTER TABLE audit_outbox_v1_upgrade RENAME CONSTRAINT {quoted} TO audit_outbox_v1_upgrade_pkey'))
    for index in inspect(connection).get_indexes('audit_outbox_v1_upgrade'):
        quoted = connection.dialect.identifier_preparer.quote(index['name'])
        connection.execute(text(f'DROP INDEX {quoted}'))
    AuditOutbox.__table__.create(connection)
    connection.execute(text("""INSERT INTO audit_outbox
        (id,event_id,created_at,payload,status,attempt_count,next_attempt_at,last_error_code,stdout_written_at)
        SELECT q.audit_id,l.event_id,l.created_at,q.payload,q.status,q.attempt_count,q.next_attempt_at,
               q.last_error_code,q.stdout_written_at
        FROM audit_outbox_v1_upgrade q JOIN audit_log l ON l.id=q.audit_id"""))
    connection.execute(text('DROP TABLE audit_outbox_v1_upgrade'))
    _repair_sequence(connection, 'audit_outbox')


def _backfill_domain(connection, snapshots, receipts):
    for model, rows, key in ((StopwordImportSnapshot, snapshots, 'id'),
                              (GlossaryMigrationReceipt, receipts, 'checksum')):
        table = model.__table__
        table.create(connection, checkfirst=True)
        existing = set(connection.scalars(select(table.c[key])))
        pending = [row for row in rows if row[key] not in existing]
        if pending:
            connection.execute(table.insert(), pending)
    _repair_sequence(connection, 'stopword_import_snapshots')


def upgrade_audit_schema(connection):
    """Upgrade legacy or exact v1, or validate an already-upgraded v2 database.

    Writers must be stopped. Caller owns the surrounding transaction; SQLite's
    driver also gets an explicit BEGIN so DDL participates in its rollback.
    """
    if connection.dialect.name not in {'sqlite', 'postgresql'}:
        raise ValueError('Unsupported audit migration database')
    if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')
    inspector = inspect(connection)
    names = inspector.get_table_names()
    if 'audit_log' not in names:
        raise ValueError('Legacy audit table required')
    if 'audit_outbox_v1_upgrade' in names:
        raise ValueError('Unknown audit migration staging table')
    if connection.dialect.name == 'postgresql':
        tables = 'audit_log,audit_outbox' if 'audit_outbox' in names else 'audit_log'
        connection.execute(text(f'LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE'))
    present = set(FIELDS) & {c['name'] for c in inspector.get_columns('audit_log')}
    if present:
        _validate_log(connection)
        version = validate_outbox_contract(connection)
        if version == 2:
            validate_audit_schema(connection)
            _validate_domain_tables(connection, [], [])
            if all(model.__table__.name in names for model in _DOMAIN_MODELS):
                validate_audit_domain_schema(connection)
                return
            snapshots, receipts = _domain_sources(connection)
            _validate_domain_tables(connection, snapshots, receipts)
            _backfill_domain(connection, snapshots, receipts)
            validate_audit_domain_schema(connection)
            return
        validate_v1(connection)
        _validate_queue_rows(connection)
    else:
        if 'audit_outbox' in names:
            raise ValueError('Unknown audit schema')
        legacy = {c.name: c for c in AuditLog.__table__.c if c.name not in FIELDS}
        _compatible_columns(connection, 'audit_log', legacy)
        if inspector.get_pk_constraint('audit_log').get('constrained_columns') != ['id']:
            raise ValueError('Unknown audit schema')
    snapshots, receipts = _domain_sources(connection)
    _validate_domain_tables(connection, snapshots, receipts)
    if not present:
        upgrade_v1(connection)
    _upgrade_queue(connection)
    _backfill_domain(connection, snapshots, receipts)
    validate_audit_schema(connection)
    validate_audit_domain_schema(connection)
