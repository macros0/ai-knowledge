"""SQL-audited activation. Stops happen in memory before any fallible I/O."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import threading
import time
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import DiagnosticSession, Document
from app.db.session import session_scope
from app.models.diagnostics import SessionOut
from app.services import audit
from .control import DeferredControlJournal, write_projection
from .schema import DiagnosticContext


class DiagnosticControlError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def session_out(row: DiagnosticSession) -> SessionOut:
    return SessionOut(
        id=row.id, status=row.status, scope=row.scope, doc_id=row.doc_id,
        created_at=utc(row.created_at), expires_at=utc(row.expires_at),
        stopped_at=utc(row.stopped_at) if row.stopped_at else None,
        stop_reason=row.stop_reason, bytes_written=row.bytes_written,
    )


@dataclass(frozen=True)
class RecoveryResult:
    stopped: int = 0
    pending: int = 0


class DiagnosticSessionService:
    def __init__(self, store, settings, *, boot_id=None):
        self.store = store
        self.settings = settings
        self.boot_id = str(boot_id or uuid4())
        self.utcnow = lambda: datetime.now(timezone.utc)
        self.monotonic = time.monotonic
        self.journal = DeferredControlJournal(store)
        self._lock = threading.RLock()
        self._active = None
        self._pending = []
        self._last_stopped = None
        self._audit_gap = False
        self._last_projection = 0.0

    def start(self, scope, minutes, actor, *, doc_id=None) -> SessionOut:
        if not self.settings.diagnostics_capture_enabled:
            raise DiagnosticControlError("diagnostic_disabled")
        if scope not in {"system", "document", "search_chat", "interface"} or type(minutes) is not int or not 5 <= minutes <= 60:
            raise DiagnosticControlError("invalid_request")
        if (scope == "document") != (doc_id is not None):
            raise DiagnosticControlError("invalid_request")
        with self._lock:
            if self._active:
                raise DiagnosticControlError("diagnostic_session_active")
            if self._audit_gap or self._pending:
                raise DiagnosticControlError("diagnostic_audit_unavailable")
            now = self.utcnow()
            row = DiagnosticSession(
                id=str(uuid4()), active_slot=1, status="starting", scope=scope, doc_id=doc_id,
                boot_id=self.boot_id, created_by_id=actor.user_id, created_by=actor.username,
                created_at=now, expires_at=now + timedelta(minutes=minutes), bytes_written=0,
                counts={}, participants={}, invitations={}, audit_receipts=[], schema_version=1,
            )
            try:
                self.store.reserve(4096).release()  # Disk admission before committing start.
                with session_scope() as db:
                    if db.scalar(select(DiagnosticSession.id).where(DiagnosticSession.active_slot == 1)):
                        raise DiagnosticControlError("diagnostic_session_active")
                    if doc_id:
                        document = db.get(Document, doc_id)
                        if document is None or document.deleted_at is not None:
                            raise DiagnosticControlError("document_not_found")
                    db.add(row)
                    audit.record_in_session(
                        db, action_type="diagnostic_session_started", user_id=actor.user_id,
                        username=actor.username, target_type="diagnostic_session", target_id=row.id,
                        new_value={"scope": scope, "minutes": minutes, "doc_id": doc_id},
                    )
                # Only committed audit allows a projection or in-memory selector.
                with session_scope() as db:
                    committed = db.get(DiagnosticSession, row.id)
                    committed.status = "active"
                state = session_out(row).model_dump()
                state.update(status="active", deadline_mono=self.monotonic() + minutes * 60)
                write_projection(self.store, boot_id=self.boot_id, active=state, now=now)
                self._active = state
                self._last_projection = self.monotonic()
                return SessionOut.model_validate(state)
            except DiagnosticControlError:
                raise
            except IntegrityError as exc:
                raise DiagnosticControlError("diagnostic_session_active") from exc
            except Exception as exc:
                # A committed starting/active row may exist; retire it before future admission.
                try:
                    with session_scope() as db:
                        failed = db.get(DiagnosticSession, row.id)
                    if failed:
                        self._last_stopped = session_out(failed).model_dump()
                        self._queue_stop(self._last_stopped, None, "storage_error")
                        self._flush_pending()
                except Exception:
                    self._audit_gap = True
                raise DiagnosticControlError("diagnostic_audit_unavailable") from exc

    def _queue_stop(self, state, actor, reason):
        if reason not in {"manual", "expired", "size_limit", "storage_low", "storage_error", "server_restarted", "disabled"}:
            raise DiagnosticControlError("invalid_request")
        now = self.utcnow()
        state.update(status="stopped", stopped_at=now, stop_reason=reason)
        state.pop("deadline_mono", None)
        self._last_stopped = state
        self._active = None
        self._pending.append({
            "event_id": str(uuid4()), "target_id": state["id"], "action": "diagnostic_session_stopped",
            "reason": reason, "timestamp_utc": now.isoformat(), "bytes_written": state["bytes_written"],
            "user_id": actor.user_id if actor else "system", "username": actor.username if actor else "system",
        })

    def stop(self, session_id, actor=None, reason="manual") -> SessionOut:
        session_id = str(session_id)
        with self._lock:
            if self._active and self._active["id"] == session_id:
                self._queue_stop(self._active, actor, reason)
            elif self._last_stopped and self._last_stopped["id"] == session_id:
                pass
            else:
                try:
                    with session_scope() as db:
                        row = db.get(DiagnosticSession, session_id)
                        if row is None:
                            raise DiagnosticControlError("diagnostic_session_not_found")
                        state = session_out(row).model_dump()
                    if state["status"] == "stopped":
                        return SessionOut.model_validate(state)
                    self._queue_stop(state, actor, reason)
                except DiagnosticControlError:
                    raise
                except Exception as exc:
                    raise DiagnosticControlError("diagnostic_audit_unavailable") from exc
            self._flush_pending()
            state = dict(self._last_stopped)
            state["audit_pending"] = bool(self._pending or self._audit_gap)
            return SessionOut.model_validate(state)

    def active_for(self, context: DiagnosticContext, event_code: str) -> str | None:
        with self._lock:
            state = self._active
            if state is None:
                return None
            if not self.settings.diagnostics_capture_enabled:
                self._queue_stop(state, None, "disabled")
                return None
            if self.monotonic() >= state["deadline_mono"]:
                self._queue_stop(state, None, "expired")
                return None
            if state["bytes_written"] >= self.store.limits.session_bytes:
                self._queue_stop(state, None, "size_limit")
                return None
            scope = state["scope"]
            matched = (
                scope == "system" or event_code == "dependency_status_changed"
                or (scope == "document" and context.doc_id == state["doc_id"])
                or (scope == "search_chat" and context.operation_kind == "search_chat" and event_code in {
                    "operation_started", "operation_finished", "operation_failed", "stage_started",
                    "stage_finished", "dependency_call_finished", "request_finished", "retry_scheduled",
                })
                or (scope == "interface" and event_code in {"request_finished", "proxy_failed", "render_failed", "browser_error"})
            )
            return state["id"] if matched else None

    def record_written(self, session_id: str, size: int):
        with self._lock:
            if self._active and self._active["id"] == session_id:
                self._active["bytes_written"] += size

    def recording_failed(self, session_id: str, reason: str):
        with self._lock:
            if self._active and self._active["id"] == session_id:
                self._queue_stop(self._active, None, reason if reason in {"size_limit", "storage_low"} else "storage_error")

    def _apply_control(self, event: dict):
        with session_scope() as db:
            row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.id == event["target_id"]).with_for_update())
            if row is None:
                raise DiagnosticControlError("diagnostic_session_not_found")
            if event["event_id"] in (row.audit_receipts or []):
                return
            row.status = "stopped"
            row.active_slot = None
            row.stopped_at = datetime.fromisoformat(event["timestamp_utc"])
            row.stop_reason = event["reason"]
            row.bytes_written = event["bytes_written"]
            row.participants = {}
            row.invitations = {}
            audit.record_in_session(
                db, action_type=event["action"], user_id=event["user_id"], username=event["username"],
                target_type="diagnostic_session", target_id=row.id,
                new_value={"reason": event["reason"], "bytes_written": row.bytes_written},
            )
            row.audit_receipts = [*(row.audit_receipts or []), event["event_id"]][-128:]

    def _flush_pending(self):
        try:
            write_projection(self.store, boot_id=self.boot_id, active=self._active, now=self.utcnow())
        except Exception:
            self._audit_gap = True
        for event in list(self._pending):
            durable = False
            try:
                self.journal.append(event)
                durable = True
            except Exception:
                self._audit_gap = True
            try:
                self._apply_control(event)
                if durable:
                    self.journal.remove(event["event_id"])
                self.store.retire_capture(event["target_id"], datetime.fromisoformat(event["timestamp_utc"]))
                self._pending.remove(event)
            except Exception:
                pass  # Capture is already off. Persist/retry without re-enabling it.

    def reconcile_control_events(self):
        with self._lock:
            try:
                items = self.journal.items()
            except Exception:
                self._audit_gap = True
                return
            for event in items:
                try:
                    self._apply_control(event)
                    self.store.retire_capture(event["target_id"], datetime.fromisoformat(event["timestamp_utc"]))
                    self.journal.remove(event["event_id"])
                    self._pending = [item for item in self._pending if item["event_id"] != event["event_id"]]
                except Exception:
                    continue
            self._flush_pending()
            if not self._pending:
                self._audit_gap = False

    def tick(self, now_utc=None, now_monotonic=None):
        with self._lock:
            mono = self.monotonic() if now_monotonic is None else now_monotonic
            if self._active:
                state = self._active
                reason = None
                if not self.settings.diagnostics_capture_enabled:
                    reason = "disabled"
                elif mono >= state["deadline_mono"]:
                    reason = "expired"
                elif state["bytes_written"] >= self.store.limits.session_bytes:
                    reason = "size_limit"
                if reason:
                    self._queue_stop(state, None, reason)
            if self._pending:
                self._flush_pending()
            if mono - self._last_projection >= 5:
                try:
                    write_projection(self.store, boot_id=self.boot_id, active=self._active, now=now_utc or self.utcnow())
                    self._last_projection = mono
                except Exception:
                    if self._active:
                        self._queue_stop(self._active, None, "storage_error")

    def recover(self, boot_id: str) -> RecoveryResult:
        with self._lock:
            self.boot_id = boot_id
            self._active = None
            write_projection(self.store, boot_id=boot_id, active=None, now=self.utcnow())
            self.reconcile_control_events()
            with session_scope() as db:
                states = [session_out(row).model_dump() for row in db.scalars(
                    select(DiagnosticSession).where(DiagnosticSession.active_slot == 1))]
            for state in states:
                self._queue_stop(state, None, "server_restarted")
            self._flush_pending()
            return RecoveryResult(len(states), len(self._pending))

    def status(self) -> dict:
        with self._lock:
            return {"session": SessionOut.model_validate(self._active).model_dump(mode="json") if self._active else None,
                    "last_session": SessionOut.model_validate(self._last_stopped).model_dump(mode="json") if self._last_stopped else None,
                    "audit_pending": bool(self._pending), "audit_gap": self._audit_gap}

    def prune_metadata(self, now_utc=None):
        now = now_utc or self.utcnow()
        with self._lock:
            pending_ids = {event["target_id"] for event in self._pending}
            pending_ids.update(event["target_id"] for event in self.journal.items())
            with session_scope() as db:
                rows = db.scalars(select(DiagnosticSession).where(
                    DiagnosticSession.status == "stopped",
                    DiagnosticSession.stopped_at < now - timedelta(days=7),
                ))
                for row in rows:
                    if row.id in pending_ids:
                        continue
                    directory = self.store.safe_path(self.store.root / "events" / row.id)
                    if directory.exists() and any(directory.iterdir()):
                        continue
                    expiry = self.store._capture_expiry(row.id)
                    if expiry is not None and expiry > now.timestamp():
                        continue
                    if directory.exists():
                        directory.rmdir()
                    marker = self.store.safe_path(self.store.root / "capture-expiry" / (row.id + ".json"))
                    marker.unlink(missing_ok=True)
                    db.delete(row)
