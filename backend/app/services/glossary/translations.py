"""Translation adapter for glossary terms.

Glossary translations are deliberately kept separate from the legacy reference
dictionary translator: a term is a pair of fields, and both fields must be
generated from the term's canonical source in one atomic operation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import DomainTerm, DomainTermTranslation
from app.db.session import session_scope
from app.services import audit
from app.services.glossary.normalization import GlossaryValidationError, validate_locale
from app.services.glossary.registry import GlossaryNotFoundError, GlossaryRegistry
from app.services.glossary.registry import invalidate_snapshot_cache
from app.services.glossary.mutation import glossary_write_session, bump_glossary_revision
from app.services.translation import translate_texts_batch

logger = logging.getLogger(__name__)

MAX_GLOSSARY_BACKFILL_TERMS = 10


class GlossaryTranslationConflictError(ValueError):
    """The caller or an in-flight translator used a stale source/version."""


class GlossaryTranslationValidationError(ValueError):
    """A glossary translation does not satisfy the public contract."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _username(user) -> str:
    return getattr(user, "username", None) or "anonymous"


def _user_id(user) -> str | None:
    return getattr(user, "user_id", None)


def _translation_dict(translation: DomainTermTranslation | None) -> dict | None:
    if translation is None:
        return None
    return {
        "term_id": translation.term_id,
        "locale": translation.locale,
        "display_name": translation.display_name,
        "description": translation.description,
        "source_revision": translation.source_revision,
        "version": translation.version,
        "is_machine_translated": translation.is_machine_translated,
        "reviewed_by": translation.reviewed_by,
        "reviewed_at": audit.iso_or_str(translation.reviewed_at),
        "updated_by": translation.updated_by,
        "updated_at": audit.iso_or_str(translation.updated_at),
    }


def _validate_target_locale(locale: str) -> str:
    try:
        return validate_locale(locale, allow_und=False)
    except GlossaryValidationError as exc:
        raise GlossaryTranslationValidationError(str(exc)) from exc


def _clean_name(value: str) -> str:
    if not isinstance(value, str):
        raise GlossaryTranslationValidationError("Перевод имени должен быть строкой")
    value = value.strip()
    if not value or len(value) > 256:
        raise GlossaryTranslationValidationError(
            "Перевод имени должен быть непустым и не длиннее 256 символов"
        )
    return value


def _clean_description(value: str | None) -> str | None:
    if value is not None and not isinstance(value, str):
        raise GlossaryTranslationValidationError("Перевод описания должен быть строкой или NULL")
    if value is not None and len(value) > 8000:
        raise GlossaryTranslationValidationError("Перевод описания не может быть длиннее 8000 символов")
    return value


def _candidate(term_id: int, locale: str) -> tuple | None:
    with session_scope() as session:
        term = session.get(DomainTerm, term_id)
        if term is None:
            return None
        translation = session.execute(
            select(DomainTermTranslation).where(
                DomainTermTranslation.term_id == term_id,
                DomainTermTranslation.locale == locale,
            )
        ).scalar_one_or_none()
        return (
            term.id,
            term.enabled,
            term.canonical_locale,
            term.original_name,
            term.original_description,
            term.source_revision,
            translation.version if translation else 0,
            translation.reviewed_by if translation else None,
            translation.is_machine_translated if translation else None,
            translation.source_revision if translation else None,
        )


def pending_glossary_term_ids(locale: str) -> list[int]:
    """Return the exact active-term set eligible for machine backfill."""
    locale = _validate_target_locale(locale)
    with session_scope() as session:
        terms = session.execute(
            select(DomainTerm)
            .where(DomainTerm.enabled.is_(True))
            .options(selectinload(DomainTerm.translations_rel))
            .order_by(DomainTerm.id)
        ).scalars().all()
        result: list[int] = []
        for term in terms:
            if term.canonical_locale == locale:
                continue
            translation = next(
                (item for item in term.translations_rel if item.locale == locale), None
            )
            if translation is None or (
                translation.is_machine_translated and not translation.reviewed_by
            ):
                result.append(term.id)
        return result


