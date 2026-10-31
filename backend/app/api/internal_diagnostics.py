"""Token-authenticated internal routes; never mounted under the public API proxy."""
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.services.diagnostics.ingest import (
    DiagnosticBatch, IngestConflict, MAX_BATCH_BYTES, current_control, ingest_events,
)
from app.services.diagnostics.store import StorageQuotaError

router = APIRouter(prefix="/internal/diagnostics", include_in_schema=False)


def internal_identity(request: Request, settings=Depends(get_settings)):
    token = settings.diagnostics_ingest_token
    if not token:
        raise HTTPException(404, "Not found")
    supplied = request.headers.get("Authorization", "")
    if not secrets.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
        raise HTTPException(401, "Invalid internal identity")
    runtime = getattr(request.app.state, "diagnostics", None)
    if runtime is None or not runtime.available:
        raise HTTPException(503, "Diagnostics unavailable")
    return runtime, settings


@router.get("/control")
def get_control(identity=Depends(internal_identity)):
    runtime, _ = identity
    try:
        return current_control(runtime.store)
    except (OSError, ValueError, TypeError):
        raise HTTPException(503, "Diagnostic control unavailable") from None


@router.post("/events")
async def post_events(request: Request, identity=Depends(internal_identity)):
    runtime, settings = identity
    size = 0
    chunks = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BATCH_BYTES:
            raise HTTPException(413, "Diagnostic batch too large")
        chunks.append(chunk)
    try:
        batch = DiagnosticBatch.model_validate(json.loads(b"".join(chunks)))
    except (ValueError, TypeError, ValidationError):
        raise HTTPException(422, "Invalid diagnostic batch") from None
    try:
        return await run_in_threadpool(ingest_events, batch, store=runtime.store,
                                      baseline_enabled=settings.diagnostics_baseline_enabled)
    except IngestConflict:
        raise HTTPException(409, "Batch identity conflict") from None
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid diagnostic batch") from None
    except (StorageQuotaError, OSError):
        raise HTTPException(503, "Diagnostic storage unavailable") from None
