"""Authenticated opt-in ingestion. These routes expose no diagnostic journal."""
import json

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError

from app import error_codes as codes
from app.api.errors import ApiError
from app.auth.models import User
from app.auth.service import require_user
from app.models.diagnostics import BrowserJoin, BrowserLeave, ClientEvent
from app.services.diagnostics.browser import get_browser_service
from app.services.diagnostics.sessions import DiagnosticControlError

router = APIRouter(prefix="/diagnostic-client", tags=["diagnostics"])


def control_error(exc):
    status = {"unauthorized": 401, "forbidden": 403, codes.DIAGNOSTIC_BROWSER_NOT_JOINED: 403,
              "diagnostic_disabled": 404, "diagnostic_rate_limited": 429}.get(exc.code, 503)
    raise ApiError(status_code=status, code=exc.code, detail="Diagnostic browser action unavailable",
                   headers={"Retry-After": "60"} if status == 429 else None) from exc


async def bounded_body(request, model):
    content = bytearray()
    async for part in request.stream():
        if len(content) + len(part) > 4096:
            raise ApiError(status_code=413, code="file_too_large", detail="Diagnostic report too large")
        content.extend(part)
    try:
        return model.model_validate(json.loads(content))
    except (ValidationError, ValueError, TypeError) as exc:
        raise ApiError(status_code=422, code="invalid_request", detail="Invalid diagnostic report") from exc


@router.get("/status")
def browser_status(response: Response, user: User = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"available": get_browser_service().available(user)}
    except DiagnosticControlError as exc:
        if exc.code == "diagnostic_disabled":
            return {"available": False}
        control_error(exc)


@router.post("/join")
async def join(request: Request, user: User = Depends(require_user)):
    data = await bounded_body(request, BrowserJoin)
    try:
        return get_browser_service().join(data.code, user)
    except DiagnosticControlError as exc:
        control_error(exc)


@router.post("/leave", status_code=204)
async def leave(request: Request, user: User = Depends(require_user)):
    data = await bounded_body(request, BrowserLeave)
    try:
        get_browser_service().leave(data.participation_id, user)
    except DiagnosticControlError as exc:
        control_error(exc)
    return Response(status_code=204)


@router.post("/events", status_code=204)
async def ingest(request: Request, user: User = Depends(require_user)):
    data = await bounded_body(request, ClientEvent)
    try:
        get_browser_service().ingest(data.model_dump(exclude_none=True), user)
    except DiagnosticControlError as exc:
        control_error(exc)
    return Response(status_code=204)
