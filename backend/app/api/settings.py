"""Роут настроек чата: параметры выбора числа результатов и режимов поиска для UI."""
from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.models.schemas import ChatSettingsOut
from app.services.vector_store import SEARCH_MODES

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=ChatSettingsOut)
def get_chat_settings(settings: Settings = Depends(get_settings)) -> ChatSettingsOut:
    default_mode = settings.search_mode_default if settings.search_mode_default in SEARCH_MODES else "hybrid"
    return ChatSettingsOut(
        top_k_min=settings.chat_top_k_min,
        top_k_max=settings.chat_top_k_max,
        top_k_default=settings.chat_top_k_default,
        top_k_presets=settings.chat_top_k_presets,
        search_mode_default=default_mode,
        search_modes=list(SEARCH_MODES),
        search_index_chunks_enabled=settings.search_index_chunks_enabled,
        translation_provider=settings.translation_provider,
        translation_model=settings.translation_model or settings.llm_model,
    )
