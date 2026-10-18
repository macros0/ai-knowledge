"""Runtime-override UI-словарей (Этап 7 фаза C).

Админ загружает JSON-словарь (частичное подмножество ключей канонического
ru-словаря) для локали — значения перезаписывают версионированный словарь релиза
без redeploy. Валидация: ключи ⊆ канонического манифеста (backend/app/i18n/
ui_keys.json, генерируется node-скриптом frontend/scripts/export-ui-keys.mjs),
{param}-плейсхолдеры каждого ключа совпадают с ru. Импорт двухшаговый
(preview → confirm), версии хранятся целиком, rollback — указатель версии в
locales.ui_dictionary_version.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import Locale, UiDictionary
from app.db.session import session_scope
from app.services import audit
from app import error_codes as codes
from app.services.errors import NotFoundError
from app.services.reference_resources import ReferenceKey
from app.services.reference_concurrency import (ReferenceError, lock_reference_resources, bump_reference_revision,
    reference_preview_digest)
from app.services.reference_mutation import guard_reference_writes

logger = logging.getLogger(__name__)

_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "i18n" / "ui_keys.json"
# Полный en-словарь для автосида при активации языка без runtime-override
# (фаза 2, 08.09.2026). Генерируется node-скриптом export-ui-keys.mjs вместе с
# ui_keys.json; дрейф ловится frontend/test/i18n.test.mjs.
_EN_SOURCE_PATH = Path(__file__).resolve().parents[1] / "i18n" / "ui_en.json"
_PARAM_RE = re.compile(r"\{(\w+)\}")


@lru_cache(maxsize=1)
def _english_messages() -> dict:
    return json.loads(_EN_SOURCE_PATH.read_text(encoding="utf-8"))


def localized_message(key: str, locale: str, *, russian_fallback: str) -> str:
    """Resolve a plain server message through the existing UI dictionary."""
    override = get_active(locale)
    value = (override or {}).get('data', {}).get(key)
    if isinstance(value, str) and value:
        return value
    if locale == 'ru':
        return russian_fallback
    return _english_messages().get(key, russian_fallback)


@lru_cache(maxsize=1)
def canonical_manifest() -> dict[str, list[str]]:
    """Канонический набор ключей → список {param}-плейсхолдеров (без count)."""
    if not _MANIFEST_PATH.is_file():
        return {}
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def _params_of(value) -> list[str]:
    found: set[str] = set()

    def scan(text: str) -> None:
        for m in _PARAM_RE.finditer(text):
            found.add(m.group(1))

    if isinstance(value, str):
        scan(value)
    elif isinstance(value, dict):
        for form in value.values():
            if isinstance(form, str):
                scan(form)
    found.discard("count")  # неявный параметр плюрала
    return sorted(found)


# Наборы plural-форм по целевой локали (Этап 7, валидация override).
#   allowed  — какие ключи форм допустимы в объекте;
#   required — какие обязаны присутствовать (иначе runtime translatePlural вернёт
#              сам ключ вместо строки: для en отсутствие `other` ломает n≠1,
#              для ru отсутствие one/few/many ломает склонения).
# en (и прочие не-ru/uk) — CLDR {one, other}; ru/uk — {one, few, many, other},
# но `other` не обязателен (pluralForm для ru/uk никогда его не возвращает).
_LOCALE_FORMS = {
    "ru": ({"one", "few", "many", "other"}, {"one", "few", "many"}),
    "uk": ({"one", "few", "many", "other"}, {"one", "few", "many"}),
}
_DEFAULT_FORMS = ({"one", "other"}, {"one", "other"})


def _locale_forms(locale: str | None) -> tuple[set[str], set[str]]:
    return _LOCALE_FORMS.get((locale or "").lower().split("-")[0], _DEFAULT_FORMS)


def validate(locale: str, data) -> list[str]:
    """Валидирует override-словарь для `locale`: список ошибок (пусто — ок).

    Требования:
      - data — dict; ключи ⊆ канонического манифеста;
      - {param}-набор (кроме неявного `count`) совпадает с ru для каждой строки/
        plural-формы;
      - plural-значение — объект, формы которого соответствуют целевой локали
        (для en — `one, other`; для ru — `one, few, many`; `other` допустим).
        Строковое значение допустимо всегда (en.js часто представляет plural-ключ
        одной строкой с `{count}`).
    """
    if not isinstance(data, dict):
        return ["Словарь должен быть JSON-объектом (ключ → значение)"]
    manifest = canonical_manifest()
    allowed, required = _locale_forms(locale)
    errors: list[str] = []
    for key, value in data.items():
        if key not in manifest:
            errors.append(f"Неизвестный ключ: {key}")
            continue
        canonical = list(manifest[key])

        if isinstance(value, str):
            params = _params_of(value)
            if params != canonical:
                errors.append(
                    f"Ключ '{key}': параметры {params} не совпадают с ru {canonical}"
                )
            continue

        if isinstance(value, dict):
            forms = set(value.keys())
            wrong = sorted(f for f in forms if f not in allowed)
            if wrong:
                errors.append(
                    f"Ключ '{key}' содержит plural-формы {', '.join(wrong)}. "
                    f"Для locale {locale} ожидаются {', '.join(sorted(allowed))}."
                )
            missing = sorted(f for f in required if f not in forms)
            if missing:
                errors.append(
                    f"Ключ '{key}' не содержит обязательные для locale {locale} "
                    f"формы: {', '.join(missing)}."
                )
            for form, template in value.items():
                if not isinstance(template, str):
                    errors.append(f"Ключ '{key}': форма '{form}' должна быть строкой.")
                    continue
                params = _params_of(template)
                if params != canonical:
                    errors.append(
                        f"Ключ '{key}' (форма '{form}'): параметры {params} "
                        f"не совпадают с ru {canonical}"
                    )
            continue

        errors.append(
            f"Ключ '{key}': значение должно быть строкой или объектом plural-форм."
        )
    return errors


def get_active(locale: str) -> dict | None:
    """Активный override-словарь локали (по locales.ui_dictionary_version)."""
    with session_scope() as s:
        loc = s.get(Locale, locale)
        if loc is None or loc.ui_dictionary_version is None:
            return None
        row = s.execute(
            select(UiDictionary).where(
                UiDictionary.locale == locale,
                UiDictionary.version == loc.ui_dictionary_version,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "locale": locale,
            "version": row.version,
            "data": row.data,
            "note": row.note,
            "uploaded_by": row.uploaded_by,
            "created_at": row.created_at,
        }


_EXPECTED_UNSET = object()


def _active_in_session(session, locale, loc):
    if loc.ui_dictionary_version is None:
        return None
    return session.scalar(select(UiDictionary).where(UiDictionary.locale == locale,
        UiDictionary.version == loc.ui_dictionary_version))


def get_admin_snapshot(locale):
    key = ReferenceKey("ui_dictionary", locale)
    with session_scope() as session:
        state = lock_reference_resources(session, [key])[key]
        loc = session.get(Locale, locale, populate_existing=True)
        if loc is None:
            raise ValueError(f"Язык '{locale}' не найден")
        active = _active_in_session(session, locale, loc)
        return {"locale": locale, "reference_revision": state.revision, "version": active.version if active else None,
            "data": active.data if active else None, "note": active.note if active else None,
            "uploaded_by": active.uploaded_by if active else None, "created_at": active.created_at if active else None}


def _dictionary_fence(session, locale, reference_context=None, *, write=False):
    key, locale_key = ReferenceKey("ui_dictionary", locale), ReferenceKey("locale", locale)
    states = lock_reference_resources(session, [locale_key, key])
    loc = session.get(Locale, locale, populate_existing=True)
    if loc is None:
        raise ValueError(f"Язык '{locale}' не найден")
    state = guard_reference_writes(session, [key], reference_context)[key] if write else states[key]
    return loc, state, states[locale_key]


def _confirm_dictionary(context, digest, actual_digest, expected_active, current_active):
    if context is not None and (not digest or expected_active is _EXPECTED_UNSET):
        raise ReferenceError("reference_precondition_required", 428)
    if expected_active is not _EXPECTED_UNSET:
        if expected_active is not None and (type(expected_active) is not int or expected_active < 1):
            raise ReferenceError("reference_precondition_required", 428)
        if expected_active != current_active:
            raise ReferenceError("version_conflict")
    if digest is not None and digest != actual_digest:
        raise ReferenceError("reference_preview_stale")


def _dictionary_audit(session, user, action, locale, version, ip_address, **meta):
    if user is not None:
        audit.record_in_session(session, action_type=action, user_id=getattr(user, "user_id", None),
            username=getattr(user, "username", None), target_type=audit.TARGET_LOCALE, target_id=locale,
            new_value={"version": version}, ip_address=ip_address, meta=meta)


def import_dictionary(locale: str, data: dict, note: str | None, uploaded_by: str | None, *, confirm=False,
    activate=True, user=None, ip_address=None, reference_context=None, preview_digest=None,
    expected_active_version=_EXPECTED_UNSET, only_if_empty=False) -> dict:
    errors = validate(locale, data)
    if errors:
        return {"errors": errors, "applied": False}
    with session_scope() as session:
        loc, state, locale_state = _dictionary_fence(session, locale, reference_context, write=confirm)
        current = _active_in_session(session, locale, loc)
        current_keys = set(current.data) if current else set()
        new_keys = set(data)
        preview = {"total": len(new_keys), "added": sorted(new_keys - current_keys),
            "removed": sorted(current_keys - new_keys), "unchanged": sorted(new_keys & current_keys)}
        digest = reference_preview_digest({"operation": "import", "locale": locale, "revision": state.revision,
            "active_version": loc.ui_dictionary_version, "data": data, "note": note, "activate": activate})
        result = {"errors": [], "applied": False, "preview": preview, "preview_digest": digest,
            "reference_revision": state.revision, "active_version": loc.ui_dictionary_version}
        if not confirm:
            return result
        _confirm_dictionary(reference_context, preview_digest, digest, expected_active_version, loc.ui_dictionary_version)
        max_version = session.scalar(select(func.max(UiDictionary.version)).where(UiDictionary.locale == locale))
        if only_if_empty and max_version is not None:
            return {**result, "reason": "already_exists"}
        next_version = (max_version or 0) + 1
        session.add(UiDictionary(locale=locale, version=next_version, data=data, note=note, uploaded_by=uploaded_by))
        bump_reference_revision(state)
        if activate:
            loc.ui_dictionary_version = next_version
            bump_reference_revision(locale_state)
        _dictionary_audit(session, user, audit.UI_DICTIONARY_IMPORT, locale, next_version, ip_address,
            note=note, total=len(new_keys), added=len(preview["added"]), removed=len(preview["removed"]), activated=activate)
        return {**result, "applied": True, "version": next_version, "reference_revision": state.revision,
            "active_version": loc.ui_dictionary_version}


def _has_versions(locale: str) -> bool:
    """Есть ли у локали хоть одна версия словаря (активная или нет)."""
    with session_scope() as s:
        row = s.execute(
            select(UiDictionary.id).where(UiDictionary.locale == locale).limit(1)
        ).first()
    return row is not None


def seed_english_copy(locale: str, *, user=None, ip_address: str | None = None) -> bool:
    """Автосид en-копии при активации языка без словаря (фаза 2).

    Модель «активный ≠ UI-язык» (08.09.2026): язык можно активировать без релиза
    со словарём. Чтобы редактор «Перевод интерфейса» имел отправную точку для
    перевода на целевой язык, при активации засевается ПОЛНЫЙ en-словарь
    (ui_en.json): английский — международный язык-источник.

    Версия создаётся НЕАКТИВНОЙ (`activate=False`) — она заготовка для
    редактора, а не работающий override. Активный override — САМЫЙ ВЕРХНИЙ слой
    (`getMessages`: {...versioned, ...override}), поэтому полный en-снапшот
    заморозил бы английский текст на момент активации: улучшенная в следующем
    релизе формулировка ключа до de/fr уже не дошла бы (новые ключи протекают —
    их в снапшоте нет, а изменённые маскируются навсегда). Без активного
    override de/fr продолжают следовать версионированному словарю релиза, а
    админ включает заготовку одним откатом на неё, когда начинает перевод.

    Контракт — любая неудача = warning + False, результат активации НЕ затронут:
      - ru/en       → False (ru — fallback-источник; en — идентичная копия, бессмысленна);
      - есть версия словаря → False (идемпотентность: не сеем поверх, в том
        числе поверх собственной прошлой заготовки при повторной активации);
      - applied=False (ошибки валидации: ru/uk-модель плюралов и т.п.) → warning + False;
      - IntegrityError по unique(locale, version) — гонка параллельной активации:
        оба конкурента вычислили одну версию, проигравшего режет БД-констрейнт
        (не дублируется и audit: он пишется только после успешного коммита) →
        warning + False;
      - прочие Exception → warning + exc_info + False.
    """
    if locale in ("ru", "en"):
        return False
    if _has_versions(locale):
        return False
    if not _EN_SOURCE_PATH.is_file():
        logger.warning("Автосид en-копии для '%s' пропущен: нет %s", locale, _EN_SOURCE_PATH)
        return False
    try:
        data = json.loads(_EN_SOURCE_PATH.read_text(encoding="utf-8"))
        uploaded_by = user.username if user is not None else "system"
        result = import_dictionary(
            locale,
            data,
            note="auto: en copy on activation",
            uploaded_by=uploaded_by,
            confirm=True,
            activate=False,
            only_if_empty=True,
            user=user,
            ip_address=ip_address,
        )
    except Exception:
        logger.warning(
            "Автосид en-копии для '%s' не выполнен (конкурентная активация "
            "или ошибка БД) — заготовка для редактора не создана",
            locale,
            exc_info=True,
        )
        return False
    if result.get("reason") == "already_exists":
        return False
    if not result.get("applied"):
        logger.warning(
            "Автосид en-копии для '%s' отклонён валидацией: %s",
            locale,
            result.get("errors"),
        )
        return False
    return True


def history(locale: str) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(
            select(UiDictionary)
            .where(UiDictionary.locale == locale)
            .order_by(UiDictionary.version.desc())
        ).scalars().all()
    return [
        {
            "id": r.id,
            "version": r.version,
            "note": r.note,
            "uploaded_by": r.uploaded_by,
            "created_at": r.created_at,
            "key_count": len(r.data or {}),
        }
        for r in rows
    ]


def rollback(locale: str, version_id: int, *, user=None, ip_address=None, confirm=True,
    reference_context=None, preview_digest=None, expected_active_version=_EXPECTED_UNSET) -> dict:
    with session_scope() as session:
        loc, state, locale_state = _dictionary_fence(session, locale, reference_context, write=confirm)
        row = session.get(UiDictionary, version_id)
        if row is None or row.locale != locale:
            raise NotFoundError("Версия словаря не найдена", code=codes.DICT_VERSION_NOT_FOUND)
        digest = reference_preview_digest({"operation": "rollback", "locale": locale, "entry_id": version_id,
            "target_version": row.version, "target_data": row.data, "revision": state.revision,
            "active_version": loc.ui_dictionary_version})
        current = _active_in_session(session, locale, loc)
        old_keys, new_keys = set(current.data) if current else set(), set(row.data)
        result = {"locale": locale, "version": row.version, "applied": False, "preview_digest": digest, "target_data": row.data,
            "reference_revision": state.revision, "active_version": loc.ui_dictionary_version,
            "preview": {"total": len(new_keys), "added": sorted(new_keys - old_keys), "removed": sorted(old_keys - new_keys),
                "unchanged": sorted(new_keys & old_keys)}}
        if not confirm:
            return result
        _confirm_dictionary(reference_context, preview_digest, digest, expected_active_version, loc.ui_dictionary_version)
        if loc.ui_dictionary_version != row.version:
            loc.ui_dictionary_version = row.version
            bump_reference_revision(state)
            bump_reference_revision(locale_state)
            _dictionary_audit(session, user, audit.UI_DICTIONARY_ROLLBACK, locale, row.version, ip_address)
        return {**result, "applied": True, "reference_revision": state.revision, "active_version": loc.ui_dictionary_version}
