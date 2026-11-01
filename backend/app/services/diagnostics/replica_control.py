"""SQL-authoritative capture control with short, fail-closed local selectors.

No successful control operation is backed only by an ephemeral stop journal.
Uncommitted stops disable this process immediately and are retried, but their
caller receives an error until state and security audit commit together.
"""
from datetime import timedelta
from types import MappingProxyType
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import DiagnosticSegment, DiagnosticSession, Document
from app.db.session import session_scope
from app.services import audit
from app.services.work.store import _now
from .policy import CapturePolicy, CaptureRuntimeView, build_policy, safe_policy_snapshot
from .sessions import DiagnosticControlError, DiagnosticSessionService, RecoveryResult, session_out, utc
from .replica_store import lock_budget

STOP_REASONS = {"manual", "expired", "size_limit", "storage_low", "storage_error", "server_restarted", "disabled"}
SELECTOR_LEASE_SECONDS = 10


def _retire(db, row, now, actor, reason):
    row.status, row.active_slot = "stopped", None
    row.stopped_at, row.stop_reason = now, reason
    row.participants, row.invitations = {}, {}
    from app.config import get_settings
    expiry = now + timedelta(seconds=get_settings().diagnostics_limits().capture_seconds)
    db.execute(update(DiagnosticSegment).where(DiagnosticSegment.stream == row.id,
        DiagnosticSegment.expires_at > expiry).values(expires_at=expiry))
    audit.record_in_session(db, action_type="diagnostic_session_stopped",
        user_id=actor.user_id if actor else "system", username=actor.username if actor else "system",
        target_type="diagnostic_session", target_id=row.id,
        new_value={"reason": reason, "bytes_written": row.bytes_written})


def control_projection(*, boot_id):
    with session_scope() as db:
        row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.active_slot == 1))
        now = _now(db)
        value = {"schema_version": 2, "boot_id": boot_id, "active": False,
            "revision": 0, "lease_until": now.timestamp() + SELECTOR_LEASE_SECONDS}
        if row is not None and row.status == "active" and utc(row.expires_at) > now and row.scope in {"system", "interface"}:
            policy = safe_policy_snapshot(row.policy_snapshot)
            if policy is None:
                raise ValueError("Diagnostic policy unavailable")
            value.update(active=True, session_id=row.id, scope=row.scope,
                deadline=utc(row.expires_at).timestamp(), policy=policy,
                revision=int(utc(row.created_at).timestamp() * 1_000_000))
        return value


