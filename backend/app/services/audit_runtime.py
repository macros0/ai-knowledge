"""Fail-closed audit readiness checks and private DB-only operator status."""
from datetime import datetime, timezone
import time
from sqlalchemy import func, select, text, inspect
from app.db.models import AuditOutbox
from app.db.session import get_engine
from .audit_schema import validate_audit_schema
from .audit_outbox import ERROR_CODES


def validate_existing_audit():
    # Check before best-effort startup init/seeds can touch a drifted installation.
    with get_engine().connect() as connection:
        if "audit_log" in inspect(connection).get_table_names():
            validate_audit_schema(connection)


def validate_audit_transport_profile(settings, *, engine=None):
    """Reject unsafe profiles before startup seeds touch the shared database."""
    if getattr(settings, 'audit_stdout_mode', 'disabled') == 'disabled':
        return None
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
    engine=engine or get_engine()
    if getattr(settings,'audit_stdout_mode','disabled')=='disabled':
        with engine.connect() as connection:
            validate_audit_schema(connection)
        return None
    if engine.dialect.name == "postgresql":
        from .audit_denial_transport import reject_service_profile, validate_driver, connection_parameters
        reject_service_profile(engine)
        validate_driver()
        from .audit_file_materials import prepare_files
        deadline = time.monotonic() + 4.0
        material = prepare_files(connection_parameters(engine, deadline=deadline), deadline)
        material.close()
    with engine.connect() as connection:
        transaction=connection.begin()
        try:
            validate_audit_schema(connection)
            from app.db.models import AuditLog
            for model in (AuditLog, AuditOutbox):
                readable = [column.name for column in model.__table__.columns]
                connection.execute(text("SELECT " + ",".join(readable) + " FROM " + model.__tablename__ + " WHERE 1=0"))
                columns = [column.name for column in model.__table__.columns
                           if not (model is AuditLog and column.name == "id")]
                connection.execute(text("INSERT INTO " + model.__tablename__ + "(" + ",".join(columns)
                                        + ") SELECT " + ",".join("NULL" for _ in columns) + " WHERE 1=0"))
            if connection.dialect.name == "postgresql":
                allowed = connection.scalar(text("SELECT has_sequence_privilege(pg_get_serial_sequence('audit_log','id'),'USAGE')"))
                if allowed is not True:
                    raise ValueError("Audit sequence permission required")
            mutable = ("status", "attempt_count", "next_attempt_at", "last_error_code", "stdout_written_at")
            connection.execute(text("UPDATE audit_outbox SET " + ",".join(name + "=" + name for name in mutable) + " WHERE 1=0"))
        finally:transaction.rollback()


def delivery_status(*,engine=None):
    engine=engine or get_engine()
    now=datetime.now(timezone.utc)
    with engine.connect() as connection:
        validate_audit_schema(connection)
        rows=connection.execute(select(AuditOutbox.status,func.count()).group_by(AuditOutbox.status)).all()
        counts={key:0 for key in ('pending','held','stdout_written')}
        for key,count in rows:
            if key not in counts:raise ValueError('Unknown audit delivery state')
            counts[key]=count
        created=connection.scalar(text("SELECT MIN(a.created_at) FROM audit_log a JOIN audit_outbox o ON o.audit_id=a.id WHERE o.status='pending'"))
        if isinstance(created,str):created=datetime.fromisoformat(created)
        if created is not None and created.tzinfo is None:created=created.replace(tzinfo=timezone.utc)
        errors={}
        for key,count in connection.execute(select(AuditOutbox.last_error_code,func.count()).where(AuditOutbox.last_error_code.is_not(None)).group_by(AuditOutbox.last_error_code)):
            safe=key if key in ERROR_CODES else 'unknown'
            errors[safe]=errors.get(safe,0)+count
    return {'observed_at':now.isoformat(),'counts':counts,'oldest_pending_age_seconds':max(0,(now-created).total_seconds()) if created else None,'runtime_state':'unknown','error_counts':errors}