def _write_machine_translation(
    *,
    term_id: int,
    locale: str,
    source_revision: int,
    expected_version: int,
    display_name: str,
    description: str | None,
    user,
    ip_address: str | None,
) -> str:
    """Commit one result only if the source and target remained unchanged."""
    with glossary_write_session() as (session, state):
        term = session.get(DomainTerm, term_id)
        if term is None or not term.enabled or term.source_revision != source_revision:
            return "skipped_changed"
        translation = session.execute(
            select(DomainTermTranslation)
            .where(
                DomainTermTranslation.term_id == term_id,
                DomainTermTranslation.locale == locale,
            )
            .with_for_update()
        ).scalar_one_or_none()
        current_version = translation.version if translation else 0
        if translation is not None and translation.reviewed_by:
            return "skipped_reviewed"
        if current_version != expected_version:
            return "skipped_changed"

        old_value = _translation_dict(translation)
        if translation is None:
            translation = DomainTermTranslation(
                term_id=term_id,
                locale=locale,
                display_name=display_name,
                description=description,
                source_revision=source_revision,
                version=1,
                is_machine_translated=True,
                updated_by=_user_id(user),
            )
            session.add(translation)
        else:
            translation.display_name = display_name
            translation.description = description
            translation.source_revision = source_revision
            translation.version += 1
            translation.is_machine_translated = True
            translation.reviewed_by = None
            translation.reviewed_at = None
            translation.updated_by = _user_id(user)
        session.flush()
        audit.record_in_session(
            session,
            action_type=audit.GLOSSARY_TRANSLATION_UPDATE,
            user_id=_user_id(user),
            username=_username(user),
            target_type=audit.TARGET_GLOSSARY_TERM,
            target_id=str(term_id),
            old_value=old_value,
            new_value=_translation_dict(translation),
            ip_address=ip_address,
        )
        bump_glossary_revision(session, state)
        return "created" if old_value is None else "updated"


