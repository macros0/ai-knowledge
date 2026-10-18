"""Portable reference writer protocol. No transaction spans an editor's lifetime."""
from contextlib import contextmanager
import hashlib
import json

from sqlalchemy import event, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

from app.db.models import ReferenceResourceState


class ReferenceError(RuntimeError):
    def __init__(self, code, status_code=409, **extra):
        from app.services.diagnostics.reference_counters import record_error
        record_error(code)
        self.code, self.status_code, self.extra = code, status_code, extra
        super().__init__(code)


def reference_preview_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _restore_sqlite_timeout(connection, record):
    original = record.info.pop("reference_original_busy_timeout", None)
    if connection is not None and original is not None:
        connection.execute(f"PRAGMA busy_timeout={int(original)}")


def translate_reference_timeout(exc, dialect):
    """Classify only database wait failures; callers retain rollback ownership."""
    if not isinstance(exc, OperationalError):
        return
    code = getattr(exc.orig, "sqlstate", None)
    if code in {"55P03", "57014", "40P01"} or (dialect == "sqlite" and "locked" in str(exc.orig).lower()):
        raise ReferenceError("reference_write_busy", 503) from exc


@contextmanager
def bounded_reference_transaction(session):
    """Apply transaction-local wait limits, including the glossary singleton lock."""
    dialect = session.get_bind().dialect.name
    session.info["reference_transaction_dialect"] = dialect
    try:
        if dialect == "postgresql":
            session.execute(text("SET LOCAL lock_timeout = '2s'"))
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
        elif dialect == "sqlite":
            connection = session.connection()
            if not event.contains(connection.engine.pool, "checkin", _restore_sqlite_timeout):
                event.listen(connection.engine.pool, "checkin", _restore_sqlite_timeout)
            if "reference_original_busy_timeout" not in connection.info:
                connection.info["reference_original_busy_timeout"] = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
            connection.exec_driver_sql("PRAGMA busy_timeout=2000")
        yield
    except OperationalError as exc:
        translate_reference_timeout(exc, dialect)
        raise



def lock_reference_resources(session, keys):
    """Lock stable rows in key order. Caller owns commit and all domain mutations."""
    keys = sorted(set(keys), key=lambda key: key.canonical)
    if not keys:
        return {}
    dialect = session.get_bind().dialect.name
    with bounded_reference_transaction(session):
        if any(key.resource_type.startswith("glossary_") for key in keys):
            from app.services.glossary.mutation import initialize_glossary_state
            from app.db.models import GlossaryState
            initialize_glossary_state(session)
            session.execute(update(GlossaryState).where(GlossaryState.id == 1).values(revision=GlossaryState.revision))
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        for key in keys:
            session.execute(insert(ReferenceResourceState).values(resource_key=key.canonical, revision=1,
                lease_epoch=0, activity_sequence=0).on_conflict_do_nothing(index_elements=["resource_key"]))
            session.execute(update(ReferenceResourceState).where(ReferenceResourceState.resource_key == key.canonical)
                .values(revision=ReferenceResourceState.revision).execution_options(synchronize_session=False))
        return {key: session.execute(select(ReferenceResourceState)
            .where(ReferenceResourceState.resource_key == key.canonical)
            .execution_options(populate_existing=True)).scalar_one() for key in keys}


def require_reference_revision(state, expected_revision):
    if type(expected_revision) is not int or expected_revision < 1:
        raise ReferenceError("reference_precondition_required", 428)
    if state.revision != expected_revision:
        raise ReferenceError("version_conflict", current_reference_revision=state.revision)


def bump_reference_revision(state):
    state.revision += 1
    return state.revision


def initialize_reference_resource(session, key):
    """Start an incarnation without reusing a retired ID's revision or lease."""
    existed = session.get(ReferenceResourceState, key.canonical) is not None
    state = lock_reference_resources(session, [key])[key]
    if existed:
        from app.services.reference_edit_lock import invalidate_reference_lease
        bump_reference_revision(state)
        invalidate_reference_lease(state)
    return state


def current_reference_revision(session, key):
    value = session.scalar(select(ReferenceResourceState.revision).where(ReferenceResourceState.resource_key == key.canonical))
    return int(value or 1)


def read_reference_snapshot(key, actor, reader):
    """Return one stable snapshot. This is a short SQL lock, never a user lease."""
    from app.db.session import session_scope
    from app.services.reference_resources import require_access, require_resource
    with session_scope() as session:
        require_access(key, actor)
        state = lock_reference_resources(session, [key])[key]
        require_resource(session, key)
        return {"data": reader(session), "reference_revision": state.revision}
