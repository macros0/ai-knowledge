"""Роут глобального справочника тегов и переводов (Этап 7 фаза B)."""
from app.api import errors
from app.services.errors import DomainError
from app.api.errors import ApiError
from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.models import User
from app.auth.service import require_role
from app.models.schemas import (
    TagBulkReviewRequest,
    TagListOut,
    TagOut,
    TagTranslationBackfillRequest,
    TagTranslationUpdate,
    TranslationPendingOut,
)
from app.services import audit
from app.services.locale_service import request_locale
from app.services.tag_registry import TagInUseError, TagRegistry
from app.services.translation import backfill_reference_data, count_pending

router = APIRouter(prefix="/tags", tags=["tags"])

_tag_registry = TagRegistry()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("", response_model=TagListOut)
def list_tags(request: Request, needs_review: bool = False):
    locale = request_locale(request)
    items = _tag_registry.all(locale=locale)
    if needs_review:
        items = [t for t in items if t["needs_review"]]
    return TagListOut(tags=[TagOut(**item) for item in items])


@router.delete("/{tag}")
def delete_tag(
    tag: str,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Удаляет тег из справочника, если он не используется активными документами.

    404 — тега нет; 409 — используется (удаление запрещено). Каждое удаление —
    в audit_log (tag_delete).
    """
    try:
        deleted = _tag_registry.delete(tag)
    except TagInUseError as exc:
        raise ApiError(
            status_code=409,
            code=errors.TAG_IN_USE,
            detail=str(exc),
        ) from exc
    if not deleted:
        raise ApiError(
            status_code=404,
            code=errors.TAG_NOT_FOUND,
            detail="Тег не найден в справочнике",
        )
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
    """Удаляет из справочника все теги, не используемые активными документами."""
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


@router.patch("/{tag_id}/translations/{locale}")
def update_tag_translation(
    tag_id: int,
    locale: str,
    body: TagTranslationUpdate,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Записывает/правит перевод имени тега для локали (человеком)."""
    try:
        result = _tag_registry.set_translation(
            tag_id, body.locale, body.text, is_machine=False, reviewed_by=user.username
        )
    except DomainError as exc:
        raise errors.domain_error(exc, 404) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail=str(exc),
        ) from exc
    audit.record(
        user,
        audit.TAG_TRANSLATION_UPDATE,
        audit.TARGET_TAG,
        target_id=str(tag_id),
        old_value=None,
        new_value={"locale": body.locale, "text": body.text},
        ip_address=_client_ip(request),
    )
    return result


@router.post("/bulk-review")
def bulk_review_translations(
    body: TagBulkReviewRequest,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Подтверждает машинные переводы выбранных тегов (массовый review)."""
    count = _tag_registry.bulk_review(body.tag_ids, user.username)
    audit.record(
        user,
        audit.TAG_TRANSLATION_REVIEW,
        audit.TARGET_TAG,
        new_value={"tag_ids": body.tag_ids, "reviewed": count},
        ip_address=_client_ip(request),
    )
    return {"reviewed": count}


@router.post("/translations/backfill")
def backfill_translations(
    body: TagTranslationBackfillRequest,
    request: Request,
    user: User = Depends(require_role("admin")),
):
    """Запускает автоперевод справочников для локали (теги/разработки/атрибуты).

    Синхронно (admin), bulk-семафор LLM, идемпотентно (ручные переводы не
    перезаписываются). Возвращает счётчики created/failed по сущностям.
    """
    result = backfill_reference_data(
        body.locale, body.entities, user=user, ip_address=_client_ip(request)
    )
    return {"locale": body.locale, "result": result}


@router.get("/translations/pending", response_model=TranslationPendingOut)
def pending_translations(
    locale: str,
    entities: str = "tags,developments,attributes",
    user: User = Depends(require_role("admin")),
):
    """Число объектов справочника без ручного перевода в locale (для preview).

    Использует тот же _pending_rows, что и бэкфилл: показанное число совпадает
    с реальным прогоном.
    """
    ent = [e for e in entities.split(",") if e in ("tags", "developments", "attributes")]
    return TranslationPendingOut(locale=locale, pending=count_pending(locale, ent))
