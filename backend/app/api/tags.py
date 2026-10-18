"""Роут глобального справочника тегов и переводов (Этап 7 фаза B)."""
from app.api import errors
from app.services.errors import DomainError
from app.api.errors import ApiError
from fastapi import APIRouter, Depends, Query, Request
from app.api.reference_editing import request_edit_session

from app.auth.models import User
from app.auth.service import require_role
from app.models.schemas import (
    TagBulkReviewRequest,
    TagCleanupRequest,
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
from app.services.reference_concurrency import ReferenceError
from app.services.reference_tag_actions import cleanup_preview, apply_cleanup, review_tag_items

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
    expected_reference_revision: int | None = Query(default=None, ge=1),
    user: User = Depends(require_role("editor", "admin")),
):
    """Удаляет тег из справочника, если он не используется активными документами.

    404 — тега нет; 409 — используется (удаление запрещено). Каждое удаление —
    в audit_log (tag_delete).
    """
    try:
        deleted = _tag_registry.delete(tag, expected_reference_revision=expected_reference_revision,
            actor=user, lease=request_edit_session(request), ip_address=_client_ip(request))
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
    return {"deleted": tag}


@router.post("/cleanup")
def cleanup_tags(
    request: Request,
    body: TagCleanupRequest | None = None,
    user: User = Depends(require_role("editor", "admin")),
):
    """Удаляет из справочника все теги, не используемые активными документами."""
    if body is None:
        raise ReferenceError("reference_precondition_required", 428)
    if not body.confirm:
        return cleanup_preview()
    if not body.preview_digest:
        raise ReferenceError("reference_precondition_required", 428)
    result = apply_cleanup([item.model_dump() for item in body.candidates], body.preview_digest, user)
    deleted = result["deleted"]
    audit.record(
        user,
        audit.TAG_CLEANUP,
        audit.TARGET_TAG,
        old_value=None,
        new_value={"deleted": deleted},
        ip_address=_client_ip(request),
    )
    return result


@router.patch("/{tag_id}/translations/{locale}")
def update_tag_translation(
    tag_id: int,
    locale: str,
    body: TagTranslationUpdate,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Записывает/правит перевод имени тега для локали (человеком)."""
    if body.locale != locale:
        raise ApiError(status_code=422, code=errors.INVALID_REQUEST, detail="Локаль тела не совпадает с URL")
    lease = None
    token, epoch = request.headers.get("X-Reference-Edit-Token"), request.headers.get("X-Reference-Edit-Epoch")
    if token and epoch and epoch.isdecimal():
        lease = {"edit_token": token, "lease_epoch": int(epoch)}
    try:
        result = _tag_registry.set_translation(
            tag_id, locale, body.text, is_machine=False, reviewed_by=user.username,
            expected_reference_revision=body.expected_reference_revision, actor=user, lease=lease,
        )
    except DomainError as exc:
        raise errors.domain_error(exc, 404) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=404,
            code=errors.DOCUMENT_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return result


@router.post("/bulk-review")
def bulk_review_translations(
    body: TagBulkReviewRequest,
    request: Request,
    user: User = Depends(require_role("editor", "admin")),
):
    """Подтверждает машинные переводы выбранных тегов (массовый review)."""
    if body.items is None:
        raise ReferenceError("reference_precondition_required", 428)
    return review_tag_items([item.model_dump() for item in body.items], user)


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
