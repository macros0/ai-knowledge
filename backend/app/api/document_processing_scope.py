"""Document-scoped editing of the selected processing sources."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api.errors import ApiError
from app.auth.models import User
from app.auth.service import require_role, require_user
from app.config import get_settings
from app.db.models import DocumentGenerationState, DocumentProcessingReview, DocumentScopeChange
from app.db.session import session_scope
from app.models.processing_scope import ScopeReviewIn, ScopeSelectionIn, ScopeConfirmIn, ScopeChangePreviewIn, ScopeChangeApplyIn, ScopeEditLockIn
from app.models.schemas import DocumentOut
from app.services.processing_scope import (
    ProcessingScopeConflict,
    create_review,
    save_review_selection,
    scope_for_generation,
    scope_summary,
    review_summary,
    locked_review,
    assessment_limits,
    append_scope_audit,
)
from app.services.processing_admission import prepare_upload_scope, confirm_processing_scope
from app.services.registry import get_registry
from app.services.errors import DomainError
from app.services.parser_supervisor import (
    ParserBusyError,
    ParserIsolationError,
    ParserTimeoutError,
    ParserMemoryLimitError,
    ParserWorkerError,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/{doc_id}/processing-scope/edit-lock")
def claim_scope_editor(doc_id: str, body: ScopeEditLockIn, user: User = Depends(require_role("editor", "admin"))):
    from app.services.processing_scope_edit_lock import acquire_edit_lock
    try:
        with session_scope() as session:
            return acquire_edit_lock(session, doc_id, user.user_id, user.username, body.edit_token)
    except ProcessingScopeConflict as exc:
        raise _conflict(exc) from exc


@router.put("/{doc_id}/processing-scope/edit-lock")
def renew_scope_editor(doc_id: str, body: ScopeEditLockIn, user: User = Depends(require_role("editor", "admin"))):
    from app.services.processing_scope_edit_lock import renew_edit_lock
    try:
        with session_scope() as session:
            return renew_edit_lock(session, doc_id, user.user_id, body.edit_token, activity_sequence=body.activity_sequence)
    except ProcessingScopeConflict as exc:
        raise _conflict(exc) from exc


@router.delete("/{doc_id}/processing-scope/edit-lock")
def release_scope_editor(doc_id: str, body: ScopeEditLockIn, user: User = Depends(require_role("editor", "admin"))):
    from app.services.processing_scope_edit_lock import release_edit_lock
    try:
        with session_scope() as session:
            release_edit_lock(session, doc_id, user.user_id, body.edit_token)
        return {"released": True}
    except ProcessingScopeConflict as exc:
        raise _conflict(exc) from exc


def _require_editor(session, doc_id, body, user):
    from app.services.processing_scope_edit_lock import require_edit_lock
    require_edit_lock(session, doc_id, user.user_id, body.edit_token)


def _document(doc_id):
    document = get_registry().get(doc_id)
    if not document or document.get("deleted_at"):
        raise ApiError(status_code=404, code="document_not_found", detail="Документ недоступен.")
    return document


def _conflict(exc):
    return ApiError(status_code=409, code=exc.code, detail=str(exc))


def _parser_error(exc):
    if isinstance(exc, ParserBusyError):
        return ApiError(
            status_code=429,
            code="rate_limited",
            detail="Все процессы разбора документов заняты",
            headers={"Retry-After": "1"},
        )
    if isinstance(exc, ParserIsolationError):
        return ApiError(
            status_code=503,
            code="parser_isolation_unavailable",
            detail="Защищённый разбор документов недоступен",
        )
    return ApiError(
        status_code=422,
        code="parser_timeout" if isinstance(exc, ParserTimeoutError) else "parser_resource_limit",
        detail="Разбор документа превысил защитный лимит",
    )


@router.get("/{doc_id}/processing-scope")
def get_scope(doc_id: str, user: User = Depends(require_user)):
    document = _document(doc_id)
    with session_scope() as session:
        state = session.get(DocumentGenerationState, doc_id)
        generation_id = state.active_generation_id if state else None
        scope = scope_for_generation(session, doc_id, generation_id)
        active = (
            {
                "summary": scope_summary(scope, generation_id),
                "inventory": scope.get("inventory") if scope else None,
                "decisions": scope.get("decisions", []) if scope else [],
                "confirmed_by": scope.get("confirmed_by") if scope else None,
                "confirmed_at": scope.get("confirmed_at") if scope else None,
            }
            if generation_id or document.get("has_published_version")
            else None
        )
        review = session.get(DocumentProcessingReview, doc_id)
        pending = None
        if review and review.state not in {"published", "canceled"}:
            last_check = None
            if generation_id:
                from app.services.processing_scope_change import SCOPE_CHANGE_ANALYSIS_VERSION
                checked = session.scalar(select(DocumentScopeChange).where(
                    DocumentScopeChange.doc_id == doc_id,
                    DocumentScopeChange.state == 'preview',
                    DocumentScopeChange.plan_json['analysis_version'].as_integer() == SCOPE_CHANGE_ANALYSIS_VERSION,
                    DocumentScopeChange.plan_json['review_id'].as_string() == review.id,
                    DocumentScopeChange.plan_json['revision'].as_integer() == review.revision,
                    DocumentScopeChange.plan_json['preview']['selection_hash'].as_string() == review.selection_hash,
                    DocumentScopeChange.plan_json['preview']['base_generation_id'].as_string() == generation_id,
                ).order_by(DocumentScopeChange.created_at.desc(), DocumentScopeChange.id.desc()).limit(1))
                if checked:
                    last_check = {key: checked.plan_json['preview'].get(key) for key in ('mode', 'reasons')}
            pending = {
                **review_summary(review),
                "inventory": review.inventory_json,
                "decisions": review.selection_json,
                "last_check": last_check,
            }
        from app.auth.service import build_auth_provider
        can_edit = build_auth_provider(get_settings()).is_disabled() or bool(set(user.roles) & {"editor", "admin"})
        from app.services.processing_scope_edit_lock import edit_lock_status
        return {"active": active, "pending": pending, "edit_lock": edit_lock_status(session, doc_id), "capabilities": {
            "edit_lock": True,
            "fast_exclusion": can_edit, "cached_restore": can_edit,
            "incremental_generation": can_edit, "full_regeneration": can_edit}}


@router.get("/{doc_id}/processing-scope/labels")
def get_scope_labels(
    doc_id: str, scope_type: Literal['active', 'pending'],
    scope_id: str = Query(min_length=1, max_length=64),
    user: User = Depends(require_user),
):
    from app.services.processing_scope_labels import scope_attachment_labels
    document = _document(doc_id)
    try:
        labels = scope_attachment_labels(get_settings(), doc_id, document, scope_type, scope_id)
    except ProcessingScopeConflict as exc:
        raise _conflict(exc) from exc
    except (ParserBusyError, ParserIsolationError, ParserTimeoutError, ParserMemoryLimitError) as exc:
        raise _parser_error(exc) from exc
    except ParserWorkerError as exc:
        raise ApiError(status_code=422, code='parse_failed', detail='Не удалось прочитать подписи вложений.') from exc
    return JSONResponse(labels, headers={'Cache-Control': 'no-store'})


@router.post("/{doc_id}/processing-scope/change-preview")
def preview_scope_change(doc_id: str, body: ScopeChangePreviewIn, user: User = Depends(require_role("editor", "admin"))):
    _document(doc_id)
    from app.services.processing_scope_jobs import create_change_preview
    try:
        return create_change_preview(doc_id, body.review_id, body.revision, body.expected_active_generation_id, user.username,
                                     edit_owner_id=user.user_id, edit_token=body.edit_token)
    except ProcessingScopeConflict as exc:
        raise _conflict(exc) from exc


def _apply_change(doc_id, body, user, modes):
    _document(doc_id)
    from app.services.processing_scope_jobs import apply_scope_change, ScopeWorkerBusy
    try:
        result = apply_scope_change(doc_id, body.plan_id, body.request_id, body.expected_active_generation_id, user.username, modes,
                                    is_admin="admin" in user.roles, edit_owner_id=user.user_id, edit_token=body.edit_token)
        return JSONResponse(status_code=200 if result["state"] in {"published", "failed", "canceled"} else 202, content=result)
    except ScopeWorkerBusy as exc:
        raise ApiError(status_code=429, code="scope_worker_busy", detail=str(exc), headers={"Retry-After": "2"}) from exc
    except ProcessingScopeConflict as exc:
        raise _conflict(exc) from exc


@router.post("/{doc_id}/processing-scope/apply-exclusions")
def apply_scope_exclusions(doc_id: str, body: ScopeChangeApplyIn, user: User = Depends(require_role("editor", "admin"))):
    return _apply_change(doc_id, body, user, {"fast_exclude", "noop"})


@router.post("/{doc_id}/processing-scope/apply-changes")
def apply_scope_changes(doc_id: str, body: ScopeChangeApplyIn, user: User = Depends(require_role("editor", "admin"))):
    return _apply_change(doc_id, body, user, {"reuse_only", "incremental_generate"})


@router.get("/{doc_id}/processing-scope/changes/{plan_id}")
def scope_change_status(doc_id: str, plan_id: str, user: User = Depends(require_user)):
    _document(doc_id)
    from app.services.processing_scope_jobs import get_scope_change
    try:
        return get_scope_change(doc_id, plan_id, user.username)
    except ValueError as exc:
        raise ApiError(status_code=404, code="scope_change_not_found", detail="Изменение состава недоступно.") from exc


@router.post("/{doc_id}/processing-scope/review")
def create_scope_review(
    doc_id: str, body: ScopeReviewIn, user: User = Depends(require_role("editor", "admin"))
):
    document = _document(doc_id)
    if document["status"] in {"uploaded", "queued", "processing", "splitting", "indexing"}:
        raise ApiError(
            status_code=409, code="already_processing", detail="Дождитесь завершения обработки."
        )
    settings = get_settings()
    path = settings.uploads_dir / (doc_id + Path(document["filename"]).suffix.lower())
    filename = document["filename"]
    try:
        from docparser.attachment_policy import AttachmentInventory, inventory_traversal_limits, inventory_node_depth
        from app.services.processing_scope import refresh_review_inventory
        from app.services.generation_files import processing_review_paths
        import shutil

        previous = None
        branch = None
        traversal = None
        with session_scope() as session:
            _require_editor(session, doc_id, body, user)
            existing_review = session.get(DocumentProcessingReview, doc_id)
            if (
                not body.review_id
                and existing_review
                and existing_review.state not in {"published", "canceled"}
            ):
                raise ProcessingScopeConflict("Состав уже открыт для выбора. Обновите данные.")
            if body.expand_source_keys and not body.review_id:
                raise ProcessingScopeConflict("Для раскрытия вложения укажите текущий состав.")
            if body.review_id:
                if body.expected_revision is None:
                    raise ProcessingScopeConflict()
                old = locked_review(session, doc_id, body.review_id, body.expected_revision)
                if old.state not in {"draft", "awaiting_similar"}:
                    raise ProcessingScopeConflict()
                previous = AttachmentInventory.from_dict(old.inventory_json)
                traversal = inventory_traversal_limits(previous)
                if body.expand_source_keys:
                    if len(body.expand_source_keys) != 1:
                        raise ProcessingScopeConflict("Раскрывайте вложения по одному.")
                    nodes = {n.source_key: n for n in previous.nodes}
                    node = nodes.get(body.expand_source_keys[0])
                    if node is None:
                        raise ProcessingScopeConflict()
                    depth = inventory_node_depth(old.inventory_json['nodes'], node.source_key)
                    if depth is None:
                        raise ProcessingScopeConflict()
                    if node.status == 'skipped_depth' or depth > traversal.max_depth:
                        raise ProcessingScopeConflict('Достигнут предел вложенности. Раскрытие этого вложения недоступно.',
                                                      code='attachment_depth_limit')
                    if node.saved_path:
                        root = processing_review_paths(settings, doc_id, old.id) / "attachments"
                        if Path(node.saved_path).name != node.saved_path:
                            raise ProcessingScopeConflict()
                        path = root / node.saved_path
                        filename = node.display_name
                        from app.services.generation_files import _reject_links

                        _reject_links(path)
                        chain = []
                        parent = node.parent_key
                        while parent in nodes:
                            chain.insert(0, nodes[parent].locator)
                            parent = nodes[parent].parent_key
                        branch = {
                            "source_file_hash": old.source_file_hash,
                            "locator": node.locator,
                            "parent_key": node.parent_key,
                            "parent_chain": chain,
                            "depth": len(chain) + 1,
                        }
        with TemporaryDirectory(prefix="scope-review-", dir=settings.uploads_dir) as folder:
            inventory, _ = prepare_upload_scope(
                path, filename, settings, attachments_dir=Path(folder), branch=branch, traversal=traversal
            )
            if branch:
                removed = {body.expand_source_keys[0]}
                for node in previous.nodes:
                    if node.parent_key in removed:
                        removed.add(node.source_key)
                inventory.nodes = [
                    n for n in previous.nodes if n.source_key not in removed
                ] + inventory.nodes
                inventory.complete = all(
                    n.children_complete and n.assessment != "unknown" for n in inventory.nodes
                )
                if len(inventory.nodes) > 1000:
                    raise ProcessingScopeConflict("Достигнут лимит вложений.")
            with session_scope() as session:
                _require_editor(session, doc_id, body, user)
                if body.review_id:
                    review = refresh_review_inventory(
                        session,
                        doc_id,
                        body.review_id,
                        body.expected_revision,
                        inventory,
                        actor=user.username,
                    )
                else:
                    active = scope_for_generation(
                        session, doc_id, body.expected_active_generation_id
                    )
                    review = create_review(
                        session,
                        doc_id,
                        inventory,
                        base_generation_id=body.expected_active_generation_id,
                        actor=user.username,
                        limits=assessment_limits(settings),
                    )
                    if active and active["source_file_hash"] == inventory.source_file_hash:
                        selected = {
                            d["source_key"] for d in active["decisions"] if d["effective_included"]
                        }
                        from app.services.processing_scope import eligible_existing_selection

                        known = eligible_existing_selection(inventory, selected)
                        review = save_review_selection(
                            session,
                            doc_id,
                            review.id,
                            review.revision,
                            known,
                            actor=user.username,
                        )
                target = processing_review_paths(settings, doc_id, review.id)
                target.mkdir(parents=True, exist_ok=True)
                shutil.copytree(folder, target / "attachments", dirs_exist_ok=True)
        from app.services.processing_review_cleanup import cleanup_superseded_reviews

        cleanup_superseded_reviews(settings, doc_id, review.id)
        return get_scope(doc_id, user)
    except ProcessingScopeConflict as exc:
        raise _conflict(exc) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=422, code="parse_failed", detail="Не удалось прочитать структуру документа."
        ) from exc
    except (
        ParserBusyError,
        ParserIsolationError,
        ParserTimeoutError,
        ParserMemoryLimitError,
    ) as exc:
        raise _parser_error(exc) from exc


@router.put("/{doc_id}/processing-scope/review")
def save_scope_review(
    doc_id: str, body: ScopeSelectionIn, user: User = Depends(require_role("editor", "admin"))
):
    _document(doc_id)
    try:
        with session_scope() as session:
            _require_editor(session, doc_id, body, user)
            row = save_review_selection(
                session,
                doc_id,
                body.review_id,
                body.expected_revision,
                frozenset(body.selected_source_keys),
                actor=user.username,
            )
            append_scope_audit(
                session, doc_id, user.username, "document_processing_scope_updated", row
            )
        return get_scope(doc_id, user)
    except ProcessingScopeConflict as exc:
        raise _conflict(exc) from exc


@router.post("/{doc_id}/processing-scope/confirm")
def confirm_scope(
    doc_id: str, body: ScopeConfirmIn, user: User = Depends(require_role("editor", "admin"))
):
    _document(doc_id)
    try:
        result = confirm_processing_scope(
            doc_id,
            body.review_id,
            body.revision,
            body.request_id,
            actor=user.username,
            is_admin="admin" in user.roles,
            similarity_token=body.similarity_token,
            edit_owner_id=user.user_id,
            edit_token=body.edit_token,
        )
        if result.status == "similar":
            return JSONResponse(status_code=409, content=result.detail)
        response = DocumentOut.model_validate(result.document).model_dump(mode="json")
        return JSONResponse(
            status_code=200 if result.status == "already_accepted" else 202, content=response
        )
    except ProcessingScopeConflict as exc:
        raise _conflict(exc) from exc
    except DomainError as exc:
        from app.api import errors

        raise errors.domain_error(exc, 503 if exc.code == "queue_overloaded" else 400) from exc
    except (
        ParserBusyError,
        ParserIsolationError,
        ParserTimeoutError,
        ParserMemoryLimitError,
    ) as exc:
        raise _parser_error(exc) from exc


@router.delete("/{doc_id}/processing-scope/review")
def cancel_scope_review(
    doc_id: str, body: ScopeSelectionIn, user: User = Depends(require_role("editor", "admin"))
):
    document = _document(doc_id)
    try:
        with session_scope() as session:
            _require_editor(session, doc_id, body, user)
            row = locked_review(session, doc_id, body.review_id, body.expected_revision)
            if row.state in {"submitted", "validating"}:
                raise ProcessingScopeConflict("Обработка уже запущена или проверяется.")
            if document.get("has_published_version"):
                row.state = "canceled"
            else:
                row.state = "draft"
                row.revision += 1
                row.similarity_confirmation_json = None
        return get_scope(doc_id, user)
    except ProcessingScopeConflict as exc:
        raise _conflict(exc) from exc


@router.get("/{doc_id}/processing-scope/attachments/{source_key}")
def download_review_attachment(doc_id: str, source_key: str, user: User = Depends(require_user)):
    _document(doc_id)
    from app.services.generation_store import lock_document_write
    from app.services.generation_files import processing_review_paths, _reject_links
    from app.services.artifact_response import OpenedFileResponse
    from docparser import portable_name

    settings = get_settings()
    with session_scope() as session:
        if not lock_document_write(session, doc_id, allow_deleted=False):
            raise ApiError(
                status_code=404, code="document_not_found", detail="Документ недоступен."
            )
        row = session.get(DocumentProcessingReview, doc_id)
        node = (
            next((n for n in row.inventory_json["nodes"] if n["source_key"] == source_key), None)
            if row
            else None
        )
        if node is None or row.state == "canceled":
            raise ApiError(status_code=404, code="file_not_found", detail="Вложение недоступно.")
        name = node.get("saved_path")
        if not name:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "source_original_unavailable",
                    "detail": "Вложение доступно в исходном контейнере.",
                    "download_url": f"/api/documents/{doc_id}/download",
                },
            )
        root = processing_review_paths(settings, doc_id, row.id) / "attachments"
        path = root / name
        _reject_links(path)
        if Path(name).name != name or not path.resolve().is_relative_to(root.resolve()):
            raise ApiError(status_code=404, code="file_not_found", detail="Вложение недоступно.")
        if not path.is_file():
            return JSONResponse(
                status_code=409,
                content={
                    "code": "source_original_unavailable",
                    "detail": "Вложение доступно в исходном контейнере.",
                    "download_url": f"/api/documents/{doc_id}/download",
                },
            )
        return OpenedFileResponse(
            path,
            media_type="application/octet-stream",
            filename=portable_name(node["display_name"]),
        )


@router.get("/{doc_id}/processing-scope/attachments/{source_key}/preview")
def preview_review_attachment(
    doc_id: str, source_key: str,
    review_id: str = Query(min_length=1, max_length=64),
    sheet: int = Query(default=0, ge=0, le=255),
    user: User = Depends(require_user),
):
    """Preview the current review original, including excluded nested sources."""
    import hashlib
    import shutil

    from app.services.generation_store import lock_document_write
    from app.services.generation_files import processing_review_paths, _reject_links
    from app.services.parser_supervisor import parse_document_supervised

    _document(doc_id)
    settings = get_settings()
    with TemporaryDirectory(prefix="attachment-preview-") as folder:
        with session_scope() as session:
            if not lock_document_write(session, doc_id, allow_deleted=False):
                raise ApiError(status_code=404, code="document_not_found", detail="Документ недоступен.")
            row = session.get(DocumentProcessingReview, doc_id)
            if not row or row.state == "canceled":
                raise ApiError(status_code=404, code="file_not_found", detail="Вложение недоступно.")
            if row.id != review_id:
                raise ApiError(status_code=409, code="conflict", detail="Состав вложений обновился.")
            node = next((n for n in row.inventory_json["nodes"] if n["source_key"] == source_key), None)
            if not node:
                raise ApiError(status_code=404, code="file_not_found", detail="Вложение недоступно.")
            from docparser.attachment_preview import PREVIEW_KINDS
            kind = node['kind']
            groups = {'mail': {'eml', 'msg'}, 'archive': {'zip', '7z', 'rar'}}
            if kind in groups:
                extension = Path(node.get('saved_path') or '').suffix.lower()[1:]
                kind = extension if extension in groups[kind] else None
            if kind not in PREVIEW_KINDS or node.get('supported') is False:
                raise ApiError(status_code=422, code="invalid_request", detail="Формат вложения не поддерживает предпросмотр.")
            original = Path(folder) / ('original.' + kind)
            root = processing_review_paths(settings, doc_id, row.id) / "attachments"
            name = node.get("saved_path")
            if not name or Path(name).name != name:
                raise ApiError(status_code=404, code="file_not_found", detail="Оригинал вложения недоступен.")
            path = root / name
            _reject_links(path)
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                raise ApiError(status_code=404, code="file_not_found", detail="Оригинал вложения недоступен.")
            # Copy while holding the document lock; replacement/cleanup may then
            # proceed without deleting the worker's input or holding a DB lock.
            if path.stat().st_size > 50 * 1024 * 1024:
                raise ApiError(status_code=422, code="parser_resource_limit", detail="Вложение превышает лимит предпросмотра.")
            with path.open("rb") as src, original.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=65536)
            with original.open("rb") as stream:
                fingerprint = hashlib.file_digest(stream, "sha256").hexdigest()
            if node.get("content_fingerprint") != fingerprint:
                raise ApiError(status_code=409, code="conflict", detail="Оригинал вложения изменился.")
        try:
            result = parse_document_supervised(
                original, original.name, attachments_dir=Path(folder) / "worker",
                timeout_seconds=min(10, settings.parser_timeout_seconds),
                max_memory_mb=settings.parser_max_memory_mb,
                max_concurrent=settings.parser_max_concurrent,
                _inspection_options={"attachment_preview": {"kind": kind, "sheet_index": sheet}},
                archive_helper_path=settings.archive_7zip_path or None,
            )
        except (ParserBusyError, ParserIsolationError, ParserTimeoutError, ParserMemoryLimitError) as exc:
            raise _parser_error(exc) from exc
        except ParserWorkerError as exc:
            raise ApiError(status_code=422, code="parse_failed", detail="Не удалось показать это вложение.") from exc
    return JSONResponse(result.attachment_preview, headers={"Cache-Control": "no-store"})
