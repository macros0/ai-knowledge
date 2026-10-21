"""Fail-closed audit readiness checks and payload-free operator status."""
from datetime import datetime, timezone
import time
from sqlalchemy import false, func, insert, null, select, text, inspect, update
from app.db.models import AuditLog, AuditOutbox
from app.db.session import get_engine
from .audit_schema import validate_audit_schema, validate_audit_domain_schema
from .audit_outbox import ERROR_CODES
from .audit_modes import get_audit_mode


def validate_existing_audit():
    with get_engine().connect() as connection:
        if 'audit_log' in inspect(connection).get_table_names():
            validate_audit_schema(connection)
            validate_audit_domain_schema(connection)


def validate_audit_transport_profile(settings, *, engine=None):
    """All modes record denial events, so validate their bounded DB profile."""
    get_audit_mode(settings)
    engine = engine if engine is not None else get_engine()
    if engine.dialect.name == 'postgresql':
        from .audit_denial_transport import reject_service_profile, validate_driver, connection_parameters, WORK_SECONDS
        from .audit_file_materials import prepare_files
        reject_service_profile(engine)
        validate_driver()
        deadline = time.monotonic() + WORK_SECONDS
        material = prepare_files(connection_parameters(engine, deadline=deadline), deadline)
        material.close()


def preflight_audit(settings, *, engine=None):
    mode = get_audit_mode(settings)
    engine = engine if engine is not None else get_engine()
    validate_audit_transport_profile(settings, engine=engine)
    selected = ([AuditLog] if mode.db_enabled else []) + ([AuditOutbox] if mode.stdout_enabled else [])
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            validate_audit_schema(connection)
            for model in selected:
                table = model.__table__
                connection.execute(select(table).where(false()))
                columns = [column.name for column in table.columns if column.name != 'id']
                connection.execute(insert(table).from_select(
                    columns, select(*(null() for _ in columns)).where(false()), include_defaults=False))
                if connection.dialect.name == 'postgresql':
                    # Names come only from the fixed selected model list above.
                    allowed = connection.scalar(text(
                        "SELECT has_sequence_privilege(pg_get_serial_sequence(:table,'id'),'USAGE')"
                    ), {'table': table.name})
                    if allowed is not True:
                        raise ValueError('Audit sequence permission required')
            if mode.stdout_enabled:
                mutable = ('status', 'attempt_count', 'next_attempt_at', 'last_error_code', 'stdout_written_at')
                connection.execute(update(AuditOutbox.__table__).where(false()).values(
                    {name: AuditOutbox.__table__.c[name] for name in mutable}))
        finally:
            transaction.rollback()


def delivery_status(*, engine=None, settings=None):
    from app.config import get_settings
    mode = get_audit_mode(settings if settings is not None else get_settings())
    engine = engine if engine is not None else get_engine()
    now = datetime.now(timezone.utc)
    with engine.connect() as connection:
        validate_audit_schema(connection)
        rows = connection.execute(select(AuditOutbox.status, func.count()).group_by(AuditOutbox.status)).all()
        counts = {key: 0 for key in ('pending', 'held', 'stdout_written')}
        for key, count in rows:
            if key not in counts:
                raise ValueError('Unknown audit delivery state')
            counts[key] = count
        created = connection.scalar(select(func.min(AuditOutbox.created_at)).where(AuditOutbox.status == 'pending'))
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        errors = {}
        for key, count in connection.execute(select(AuditOutbox.last_error_code, func.count()).where(
                AuditOutbox.last_error_code.is_not(None)).group_by(AuditOutbox.last_error_code)):
            safe = key if key in ERROR_CODES else 'unknown'
            errors[safe] = errors.get(safe, 0) + count
    return {'observed_at': now.isoformat(), 'counts': counts,
            'oldest_pending_age_seconds': max(0, (now-created).total_seconds()) if created else None,
            'runtime_state': 'unknown', 'error_counts': errors, 'effective_mode': mode.effective_mode,
            'delivery_paused': not mode.stdout_enabled and counts['pending'] > 0}