class ReplicaDiagnosticSessionService(DiagnosticSessionService):
    def __init__(self, store, settings, *, boot_id=None):
        super().__init__(store, settings, boot_id=boot_id)
        if self.journal.items():
            raise ValueError("Legacy diagnostic control requires verified adoption")
        self.journal = None
        self._selector_until = 0.0
        self._local_stop = None

    def _clear_selector(self):
        self._active_view, self._active = None, None
        self._selector_until = 0.0

    def _publish(self, state, now, sampled_mono):
        policy = safe_policy_snapshot(state.get("policy_snapshot"))
        if policy is None:
            raise ValueError("Diagnostic policy unavailable")
        if self._active is None or self._active["id"] != state["id"]:
            self._revision += 1
        deadline = sampled_mono + max(0, (state["expires_at"] - now).total_seconds())
        state = {**state, "deadline_mono": deadline}
        self._active = state
        self._active_view = CaptureRuntimeView(session_id=state["id"], revision=self._revision,
            scope=state["scope"], doc_id=state["doc_id"], deadline_mono=deadline,
            policy=CapturePolicy(**{**policy, "slow_thresholds_ms": MappingProxyType(policy["slow_thresholds_ms"])}))
        self._selector_until = min(deadline, sampled_mono + SELECTOR_LEASE_SECONDS)

    def start(self, scope, minutes, actor, *, doc_id=None, capture_level="standard"):
        if not self.settings.diagnostics_capture_enabled:
            raise DiagnosticControlError("diagnostic_disabled")
        if (scope not in {"system", "document", "search_chat", "interface"}
                or type(minutes) is not int or not 5 <= minutes <= 60
                or (scope == "document") != (doc_id is not None)):
            raise DiagnosticControlError("invalid_request")
        try:
            policy = build_policy(capture_level, self.settings)
        except ValueError as exc:
            raise DiagnosticControlError("invalid_request") from exc
        with self._lock:
            if self._local_stop is not None:
                raise DiagnosticControlError("diagnostic_audit_unavailable")
            sampled_mono = self.monotonic()
            try:
                self.store.reserve(4096).release()
                with session_scope() as db:
                    lock_budget(db, self.store.limits)
                    current = db.scalar(select(DiagnosticSession).where(DiagnosticSession.active_slot == 1).with_for_update())
                    now = _now(db)
                    if current is not None:
                        if utc(current.expires_at) > now:
                            raise DiagnosticControlError("diagnostic_session_active")
                        _retire(db, current, now, None, "expired")
                        db.flush()  # Release the unique slot before inserting.
                    if doc_id:
                        document = db.get(Document, doc_id)
                        if document is None or document.deleted_at is not None:
                            raise DiagnosticControlError("document_not_found")
                    row = DiagnosticSession(id=str(uuid4()), active_slot=1, status="active", scope=scope,
                        doc_id=doc_id, capture_level=policy.level, policy_version=policy.version,
                        policy_snapshot=policy.snapshot(), boot_id=self.boot_id,
                        created_by_id=actor.user_id, created_by=actor.username, created_at=now,
                        expires_at=now + timedelta(minutes=minutes), bytes_written=0,
                        counts={}, participants={}, invitations={}, audit_receipts=[], schema_version=1)
                    db.add(row)
                    audit.record_in_session(db, action_type="diagnostic_session_started", user_id=actor.user_id,
                        username=actor.username, target_type="diagnostic_session", target_id=row.id,
                        new_value={"scope": scope, "minutes": minutes, "doc_id": doc_id,
                            "capture_level": policy.level, "policy_version": policy.version})
                    result = session_out(row)
                self._publish(result.model_dump(), now, sampled_mono)
                self._audit_gap = False
                return result
            except DiagnosticControlError:
                raise
            except IntegrityError as exc:
                raise DiagnosticControlError("diagnostic_session_active") from exc
            except Exception as exc:
                self._clear_selector()
                self._audit_gap = True
                raise DiagnosticControlError("diagnostic_audit_unavailable") from exc

    def _commit_stop(self, session_id, actor, reason):
        with session_scope() as db:
            lock_budget(db, self.store.limits)
            row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.id == session_id).with_for_update())
            if row is None:
                raise DiagnosticControlError("diagnostic_session_not_found")
            if row.status != "stopped":
                _retire(db, row, _now(db), actor, reason)
            result = session_out(row)
        self._last_stopped = result.model_dump()
        self._local_stop = None
        self._audit_gap = False
        return result

    def stop(self, session_id, actor=None, reason="manual"):
        if reason not in STOP_REASONS:
            raise DiagnosticControlError("invalid_request")
        with self._lock:
            identity = str(session_id)
            if self._active and self._active["id"] == identity:
                self._clear_selector()
            self._local_stop = (identity, actor, reason)
            try:
                return self._commit_stop(*self._local_stop)
            except DiagnosticControlError:
                self._local_stop = None
                raise
            except Exception as exc:
                self._audit_gap = True
                raise DiagnosticControlError("diagnostic_audit_unavailable") from exc

    def _queue_stop(self, state, actor, reason):
        if reason not in STOP_REASONS:
            raise DiagnosticControlError("invalid_request")
        self._local_stop = (state["id"], actor, reason)
        self._clear_selector()

    def active_for(self, context, event_code):
        if self.monotonic() >= self._selector_until:
            return None
        return super().active_for(context, event_code)

    def tick(self, now_utc=None, now_monotonic=None):
        # Caller wall time cannot extend a shared DB expiry. The monotonic sample
        # precedes SQL, so a blocked refresh cannot publish a fresh ten-second lease.
        with self._lock:
            sampled_mono = self.monotonic()
            try:
                if self._local_stop is not None:
                    self._commit_stop(*self._local_stop)
                with session_scope() as db:
                    lock_budget(db, self.store.limits)
                    row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.active_slot == 1).with_for_update())
                    now = _now(db)
                    state = None
                    if row is not None:
                        reason = ("expired" if utc(row.expires_at) <= now else
                            "disabled" if not self.settings.diagnostics_capture_enabled else None)
                        if reason:
                            _retire(db, row, now, None, reason)
                            self._last_stopped = session_out(row).model_dump()
                        elif row.status == "active":
                            state = session_out(row).model_dump()
                if state:
                    self._publish(state, now, sampled_mono)
                else:
                    self._clear_selector()
                self._audit_gap = False
            except Exception:
                self._clear_selector()
                self._audit_gap = True

    def status(self):
        self.tick()
        result = super().status()
        result["audit_pending"] = self._local_stop is not None
        return result

    def recover(self, boot_id):
        self.boot_id = boot_id
        self.tick()
        return RecoveryResult(pending=int(self._local_stop is not None))

    def reconcile_control_events(self):
        self.tick()

    def prune_metadata(self, now_utc=None):
        # R4 segment/instance retention owns coordinated metadata retirement.
        return None