def backfill_glossary_translations(
    locale: str,
    term_ids: list[int],
    *,
    expected_translation_versions: dict[int, int] | None = None,
    user,
    ip_address: str | None = None,
) -> dict:
    """Translate at most ten selected terms, preserving concurrent human work."""
    locale = _validate_target_locale(locale)
    if len(term_ids) > MAX_GLOSSARY_BACKFILL_TERMS:
        raise ValueError("За один пакет можно передать не более 10 терминов")
    if len(set(term_ids)) != len(term_ids):
        raise GlossaryTranslationValidationError("В пакете не должно быть повторяющихся term_id")
    expected_translation_versions = expected_translation_versions or {}
    if any(version < 0 for version in expected_translation_versions.values()):
        raise GlossaryTranslationValidationError("Ожидаемая версия перевода не может быть отрицательной")

    result = {
        "locale": locale,
        "requested": len(term_ids),
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
    settings = get_settings()
    if settings.translation_provider == "off":
        result["status"] = "skipped_provider_off"
        audit.record(
            user,
            audit.GLOSSARY_TRANSLATION_BACKFILL,
            audit.TARGET_GLOSSARY_TERM,
            target_id=None,
            new_value=result,
            ip_address=ip_address,
        )
        return result

    for term_id in term_ids:
        candidate = _candidate(term_id, locale)
        if candidate is None:
            result["skipped_missing"] += 1
            continue
        (
            _id,
            enabled,
            canonical_locale,
            original_name,
            original_description,
            source_revision,
            translation_version,
            reviewed_by,
            is_machine,
            translation_source_revision,
        ) = candidate
        expected = expected_translation_versions.get(term_id)
        if expected is not None and expected != translation_version:
            result["skipped_expected_version"] += 1
            continue
        if not enabled:
            result["skipped_disabled"] += 1
            continue
        if canonical_locale == locale:
            result["skipped_same_locale"] += 1
            continue
        if reviewed_by:
            result["skipped_reviewed"] += 1
            continue

        try:
            texts = [original_name]
            if original_description is not None:
                texts.append(original_description)
            translated = translate_texts_batch(
                texts,
                locale,
                source_locale=canonical_locale,
                strict=True,
            )
            display_name = _clean_name(translated[0])
            description = _clean_description(translated[1] if original_description is not None else None)
            outcome = _write_machine_translation(
                term_id=term_id,
                locale=locale,
                source_revision=source_revision,
                expected_version=translation_version,
                display_name=display_name,
                description=description,
                user=user,
                ip_address=ip_address,
            )
            result[outcome] += 1
        except Exception as exc:
            logger.warning("Перевод термина %d не удался: %s", term_id, exc)
            result["failed"] += 1

    if result["failed"] or result["skipped_changed"] or result["skipped_reviewed"]:
        result["status"] = "partial"
    audit.record(
        user,
        audit.GLOSSARY_TRANSLATION_BACKFILL,
        audit.TARGET_GLOSSARY_TERM,
        target_id=None,
        new_value=result,
        ip_address=ip_address,
    )
    return result


def set_glossary_translation(
    term_id: int,
    locale: str,
    *,
    display_name: str,
    description: str | None,
    translation_version: int,
    source_revision: int,
    user,
    ip_address: str | None = None,
) -> dict:
    """Human edit with independent translation and source CAS guards."""
    locale = _validate_target_locale(locale)
    display_name = _clean_name(display_name)
    description = _clean_description(description)
    if translation_version < 0 or source_revision < 1:
        raise GlossaryTranslationValidationError("Некорректная версия перевода или исходника")

    with glossary_write_session() as (session, state):
        term = session.get(DomainTerm, term_id)
        if term is None:
            raise GlossaryNotFoundError(f"Термин не найден: {term_id}")
        if not term.enabled:
            raise GlossaryTranslationConflictError("Термин отключён")
        if term.source_revision != source_revision:
            raise GlossaryTranslationConflictError("Исходный текст термина уже изменён")
        translation = session.execute(
            select(DomainTermTranslation)
            .where(
                DomainTermTranslation.term_id == term_id,
                DomainTermTranslation.locale == locale,
            )
            .with_for_update()
        ).scalar_one_or_none()
        current_version = translation.version if translation else 0
        if current_version != translation_version:
            raise GlossaryTranslationConflictError("Версия перевода уже изменена")
        old_value = _translation_dict(translation)
        if translation is None:
            translation = DomainTermTranslation(
                term_id=term_id,
                locale=locale,
                display_name=display_name,
                description=description,
                source_revision=source_revision,
                version=1,
                is_machine_translated=False,
                reviewed_by=_username(user),
                reviewed_at=_now(),
                updated_by=_user_id(user),
            )
            session.add(translation)
        else:
            translation.display_name = display_name
            translation.description = description
            translation.source_revision = source_revision
            translation.version += 1
            translation.is_machine_translated = False
            translation.reviewed_by = _username(user)
            translation.reviewed_at = _now()
            translation.updated_by = _user_id(user)
        session.flush()
        audit.record_in_session(
            session,
            action_type=audit.GLOSSARY_TRANSLATION_UPDATE,
            user_id=_user_id(user),
            username=_username(user),
            target_type=audit.TARGET_GLOSSARY_TERM,
            target_id=str(term_id),
            old_value=old_value,
            new_value=_translation_dict(translation),
            ip_address=ip_address,
        )
        bump_glossary_revision(session, state)
    invalidate_snapshot_cache()
    return GlossaryRegistry().get(term_id)  # type: ignore[return-value]


def review_glossary_translation(
    term_id: int,
    locale: str,
    *,
    translation_version: int,
    source_revision: int,
    user,
    ip_address: str | None = None,
) -> dict:
    """Confirm a machine translation without changing its text."""
    locale = _validate_target_locale(locale)
    with glossary_write_session() as (session, state):
        term = session.get(DomainTerm, term_id)
        if term is None:
            raise GlossaryNotFoundError(f"Термин не найден: {term_id}")
        if not term.enabled:
            raise GlossaryTranslationConflictError("Термин отключён")
        if term.source_revision != source_revision:
            raise GlossaryTranslationConflictError("Исходный текст термина уже изменён")
        translation = session.execute(
            select(DomainTermTranslation)
            .where(
                DomainTermTranslation.term_id == term_id,
                DomainTermTranslation.locale == locale,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if translation is None or translation.version != translation_version:
            raise GlossaryTranslationConflictError("Версия перевода уже изменена или перевод отсутствует")
        if translation.source_revision != term.source_revision:
            raise GlossaryTranslationConflictError("Перевод относится к прежнему исходному тексту")
        if translation.reviewed_by:
            pass
        else:
            old_value = _translation_dict(translation)
            translation.reviewed_by = _username(user)
            translation.reviewed_at = _now()
            translation.is_machine_translated = False
            translation.version += 1
            translation.updated_by = _user_id(user)
            session.flush()
            audit.record_in_session(
                session,
                action_type=audit.GLOSSARY_TRANSLATION_REVIEW,
                user_id=_user_id(user),
                username=_username(user),
                target_type=audit.TARGET_GLOSSARY_TERM,
                target_id=str(term_id),
                old_value=old_value,
                new_value=_translation_dict(translation),
                ip_address=ip_address,
            )
            bump_glossary_revision(session, state)
    invalidate_snapshot_cache()
    return GlossaryRegistry().get(term_id)  # type: ignore[return-value]
