"""Роут настроек чата: параметры выбора числа результатов и режимов поиска для UI."""
from fastapi import APIRouter, Depends, Response

from app.config import Settings, get_settings
from app.auth.models import User
from app.auth.service import require_user
from app.services.answer_scenarios import scenarios_available
from app.models.schemas import ChatSettingsOut
from app.services.vector_store import SEARCH_MODES

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/revisions")
def get_config_revisions(response: Response, settings: Settings = Depends(get_settings),
                         user: User = Depends(require_user)):
    from app.db.session import session_scope
    from app.services.work.admission import durable_documents
    from app.services.work.coordination import config_revision
    response.headers["Cache-Control"] = "private, no-store"
    if not durable_documents(settings):
        return {"enabled": False, "revisions": {}}
    with session_scope() as session:
        revision = config_revision(session, "references")
    # Opaque decimal strings avoid losing BIGINT precision in browser JSON.
    return {"enabled": True, "revisions": {"references": str(revision)}}


@router.get("", response_model=ChatSettingsOut)
def get_chat_settings(settings: Settings = Depends(get_settings), user: User = Depends(require_user)) -> ChatSettingsOut:
    default_mode = settings.search_mode_default if settings.search_mode_default in SEARCH_MODES else "hybrid"
    return ChatSettingsOut(
        source_assessment={"available": settings.source_assessment_enabled,
                           "default_enabled": settings.source_assessment_enabled and settings.source_assessment_default_enabled,
                           "sample_size": settings.source_assessment_sample_size},
        knowledge_profile=settings.knowledge_profile,
        scenarios_available=scenarios_available(user),
        response_modes=["documents","fast","full"] + (["scenario"] if scenarios_available(user) else []),
        top_k_min=settings.chat_top_k_min,
        top_k_max=settings.chat_top_k_max,
        top_k_default=settings.chat_top_k_default,
        top_k_presets=settings.chat_top_k_presets,
        search_mode_default=default_mode,
        search_modes=list(SEARCH_MODES),
        search_index_chunks_enabled=settings.search_index_chunks_enabled,
        glossary_query_expansion_enabled=settings.glossary_query_expansion_enabled,
        translation_provider=settings.translation_provider,
        translation_model=settings.translation_model or settings.llm_model,
        bulk_export_enabled=settings.bulk_export_enabled,
        bulk_export_download_enabled=settings.bulk_export_download_enabled,
        bulk_export_max_docs=settings.bulk_export_max_docs,
    )
