"""Admin-only control and read access to bounded diagnostic artifacts."""
from datetime import datetime, timedelta, timezone
from uuid import UUID
from hashlib import sha256
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.background import BackgroundTask

from app import error_codes as codes
from app.api.errors import ApiError
from app.auth.models import User
from app.auth.service import require_role
from app.db.models import DiagnosticBundle
from app.db.session import session_scope
from app.models.diagnostics import BundleOut, BundleRequest, EventQuery, SessionStart
from app.services import audit
from app.services.diagnostics.browser import get_browser_service
from app.services.diagnostics.bundle_queue import BundleQueueError, _utc
from app.services.diagnostics.sessions import DiagnosticControlError
from app.services.diagnostics.schema import EventFilter
from app.services.diagnostics.read_view import attach_frontend

router = APIRouter(prefix="/admin/diagnostics", tags=["admin-diagnostics"])
admin = Depends(require_role("admin"))


def runtime(request: Request):
    value = getattr(request.app.state, "diagnostics", None)
    if value is None:
        raise ApiError(503, codes.DIAGNOSTIC_UNAVAILABLE, "Diagnostics unavailable")
    return value


def error(exc):
    code = exc.code
    status = {
        "forbidden": 403, codes.DIAGNOSTIC_DISABLED: 404,
        codes.DIAGNOSTIC_SESSION_NOT_FOUND: 404, codes.DIAGNOSTIC_BUNDLE_NOT_FOUND: 404,
        codes.DIAGNOSTIC_SESSION_ACTIVE: 409, codes.DIAGNOSTIC_BUNDLE_BUSY: 409,
        codes.DIAGNOSTIC_BUNDLE_NOT_READY: 409, codes.DIAGNOSTIC_BUNDLE_GONE: 410,
        codes.DIAGNOSTIC_BUNDLE_RATE_LIMITED: 429, codes.DIAGNOSTIC_RATE_LIMITED: 429,
        codes.DIAGNOSTIC_STORAGE_LOW: 507, codes.DIAGNOSTIC_BUNDLE_TOO_LARGE: 507,
        "invalid_request": 422,
    }.get(code, 503)
    raise ApiError(status, code, "Diagnostic action unavailable",
                   headers={"Retry-After": "60"} if status == 429 else None) from exc


def require_flag(enabled):
    if not enabled:
        raise ApiError(404, codes.DIAGNOSTIC_DISABLED, "Diagnostics disabled")


@router.get("/status")
def status(request: Request, user: User = admin):
    diag = runtime(request)
    settings = diag.settings
    return {
        "server_now": datetime.now(timezone.utc).isoformat(),
        "runtime": {"available": diag.available if hasattr(diag, "available") else True,
                    "failure_code": getattr(diag, "failure_code", None)},
        "capabilities": {"capture": settings.diagnostics_capture_enabled,
                         "bundle": settings.diagnostics_bundle_enabled,
                         "download": settings.diagnostics_download_enabled,
                         "baseline": settings.diagnostics_baseline_enabled,
                         "capture_levels": ["standard", "detailed"],
                         "policy_version": 1},
        "recorder": diag.recorder.status(), "session": diag.sessions.status(),
        "quota": diag.store.status(),
    }


def audited(user, action, target_type, target_id, value=None):
    try:
        with session_scope() as db:
            audit.record_in_session(db, action_type=action, user_id=user.user_id,
                                    username=user.username, target_type=target_type,
                                    target_id=target_id, new_value=value or {})
    except Exception as exc:
        raise ApiError(503, codes.DIAGNOSTIC_AUDIT_UNAVAILABLE, "Diagnostic audit unavailable") from exc


@router.post("/events/query")
def query_events(body: EventQuery, request: Request, user: User = admin):
    diag = runtime(request)
    now = datetime.now(timezone.utc)
    cutoff = body.cutoff_at or now
    if cutoff.tzinfo is None or cutoff.utcoffset() != timedelta(0) or cutoff > now or now - cutoff > timedelta(days=7):
        raise ApiError(422, "invalid_request", "Invalid diagnostic cutoff")
    filt = EventFilter(
        from_utc=body.from_utc,
        to_utc=min(body.to_utc, cutoff) if body.to_utc else cutoff,
        session_id=str(body.session_id) if body.session_id else None,
        request_id=str(body.request_id) if body.request_id else None,
        operation_id=str(body.operation_id) if body.operation_id else None,
        doc_id=body.doc_id,
    )
    view = diag.store.pin_read_view(filt, cutoff)
    try:
        if getattr(diag, "frontend_root", None):
            attach_frontend(view, diag.frontend_root)
        token_source = [(str(item.path), item.identity, item.length, item.mtime_ns)
                        for item in view.descriptors]
        view_token = sha256(json.dumps(token_source, separators=(",", ":")).encode()).hexdigest()
        changed = body.view_token is not None and body.view_token != view_token
        page = diag.store.read_event_page(view, offset=body.offset, limit=body.limit)
        audited(user, "diagnostic_view", "diagnostic_event_query", None,
                {**body.model_dump(mode="json", exclude_none=True), "returned_count": len(page.events)})
        return {"events": page.events, "counts": page.counts,
                "partial": page.partial or changed,
                "gaps": (*page.gaps, "coverage_changed") if changed else page.gaps,
                "next_offset": page.next_offset, "view_token": view_token,
                "cutoff_at": cutoff.isoformat()}
    finally:
        view.release()


