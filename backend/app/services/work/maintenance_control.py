"""Transaction fence and exclusive offline operator ownership, never a drain proof."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import re
import threading
from weakref import WeakKeyDictionary

from sqlalchemy import inspect, select, text, func
from sqlalchemy.orm import Session

from app.db.models import ArtifactStorageControl, ArtifactStorageMigration, MaintenanceState, ReplicaCapacitySlot

REQUIRED_WRITERS = frozenset({"api", "worker", "audit_dispatcher", "diagnostic_children",
    "provider_requests", "maintenance_jobs"})
_operator = ContextVar("replica_maintenance_operator", default=None)
_sqlite_mutex = threading.Lock()
_sqlite_locks = WeakKeyDictionary()


class MaintenanceRefused(ValueError):
    pass


def _state(session, *, exclusive=False):
    connection = session.connection()
    if not inspect(connection).has_table(MaintenanceState.__tablename__):
        return None
    statement = select(MaintenanceState.__table__).where(MaintenanceState.id == 1)
    if connection.dialect.name == "postgresql":
        statement = statement.with_for_update(read=not exclusive)
    return connection.execute(statement).mappings().one_or_none()


def assert_writes_allowed(session, expected_epoch, *, require_installed=False):
    """A PostgreSQL shared row lock is retained until this transaction ends."""
    if expected_epoch is not None and (type(expected_epoch) is not int or expected_epoch < 0):
        raise MaintenanceRefused("Invalid maintenance epoch")
    transaction = session.get_transaction()
    saved = session.info.get("replica_maintenance_guard")
    if (saved is not None and saved == (transaction, expected_epoch, require_installed)
            and session.get_bind().dialect.name == "postgresql"):
        return expected_epoch
    row = _state(session)
    if row is None:
        if require_installed or expected_epoch not in {None, 0}:
            raise MaintenanceRefused("Replica maintenance state is not installed")
        return 0  # Legacy compact schema, without a replica installation.
    if row["enabled"]:
        raise MaintenanceRefused("Replica maintenance is active")
    if expected_epoch is None or row["epoch"] != expected_epoch:
        raise MaintenanceRefused("Runtime maintenance epoch is retired")
    # Capture the actual transaction after _state's autobegin, not its previous
    # None value. No cached decision can survive commit, rollback or reconnect.
    session.info["replica_maintenance_guard"] = (session.get_transaction(), expected_epoch, require_installed)
    return row["epoch"]


def bind_runtime_epoch(session, settings):
    row = _state(session)
    identity = getattr(settings, "_runtime_identity", None)
    if (row is None or row["enabled"] or identity is not None
            and row["installation_id"] != identity.installation_id):
        raise MaintenanceRefused("Replica maintenance state is missing or active")
    previous = getattr(settings, "_maintenance_epoch", None)
    if previous is not None and previous != row["epoch"]:
        raise MaintenanceRefused("This process belongs to a retired maintenance epoch")
    settings._maintenance_epoch = row["epoch"]
    return row["epoch"]


def owned_operator(session):
    op = _operator.get()
    if op is None or session.info.get("replica_maintenance_operator") is not op:
        return False
    if session.connection() is not op.connection:
        raise MaintenanceRefused("Maintenance transaction belongs to another connection")
    op.assert_owned()
    return True


def enforce_session(session):
    from app.services.artifacts.control import _migration_access
    if _migration_access.get():
        return  # Existing explicit trusted offline migration boundary, not env.
    if owned_operator(session):
        return
    from app.config import get_settings
    settings = get_settings()
    assert_writes_allowed(session, getattr(settings, "_maintenance_epoch", None),
        require_installed=getattr(settings, "_runtime_identity", None) is not None)


class MaintenanceOperator:
    """Own one direct PostgreSQL session advisory lock for the whole operation.

    Transactions commit the fence independently of the long-running export.
    Losing this connection never permits an ACK/release through a fresh one.
    SQLite support is an explicit process-local test fallback only.
    """
    def __init__(self, engine, *, operation_id, owner_id, settings=None, allow_sqlite=False):
        if (not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id)
                or not isinstance(owner_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", owner_id)
                or engine.dialect.name != "postgresql" and not (allow_sqlite and engine.dialect.name == "sqlite")):
            raise MaintenanceRefused("Explicit supported maintenance operator required")
        self.engine, self.operation_id, self.owner_id = engine, operation_id, owner_id
        self.settings = settings
        self.connection = self.key = self.backend_pid = self.local_lock = None

    def __enter__(self):
        if self.connection is not None:
            raise MaintenanceRefused("Maintenance operator already open")
        try:
            if self.engine.dialect.name == "sqlite":
                with _sqlite_mutex:
                    lock = _sqlite_locks.setdefault(self.engine, threading.Lock())
                if not lock.acquire(blocking=False):
                    raise MaintenanceRefused("Maintenance operator is already active")
                self.local_lock = lock
            self.connection = self.engine.connect()
            if self.engine.dialect.name == "postgresql":
                database, schema, pid = self.connection.execute(text(
                    "SELECT current_database(),current_schema(),pg_backend_pid()" )).one()
                self.key = int.from_bytes(hashlib.sha256(
                    f"okf:replica-maintenance:v1:{database}:{schema}".encode()).digest()[:8], "big") & ((1 << 63) - 1)
                self.backend_pid = pid
                if self.connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": self.key}) is not True:
                    raise MaintenanceRefused("Maintenance operator is already active")
                self.connection.commit()
            return self
        except Exception:
            self.close()
            raise

    def assert_owned(self):
        connection = self.connection
        if connection is None or connection.closed or connection.invalidated:
            raise MaintenanceRefused("Maintenance operator connection is lost")
        if connection.dialect.name == "postgresql":
            owned = connection.scalar(text("""SELECT EXISTS (
                SELECT 1 FROM pg_locks WHERE locktype='advisory' AND granted
                AND pid=pg_backend_pid() AND pid=:pid AND classid::bigint=:high
                AND objid::bigint=:low AND objsubid=1)"""),
                {"pid": self.backend_pid, "high": self.key >> 32, "low": self.key & 0xffffffff})
            if owned is not True:
                raise MaintenanceRefused("Maintenance operator connection is lost")

    @contextmanager
    def transaction(self):
        self.assert_owned()
        if self.connection.dialect.name == "postgresql":
            self.connection.execute(text("SELECT set_config('lock_timeout','5000ms',true)"))
        # The ownership query can autobegin on the exact retained connection.
        # Join it explicitly so Session.commit commits the fence transaction.
        with Session(bind=self.connection, expire_on_commit=False, join_transaction_mode="control_fully") as session:
            session.info["replica_maintenance_operator"] = self
            token = _operator.set(self)
            try:
                with session.begin():
                    self.assert_owned()
                    yield session
                    session.flush()
                    self.assert_owned()
            finally:
                _operator.reset(token)

    def close(self):
        connection, self.connection = self.connection, None
        try:
            if connection is not None:
                try:
                    connection.invalidate()
                finally:
                    connection.close()
        finally:
            if self.local_lock is not None:
                self.local_lock.release()
                self.local_lock = None

    def __exit__(self, *_exc):
        self.close()


def _require_operator(session, operation_id):
    op = _operator.get()
    if (op is None or op.operation_id != operation_id or session.info.get("replica_maintenance_operator") is not op
            or session.connection() is not op.connection):
        raise MaintenanceRefused("An owned offline operator transaction is required")
    op.assert_owned()
    return op


def _audit(session, action, operation_id, epoch):
    from app.services import audit
    from app.services.audit_event import AuditContext
    audit.record_in_session(session, action_type=action, user_id="system", username="system",
        target_type="replica_operation", target_id=operation_id, new_value={"epoch": epoch},
        event_context=AuditContext(actor_type="system"), settings=_operator.get().settings)


def enter_maintenance(session, operation_id, *, kind, intended_counts):
    op = _require_operator(session, operation_id)
    if (not isinstance(kind, str) or kind not in {"backup", "upgrade", "restore", "migration"} or type(intended_counts) is not dict
            or set(intended_counts) != {"frontend", "api", "worker"}
            or any(type(n) is not int or not 0 <= n <= 64 for n in intended_counts.values())):
        raise MaintenanceRefused("Explicit operation and intended workload counts required")
    # Take the epoch before any work/control rows: all request transactions use
    # the same order. FOR UPDATE waits for older shared transaction guards.
    row = _state(session, exclusive=True)
    control = session.scalar(select(ArtifactStorageControl).where(ArtifactStorageControl.id == 1).with_for_update())
    if control is None or control.maintenance_token not in {None, operation_id}:
        raise MaintenanceRefused("Artifact storage operation is already active")
    if control.migration_id:
        history = session.get(ArtifactStorageMigration, control.migration_id)
        if (history is None or history.installation_id != control.installation_id or history.status != "cutover"
                or history.target_store_id != control.active_store_id or history.verified_hash != history.plan_hash):
            raise MaintenanceRefused("Artifact storage migration outcome is unknown")
    if row is None or row["installation_id"] != control.installation_id:
        raise MaintenanceRefused("Replica maintenance state is not installed")
    if row["enabled"]:
        if (row["operation_id"], row["owner_id"], row["operation_kind"], row["intended_counts_json"]) != (
                operation_id, op.owner_id, kind, intended_counts) or control.maintenance_token != operation_id:
            raise MaintenanceRefused("Another maintenance operation is active")
        return row["epoch"]
    if row["operation_id"] == operation_id:
        raise MaintenanceRefused("Completed operation cannot be re-entered")
    if row["epoch"] >= 2**63 - 1:
        raise MaintenanceRefused("Maintenance epoch exhausted")
    from .store import _now
    state = session.get(MaintenanceState, 1, populate_existing=True)
    if control.maintenance_token is not None:
        raise MaintenanceRefused("Artifact storage operation outcome is unknown")
    control.maintenance_token = operation_id
    state.epoch += 1
    state.enabled, state.operation_id, state.operation_kind = True, operation_id, kind
    state.owner_id, state.started_at, state.completed_at = op.owner_id, _now(session), None
    state.intended_counts_json, state.writer_receipts_json = dict(intended_counts), {}
    _audit(session, "replica_maintenance_enter", operation_id, state.epoch)
    session.flush()
    return state.epoch


def leave_maintenance(session, operation_id, *, writer_receipts):
    op = _require_operator(session, operation_id)
    row = _state(session, exclusive=True)
    if (row is None or not row["enabled"] or row["operation_id"] != operation_id or row["owner_id"] != op.owner_id):
        raise MaintenanceRefused("Maintenance operation ownership differs")
    control = session.scalar(select(ArtifactStorageControl).where(ArtifactStorageControl.id == 1).with_for_update())
    if (control is None or control.installation_id != row["installation_id"] or control.maintenance_token != operation_id):
        raise MaintenanceRefused("Artifact storage operation ownership differs")
    if (type(writer_receipts) is not dict or set(writer_receipts) != REQUIRED_WRITERS
            or any(not isinstance(value, str) or value not in {"stopped", "drained"} for value in writer_receipts.values())):
        raise MaintenanceRefused("Every writer must have an explicit verified stop/drain receipt")
    # Even an operator's generic stop claim cannot discard a tracked uncertain
    # request. Separate provider-specific, audited reconciliation is required.
    if session.scalar(select(func.count()).select_from(ReplicaCapacitySlot).where(
            ReplicaCapacitySlot.resource == "llm", ReplicaCapacitySlot.owner_id.is_not(None))):
        raise MaintenanceRefused("Provider requests remain active or uncertain")
    from .store import _now
    state = session.get(MaintenanceState, 1, populate_existing=True)
    state.writer_receipts_json = dict(writer_receipts)
    state.enabled, state.completed_at = False, _now(session)
    control.maintenance_token = None
    _audit(session, "replica_maintenance_leave", operation_id, state.epoch)
    session.flush()
    return state.epoch
