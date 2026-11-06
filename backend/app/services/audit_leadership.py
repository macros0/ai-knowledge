"""Session advisory ownership; queue transactions never borrow another connection.

Requires direct PostgreSQL or session pooling, never transaction pooling. Closing
an owner physically discards its connection, including its session-level locks.
"""
from contextlib import contextmanager
import hashlib

from sqlalchemy import text
from sqlalchemy.orm import Session


class AuditLeadershipLost(RuntimeError):
    pass


class AuditLeadership:
    def __init__(self, engine):
        if engine.dialect.name != "postgresql":
            raise ValueError("Replica audit requires PostgreSQL session ownership")
        self.engine = engine
        self.connection = None
        self.backend_pid = None
        self.key = None

    def acquire(self):
        if self.connection is not None:
            return True
        connection = self.engine.connect()
        try:
            database, schema, pid = connection.execute(text(
                "SELECT current_database(), current_schema(), pg_backend_pid()"
            )).one()
            key = int.from_bytes(hashlib.sha256(
                f"okf:audit-outbox:v1:{database}:{schema}".encode()
            ).digest()[:8], "big") & ((1 << 63) - 1)
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            connection.commit()
            if acquired is not True:
                return False
            self.connection, self.backend_pid, self.key = connection, pid, key
            return True
        finally:
            if self.connection is not connection:
                # Even an unsuccessful candidate must not return session state
                # to a shared pool (including after an ambiguous lock ACK).
                connection.invalidate()
                connection.close()

    def _assert_owned(self, session):
        connection = self.connection
        if connection is None or connection.closed or connection.invalidated:
            raise AuditLeadershipLost("Audit leadership unavailable")
        owned = session.scalar(text("""SELECT EXISTS (
            SELECT 1 FROM pg_locks WHERE locktype='advisory'
            AND pid=pg_backend_pid() AND pid=:pid AND granted
            AND classid::bigint=:high AND objid::bigint=:low AND objsubid=1
        )"""), {"pid": self.backend_pid, "high": self.key >> 32, "low": self.key & 0xffffffff})
        if owned is not True:
            raise AuditLeadershipLost("Audit leadership unavailable")

    @contextmanager
    def session(self):
        connection = self.connection
        if connection is None or connection.closed or connection.invalidated:
            raise AuditLeadershipLost("Audit leadership unavailable")
        # This exact connection owns the session-level advisory lock. SQLAlchemy
        # cannot transparently reconnect after invalidation: each entry checks
        # invalidated BEFORE executing any statement.
        with Session(bind=connection, expire_on_commit=False) as session:
            with session.begin():
                self._assert_owned(session)
                yield session
                session.flush()
                self._assert_owned(session)

    def close(self):
        connection, self.connection = self.connection, None
        self.backend_pid = self.key = None
        if connection is not None:
            try:
                connection.invalidate()
            finally:
                connection.close()