@router.post("/sessions", status_code=201)
def start_session(body: SessionStart, request: Request, user: User = admin):
    diag = runtime(request)
    require_flag(diag.settings.diagnostics_capture_enabled)
    try:
        return diag.sessions.start(body.scope, body.minutes, user, doc_id=body.doc_id,
                                   capture_level=body.capture_level)
    except DiagnosticControlError as exc:
        error(exc)


@router.post("/sessions/{session_id}/stop")
def stop_session(session_id: UUID, request: Request, user: User = admin):
    diag = runtime(request)
    try:
        # Complete records accepted before the administrator closed this session.
        # Unknown/old UUIDs must not grow the recorder's closed-session set.
        active = diag.sessions.status()["session"]
        if active and active["id"] == str(session_id):
            diag.recorder.drain_capture(str(session_id))
        return diag.sessions.stop(session_id, user)
    except DiagnosticControlError as exc:
        error(exc)


@router.post("/sessions/{session_id}/invite")
def invite_browser(session_id: UUID, request: Request, user: User = admin):
    runtime(request)
    try:
        return {"code": get_browser_service().invite(session_id, user)}
    except DiagnosticControlError as exc:
        error(exc)


@router.post("/bundles", status_code=202)
def create_bundle(body: BundleRequest, request: Request, user: User = admin):
    diag = runtime(request)
    require_flag(diag.settings.diagnostics_bundle_enabled)
    try:
        return diag.bundle_queue.submit(body, user)
    except BundleQueueError as exc:
        error(exc)


@router.get("/bundles")
def list_bundles(request: Request, offset: int = Query(0, ge=0, le=100000),
                 limit: int = Query(20, ge=1, le=50), user: User = admin):
    runtime(request)
    try:
        with session_scope() as db:
            rows = db.scalars(select(DiagnosticBundle).order_by(DiagnosticBundle.created_at.desc())
                              .offset(offset).limit(limit)).all()
            return {"items": [BundleOut(id=row.id, status=row.status, created_at=row.created_at,
                                        finished_at=row.finished_at, expires_at=row.expires_at,
                                        size_bytes=row.size_bytes, sha256=row.sha256,
                                        error_code=row.error_code, counts=row.counts or {}) for row in rows]}
    except Exception as exc:
        raise ApiError(503, codes.DIAGNOSTIC_UNAVAILABLE, "Diagnostics unavailable") from exc


@router.post("/bundles/{bundle_id}/preview")
def preview_bundle(bundle_id: UUID, request: Request, user: User = admin):
    runtime(request)
    try:
        with session_scope() as db:
            row = db.get(DiagnosticBundle, str(bundle_id))
            if row is None:
                raise BundleQueueError("diagnostic_bundle_not_found")
            if row.status in {"deleted", "expired"} or row.expires_at and _utc(row.expires_at) <= datetime.now(timezone.utc):
                raise BundleQueueError("diagnostic_bundle_gone")
            if row.status != "ready":
                raise BundleQueueError("diagnostic_bundle_not_ready")
            audit.record_in_session(db, action_type="diagnostic_view", user_id=user.user_id,
                                    username=user.username, target_type="diagnostic_bundle", target_id=row.id,
                                    new_value={"preview": True, "filters": row.request,
                                               "event_count": row.manifest.get("counts", {}).get("events", 0)})
            return {"manifest": row.manifest, "size_bytes": row.size_bytes, "sha256": row.sha256}
    except BundleQueueError as exc:
        error(exc)
    except Exception as exc:
        raise ApiError(503, codes.DIAGNOSTIC_AUDIT_UNAVAILABLE, "Diagnostic audit unavailable") from exc


@router.get("/bundles/{bundle_id}/download")
def download_bundle(bundle_id: UUID, request: Request, user: User = admin):
    diag = runtime(request)
    require_flag(diag.settings.diagnostics_download_enabled)
    if request.headers.get("range"):
        raise ApiError(416, codes.DIAGNOSTIC_RANGE_UNSUPPORTED, "Range download unsupported")
    try:
        lease = diag.bundle_queue.acquire_download(bundle_id, user)
    except BundleQueueError as exc:
        error(exc)
    return StreamingResponse(
        lease.chunks(), media_type="application/zip", background=BackgroundTask(lease.release),
        headers={"Content-Length": str(lease.size_bytes),
                 "Content-Disposition": f'attachment; filename="{lease.filename}"',
                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                 "Accept-Ranges": "none"},
    )


@router.delete("/bundles/{bundle_id}", status_code=202)
def delete_bundle(bundle_id: UUID, request: Request, user: User = admin):
    diag = runtime(request)
    try:
        return diag.bundle_queue.delete(bundle_id, user)
    except BundleQueueError as exc:
        error(exc)
