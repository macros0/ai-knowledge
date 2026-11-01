"""Operations metrics require administrator authorization on the API role."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth.service import require_role
from app.config import get_settings

router = APIRouter(prefix="/admin/replicas", tags=["operations"])


@router.get("/metrics", dependencies=[Depends(require_role("admin"))])
def metrics(request: Request):
    from app.services.work.metrics import render_metrics
    settings = get_settings()
    if settings.okf_deployment_schema != "replicas-v1" or settings.app_role != "api":
        raise HTTPException(404)
    try:
        body = render_metrics(settings, request.app.state.runtime)
    except Exception:
        raise HTTPException(503, "Replica metrics unavailable") from None
    return Response(body, media_type="text/plain; version=0.0.4", headers={"Cache-Control": "no-store"})
