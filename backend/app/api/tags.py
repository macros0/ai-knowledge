"""Роут глобального справочника тегов."""
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.models import User
from app.auth.service import require_role
from app.models.schemas import TagListOut, TagOut
from app.services import audit
from app.services.tag_registry import TagInUseError, TagRegistry

router = APIRouter(prefix="/tags", tags=["tags"])

_tag_registry = TagRegistry()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("", response_model=TagListOut)
def list_tags():
    return TagListOut(tags=[TagOut(**item) for item in _tag_registry.all()])


@router.delete("/{tag}")
def delete_tag(
    tag: str,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Удаляет имя тега из справочника автодополнения, если оно не используется.

    404 — тега нет в справочнике; 409 — тег используется документами (удаление
    запрещено). Операция не затрагивает связи документов. Каждое удаление —
    в audit_log (tag_delete).
    """
    try:
        deleted = _tag_registry.delete(tag)
    except TagInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Тег не найден в справочнике")
    audit.record(
        user,
        audit.TAG_DELETE,
        audit.TARGET_TAG,
        target_id=tag,
        old_value={"name": tag},
        new_value=None,
        ip_address=_client_ip(request),
    )
    return {"deleted": tag}


@router.post("/cleanup")
def cleanup_tags(
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Удаляет из справочника все теги, не используемые ни одним документом.

    Не затрагивает document_tags/okf_concepts/Qdrant — чистится только пул
    автодополнения. Возвращает удалённые имена; в audit_log — одна запись
    tag_cleanup с полным списком.
    """
    deleted = _tag_registry.delete_unused()
    audit.record(
        user,
        audit.TAG_CLEANUP,
        audit.TARGET_TAG,
        old_value=None,
        new_value={"deleted": deleted},
        ip_address=_client_ip(request),
    )
    return {"deleted": deleted, "total": len(deleted)}