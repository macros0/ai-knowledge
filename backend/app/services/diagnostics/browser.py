"""Explicit browser participation; raw client error text is never an input."""
from collections import OrderedDict, deque
import hashlib
import secrets
import threading
from uuid import uuid4

from sqlalchemy import select

from app.db.models import DiagnosticSession
from app.db.session import session_scope
from app.services import audit
from .schema import DiagnosticContext
from .sessions import DiagnosticControlError

_browser_service = None


def set_browser_service(service):
    global _browser_service
    _browser_service = service


def get_browser_service():
    if _browser_service is None:
        raise DiagnosticControlError("diagnostic_disabled")
    return _browser_service


class DiagnosticBrowserService:
    def __init__(self, sessions, recorder):
        self.sessions = sessions
        self.recorder = recorder
        self._attempts = OrderedDict()
        self._reports = OrderedDict()
        self._global_reports = deque()
        self._rate_lock = threading.Lock()

    def _rate(self, mapping, key, limit, *, global_limit=False):
        now = self.sessions.monotonic()
        with self._rate_lock:
            for identity, items in list(mapping.items()):
                while items and items[0] <= now - 60:
                    items.popleft()
                if not items:
                    del mapping[identity]
            while self._global_reports and self._global_reports[0] <= now - 60:
                self._global_reports.popleft()
            if key not in mapping and len(mapping) >= 4096:
                raise DiagnosticControlError("diagnostic_rate_limited")
            entries = mapping.setdefault(key, deque())
            if len(entries) >= limit or global_limit and len(self._global_reports) >= 100:
                raise DiagnosticControlError("diagnostic_rate_limited")
            entries.append(now)
            if global_limit:
                self._global_reports.append(now)

    def _active_id(self):
        identity = self.sessions.active_for(DiagnosticContext(operation_kind="interface"), "browser_error")
        if not identity or self.sessions._active["scope"] not in {"system", "interface"}:
            raise DiagnosticControlError("diagnostic_browser_not_joined")
        if self.sessions._audit_gap or self.sessions._pending:
            raise DiagnosticControlError("diagnostic_audit_unavailable")
        return identity

    def available(self, user):
        self._require_user(user)
        with self.sessions._lock:
            try:
                self._active_id()
            except DiagnosticControlError:
                return False
            return True

    @staticmethod
    def _require_user(user):
        if user.user_id == "anonymous" or not user.roles:
            raise DiagnosticControlError("unauthorized")

    @staticmethod
    def _audit(db, action, user, session_id):
        audit.record_in_session(db, action_type=action, user_id=user.user_id,
                                username=user.username, target_type="diagnostic_session", target_id=session_id,
                                new_value={"browser_participation": True})

    def invite(self, session_id, actor):
        self._require_user(actor)
        if "admin" not in actor.roles:
            raise DiagnosticControlError("forbidden")
        with self.sessions._lock:
            if self._active_id() != str(session_id):
                raise DiagnosticControlError("diagnostic_session_not_found")
            code = secrets.token_urlsafe(16)
            digest = hashlib.sha256(code.encode()).hexdigest()
            try:
                with session_scope() as db:
                    row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.id == str(session_id)).with_for_update())
                    if len(row.participants or {}) + len(row.invitations or {}) >= 10:
                        raise DiagnosticControlError("diagnostic_rate_limited")
                    row.invitations = {**(row.invitations or {}), digest: {"expires_at": row.expires_at.isoformat()}}
                    self._audit(db, "diagnostic_browser_invited", actor, row.id)
                return code
            except DiagnosticControlError:
                raise
            except Exception as exc:
                raise DiagnosticControlError("diagnostic_audit_unavailable") from exc

    def join(self, code, user):
        self._require_user(user)
        self._rate(self._attempts, user.user_id, 5)
        with self.sessions._lock:
            session_id = self._active_id()
            try:
                with session_scope() as db:
                    row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.id == session_id).with_for_update())
                    own_browser = code is None and "admin" in user.roles
                    digest = hashlib.sha256(code.encode()).hexdigest() if isinstance(code, str) else None
                    if not own_browser and (digest is None or digest not in (row.invitations or {})):
                        raise DiagnosticControlError("diagnostic_browser_not_joined")
                    participants = dict(row.participants or {})
                    if len(participants) >= 10:
                        raise DiagnosticControlError("diagnostic_rate_limited")
                    participation_id = str(uuid4())
                    membership = {"participation_id": participation_id, "expires_at": self.sessions._active["expires_at"].isoformat()}
                    participants[participation_id] = {"user_id": user.user_id, "expires_at": membership["expires_at"]}
                    row.participants = participants
                    if digest:
                        row.invitations = {key: value for key, value in (row.invitations or {}).items() if key != digest}
                    self._audit(db, "diagnostic_browser_joined", user, row.id)
                return {**membership, "server_now": self.sessions.utcnow().isoformat()}
            except DiagnosticControlError:
                raise
            except Exception as exc:
                raise DiagnosticControlError("diagnostic_audit_unavailable") from exc

    def leave(self, participation_id, user):
        self._require_user(user)
        with self.sessions._lock:
            try:
                session_id = self._active_id()
            except DiagnosticControlError as exc:
                if exc.code == "diagnostic_browser_not_joined":
                    return
                raise
            try:
                with session_scope() as db:
                    row = db.scalar(select(DiagnosticSession).where(DiagnosticSession.id == session_id).with_for_update())
                    membership = (row.participants or {}).get(str(participation_id))
                    if not membership or membership.get("user_id") != user.user_id:
                        return
                    row.participants = {key: value for key, value in row.participants.items() if key != str(participation_id)}
                    self._audit(db, "diagnostic_browser_left", user, row.id)
            except DiagnosticControlError:
                raise
            except Exception as exc:
                raise DiagnosticControlError("diagnostic_audit_unavailable") from exc

    def ingest(self, event, user):
        self._require_user(user)
        with self.sessions._lock:
            session_id = self._active_id()
            try:
                with session_scope() as db:
                    row = db.get(DiagnosticSession, session_id)
                    membership = (row.participants or {}).get(str(event.get("participation_id"))) if row else None
                    if not membership or membership.get("user_id") != user.user_id:
                        raise DiagnosticControlError("diagnostic_browser_not_joined")
            except DiagnosticControlError:
                raise
            except Exception as exc:
                raise DiagnosticControlError("diagnostic_audit_unavailable") from exc
            self._rate(self._reports, user.user_id, 10, global_limit=True)
            context = DiagnosticContext(request_id=event.get("request_id"), operation_kind="interface")
            self.recorder.emit_client_event({key: value for key, value in event.items() if key != "participation_id"},
                                            context=context, session_id=session_id)
