"""Автоперевод справочников (теги/разработки/атрибуты) — Этап 7 фаза B.

Провайдеры: `llm` (через существующий llm_client, bulk-семафор LLMClient(
interactive=False) — интерактивный чат сохраняет приоритет), `off` (без
LLM — переводы вносятся вручную или пастой из файла, см. translations-параметр
бэкфилла). Ручной перевод не перезаписывается бэкфиллом (идемпотентность):
пропускаются переводы с reviewed_by, машинные без review — обновляются.

Отклонение от плана: бэкфилл выполняется СИНХРОННО в admin-запросе, а не через
job queue — очередь массовых операций (job_queue.py) документоцентрична
(submit(doc_ids), _execute по doc_id), а переводы — по справочникам (сотни
строк). Операция не деструктивна и обратима через review; LLM-нагрузка ограничена
bulk-семафором (не конкурирует с чатом). При необходимости переносится в очередь.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import get_settings
from app.db.models import (
    AttributeValue,
    AttributeValueTranslation,
    Development,
    DevelopmentTranslation,
    Tag,
    TagTranslation,
)
from app.db.session import session_scope
from app.services import audit

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a technical translator. Translate each given term/name from {source} "
    "to {target}. Preserve abbreviations, codes and identifiers verbatim where "
    "they are proper nouns (e.g. СЭДО, ЭЛН, SAP HCM, LK_STAT). Return a JSON "
    "array of strings, one per input, in the same order — nothing else."
)


def translate_texts_batch(
    texts: list[str],
    target_locale: str,
    *,
    source_locale: str = "und",
    strict: bool = False,
) -> list[str]:
    """Машинный перевод пакета текстов. Возвращает список той же длины/порядка.

    `off`-провайдер → пустые строки (переводы не создаются).
    """
    settings = get_settings()
    if settings.translation_provider == "off":
        return [""] * len(texts)
    if not texts:
        return []
    from app.services.llm_client import LLMClient

    model = settings.translation_model or settings.llm_model
    client = LLMClient(interactive=False, model=model)  # bulk-семафор, чат не блокируется
    source = source_locale if source_locale != "und" else "its original language (identify it from the input)"
    system = _SYSTEM_PROMPT.format(source=source, target=target_locale)
    user = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    result = client.chat_json(system, user, doc_id="translation", chunk_idx=0)
    if not isinstance(result, list):
        raise ValueError("LLM-переводчик вернул не массив")
    if strict and any(not isinstance(x, str) for x in result):
        raise ValueError("Строгий переводчик ожидает только строки")
    out = [str(x) for x in result]
    if len(out) != len(texts):
        raise ValueError(
            f"LLM-переводчик вернул {len(out)} переводов на {len(texts)} текстов"
        )
    if strict:
        if not out or not out[0].strip() or len(out[0]) > 256:
            raise ValueError("Строгий переводчик вернул пустое или слишком длинное имя")
        if any(len(value) > 8000 for value in out):
            raise ValueError("Строгий переводчик вернул слишком длинное поле")
    return out


def _pending_rows(entity: str, locale: str) -> list[tuple[int, str, str]]:
    """[(entity_id, original_text, original_locale)] без РУЧНОГО перевода в locale
    (reviewed_by IS NULL; машинный без review переводится/обновляется).

    Единственный источник «что обработает бэкфилл»: count_pending и _backfill_*
    используют его, чтобы число preview совпадало с реальным прогоном.
    """
    with session_scope() as s:
        if entity == "tags":
            rows = s.execute(
                select(Tag.id, Tag.canonical_text, Tag.canonical_locale, TagTranslation.reviewed_by)
                .outerjoin(
                    TagTranslation,
                    (TagTranslation.tag_id == Tag.id) & (TagTranslation.locale == locale),
                )
                .order_by(Tag.id)
            ).all()
        elif entity == "developments":
            rows = s.execute(
                select(Development.id, Development.name, Development.canonical_locale, DevelopmentTranslation.reviewed_by)
                .outerjoin(
                    DevelopmentTranslation,
                    (DevelopmentTranslation.development_id == Development.id)
                    & (DevelopmentTranslation.locale == locale),
                )
                .order_by(Development.id)
            ).all()
        elif entity == "attributes":
            rows = s.execute(
                select(AttributeValue.id, AttributeValue.label, AttributeValue.canonical_locale, AttributeValueTranslation.reviewed_by)
                .outerjoin(
                    AttributeValueTranslation,
                    (AttributeValueTranslation.attribute_value_id == AttributeValue.id)
                    & (AttributeValueTranslation.locale == locale),
                )
                .where(AttributeValue.label.isnot(None))
                .order_by(AttributeValue.id)
            ).all()
        else:
            raise ValueError(f"Неизвестная сущность справочника: {entity}")
    return [(entity_id, text, source) for entity_id, text, source, reviewed in rows
            if reviewed is None and source != locale]


def backfill_reference_data(
    locale: str,
    entities: list[str],
    *,
    translations: dict[str, str] | None = None,
    user,
    ip_address: str | None = None,
    glossary_term_ids: list[int] | None = None,
    expected_translation_versions: dict[int, int] | None = None,
) -> dict:
    """Заполняет переводы справочников для `locale`. Возвращает счётчики.

    `translations` — ручной словарь {канонический_текст: перевод} (file/offline
    режим); если задан, LLM не вызывается. Каждая сущность без ручного перевода
    получает перевод (LLM-батч или из словаря). Идемпотентно.
    """
    settings = get_settings()
    # Диагностика маршрута ДО отправки справочников во внешний LLM (runbook/CLI):
    # оператор обязан видеть фактический провайдер/модель, а не кодовый фолбэк.
    logger.info(
        "Translation backfill: locale=%s entities=%s provider=%s model=%s",
        locale,
        entities,
        settings.translation_provider,
        settings.translation_model or settings.llm_model,
    )
    result: dict = {}
    username = getattr(user, "username", None) or "anonymous"
    total_created = 0

    if "tags" in entities:
        created, failed = _backfill_tags(locale, translations, username)
        result["tags"] = {"created": created, "failed": failed}
        total_created += created
    if "developments" in entities:
        created, failed = _backfill_developments(locale, translations, username)
        result["developments"] = {"created": created, "failed": failed}
        total_created += created
    if "attributes" in entities:
        created, failed = _backfill_attributes(locale, translations, username)
        result["attributes"] = {"created": created, "failed": failed}
        total_created += created

    if "glossary" in entities:
        from app.services.glossary.translations import (
            backfill_glossary_translations,
            pending_glossary_term_ids,
        )

        ids = glossary_term_ids or pending_glossary_term_ids(locale)
        summary = {
            "requested": 0,
            "created": 0,
            "updated": 0,
            "failed": 0,
            "skipped_changed": 0,
            "skipped_reviewed": 0,
            "skipped_same_locale": 0,
            "skipped_disabled": 0,
            "skipped_missing": 0,
            "skipped_expected_version": 0,
            "status": "completed",
        }
        for start in range(0, len(ids), 10):
            batch_ids = ids[start : start + 10]
            batch = backfill_glossary_translations(
                locale,
                batch_ids,
                expected_translation_versions={
                    term_id: expected_translation_versions[term_id]
                    for term_id in batch_ids
                    if expected_translation_versions and term_id in expected_translation_versions
                },
                user=user,
                ip_address=ip_address,
            )
            for key in summary:
                if key == "status":
                    if batch[key] != "completed":
                        summary[key] = "partial" if batch[key] == "partial" else batch[key]
                else:
                    summary[key] += batch[key]
        result["glossary"] = summary
        total_created += summary["created"] + summary["updated"]

    if total_created:
        audit.record(
            user,
            audit.TRANSLATIONS_BACKFILL,
            audit.TARGET_LOCALE,
            target_id=locale,
            new_value={"entities": entities, "result": result},
            ip_address=ip_address,
        )
    return result


def count_pending(locale: str, entities: list[str]) -> dict:
    """Число объектов справочника без РУЧНОГО перевода в locale — по сущностям.

    Использует тот же `_pending_rows`, что и бэкфилл: preview (сколько будет
    переведено) гарантированно совпадает с реальным прогоном.
    """
    result: dict = {}
    for ent in entities:
        if ent in ("tags", "developments", "attributes"):
            result[ent] = len(_pending_rows(ent, locale))
        elif ent == "glossary":
            from app.services.glossary.translations import pending_glossary_term_ids

            result[ent] = len(pending_glossary_term_ids(locale))
    return result


def _resolve_translation(text: str, translations: dict[str, str] | None) -> str | None:
    if translations is None:
        return None
    return translations.get(text)


def _backfill_tags(locale: str, translations: dict[str, str] | None, username: str) -> tuple[int, int]:
    targets = _pending_rows("tags", locale)
    if not targets:
        return 0, 0
    created = 0
    failed = 0
    for tid, text, source in targets:
        tr = _resolve_translation(text, translations) if translations is not None else None
        is_machine = tr is None
        if tr is None:
            try:
                tr = translate_texts_batch([text], locale, source_locale=source)[0]
            except Exception as exc:
                logger.warning("Перевод тега %d не удался: %s", tid, exc)
                failed += 1
                continue
        if not tr:
            failed += 1
            continue
        with session_scope() as s:
            existing = s.execute(
                select(TagTranslation).where(
                    TagTranslation.tag_id == tid, TagTranslation.locale == locale
                )
            ).scalar_one_or_none()
            if existing is None:
                s.add(TagTranslation(tag_id=tid, locale=locale, text=tr, is_machine_translated=is_machine))
            else:
                existing.text = tr
                existing.is_machine_translated = is_machine
        created += 1
    return created, failed


def _backfill_developments(locale: str, translations: dict[str, str] | None, username: str) -> tuple[int, int]:
    targets = _pending_rows("developments", locale)
    created, failed = 0, 0
    if not targets:
        return 0, 0
    for did, name, source in targets:
        tr = _resolve_translation(name, translations) if translations is not None else None
        is_machine = tr is None
        if tr is None:
            try:
                tr = translate_texts_batch([name], locale, source_locale=source)[0]
            except Exception as exc:
                logger.warning("Перевод разработки %d не удался: %s", did, exc)
                failed += 1
                continue
        if not tr:
            failed += 1
            continue
        with session_scope() as s:
            existing = s.execute(
                select(DevelopmentTranslation).where(
                    DevelopmentTranslation.development_id == did,
                    DevelopmentTranslation.locale == locale,
                )
            ).scalar_one_or_none()
            if existing is None:
                s.add(DevelopmentTranslation(development_id=did, locale=locale, name=tr, is_machine_translated=is_machine))
            else:
                existing.name = tr
                existing.is_machine_translated = is_machine
        created += 1
    return created, failed


def _backfill_attributes(locale: str, translations: dict[str, str] | None, username: str) -> tuple[int, int]:
    targets = _pending_rows("attributes", locale)
    created, failed = 0, 0
    if not targets:
        return 0, 0
    for aid, label, source in targets:
        tr = _resolve_translation(label, translations) if translations is not None else None
        is_machine = tr is None
        if tr is None:
            try:
                tr = translate_texts_batch([label], locale, source_locale=source)[0]
            except Exception as exc:
                logger.warning("Перевод атрибута %d не удался: %s", aid, exc)
                failed += 1
                continue
        if not tr:
            failed += 1
            continue
        with session_scope() as s:
            existing = s.execute(
                select(AttributeValueTranslation).where(
                    AttributeValueTranslation.attribute_value_id == aid,
                    AttributeValueTranslation.locale == locale,
                )
            ).scalar_one_or_none()
            if existing is None:
                s.add(AttributeValueTranslation(attribute_value_id=aid, locale=locale, label=tr, is_machine_translated=is_machine))
            else:
                existing.label = tr
                existing.is_machine_translated = is_machine
        created += 1
    return created, failed
