"""Автоопределение номера разработки (Этап 4).

Каскад:
  1. Regex по имени файла (синхронно, без LLM) — confidence 0.9;
  2. LLM с титульного листа (голова markdown) — confidence 0.6.

Сопоставление со справочником разработок:
  - точное совпадение по number → matched, development_id;
  - fuzzy-совпадение по name (difflib ratio >= dev_fuzzy_name_threshold);
  - нет совпадения → development_id=None, кандидат (number/name/module) кладётся
    в documents.development_suggestion — документ попадает в «требует уточнения».

422-безопасность: детектор НИКОГДА не вызывает DevelopmentRegistry.create/update,
поэтому module, отсутствующий в attribute_values, не порождает DevelopmentModuleError
(422) и не роняет пайплайн/загрузку документа. Кандидат (включая module) хранится
как suggestion; создание разработки с валидацией module — отдельное действие
пользователя через POST /developments.
"""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field

from app.config import get_settings
from app.prompts.store import get_store
from app.services.development_registry import get_development_registry
from app.services.llm_client import LLMClient
from app.services.registry import get_registry

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    number: str | None = None
    name: str | None = None
    module: str | None = None
    development_id: int | None = None
    confidence: float | None = None
    matched: bool = False
    source: str | None = None  # "filename" | "llm"
    suggestion: dict | None = None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def extract_number_from_filename(filename: str) -> str | None:
    """Извлекает номер разработки из имени файла по config.dev_filename_pattern."""
    pattern = get_settings().dev_filename_pattern
    if not pattern:
        return None
    try:
        m = re.search(pattern, filename or "")
    except re.error:
        logger.warning("Некорректный dev_filename_pattern: %r", pattern)
        return None
    return m.group(1).strip() if m else None


def _extract_from_llm(text: str, filename: str, doc_id: str) -> dict | None:
    """LLM-извлечение {dev_number, dev_name, module} с титульного листа. Non-fatal."""
    try:
        store = get_store()
        system = store.get("dev_number_system")
        user = store.format("dev_number_user", filename=filename, content=text)
        result = LLMClient().chat_json(system, user, doc_id=doc_id, chunk_idx=0)
        if isinstance(result, list):
            result = result[0] if result else {}
        if not isinstance(result, dict):
            return None
        return result
    except Exception:
        logger.warning("[%s] LLM-извлечение номера разработки не удалось", doc_id, exc_info=True)
        return None


def match_reference(candidate: dict) -> dict | None:
    """Сопоставляет кандидата со справочником: точное число → fuzzy название."""
    reg = get_development_registry()
    number = (candidate.get("number") or "").strip()
    name = _normalize(candidate.get("name") or "")

    if number:
        dev = reg.find_by_number(number)
        if dev:
            return dev

    if name:
        threshold = get_settings().dev_fuzzy_name_threshold
        best: dict | None = None
        best_ratio = 0.0
        for dev in reg.list():
            ratio = difflib.SequenceMatcher(None, name, _normalize(dev["name"])).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best = dev
        if best is not None and best_ratio >= threshold:
            return best
    return None


def detect(markdown: str, filename: str, doc_id: str = "unknown") -> Detection:
    """Полный каскад автоопределения. markdown='' → только regex по имени файла."""
    settings = get_settings()
    if not settings.dev_detection_enabled:
        return Detection()

    candidate: dict | None = None
    source: str | None = None
    confidence: float | None = None

    number = extract_number_from_filename(filename)
    if number:
        candidate = {"number": number, "name": None, "module": None}
        source = "filename"
        confidence = 0.9
    elif settings.dev_llm_title_page_enabled and markdown:
        llm = _extract_from_llm(markdown[: settings.dev_title_page_chars], filename, doc_id)
        if llm and (llm.get("dev_number") or llm.get("dev_name")):
            candidate = {
                "number": (llm.get("dev_number") or "").strip() or None,
                "name": (llm.get("dev_name") or "").strip() or None,
                "module": (llm.get("module") or "").strip() or None,
            }
            source = "llm"
            confidence = 0.6

    if not candidate:
        return Detection()

    dev = match_reference(candidate)
    if dev is not None:
        return Detection(
            number=candidate.get("number"),
            name=candidate.get("name"),
            module=candidate.get("module"),
            development_id=dev["id"],
            confidence=confidence,
            matched=True,
            source=source,
        )

    return Detection(
        number=candidate.get("number"),
        name=candidate.get("name"),
        module=candidate.get("module"),
        confidence=confidence,
        matched=False,
        source=source,
        suggestion={
            "number": candidate.get("number"),
            "name": candidate.get("name"),
            "module": candidate.get("module"),
        },
    )


def attach_development(doc_id: str, detection: Detection) -> None:
    """Записывает результат автоопределения в документ (development_id/confidence/suggestion)."""
    fields: dict = {"development_confidence": detection.confidence}
    if detection.development_id is not None:
        fields["development_id"] = detection.development_id
    fields["development_suggestion"] = detection.suggestion
    get_registry().update(doc_id, **fields)
