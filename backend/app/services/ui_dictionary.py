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

from sqlalchemy import select

from app.db.models import Locale, UiDictionary
from app.db.session import session_scope
from app.services import audit
from app import error_codes as codes
from app.services.errors import NotFoundError

logger = logging.getLogger(__name__)

_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "i18n" / "ui_keys.json"
# Полный en-словарь для автосида при активации языка без runtime-override
# (фаза 2, 08.09.2026). Генерируется node-скриптом export-ui-keys.mjs вместе с
# ui_keys.json; дрейф ловится frontend/test/i18n.test.mjs.
_EN_SOURCE_PATH = Path(__file__).resolve().parents[1] / "i18n" / "ui_en.json"
_PARAM_RE = re.compile(r"\{(\w+)\}")


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


def import_dictionary(
    locale: str,
    data: dict,
    note: str | None,
    uploaded_by: str | None,
    *,
    confirm: bool = False,
    user=None,
    ip_address: str | None = None,
) -> dict:
    """Двухшаговый импорт: без confirm — preview, с confirm — новый version.

    Возвращает {errors, applied, preview|version}. Семантика — ПОЛНАЯ замена
    override-словаря (загруженный JSON становится актуальным словарём локали).
    """
    errors = validate(locale, data)
    if errors:
        return {"errors": errors, "applied": False}

    # Проверка существования локали ДО построения preview: иначе preview для
    # несуществующего языка вернёт 200 и замаскирует ошибку вызывающей стороны
    # (инцидент «[object Object]», Этап 7 P0 hardening).
    with session_scope() as s:
        if s.get(Locale, locale) is None:
            raise ValueError(f"Язык '{locale}' не найден")

    current = get_active(locale)
    current_keys = set(current["data"].keys()) if current else set()
    new_keys = set(data.keys())
    preview = {
        "total": len(new_keys),
        "added": sorted(new_keys - current_keys),
        "removed": sorted(current_keys - new_keys),
        "unchanged": sorted(new_keys & current_keys),
    }
    if not confirm:
        return {"errors": [], "applied": False, "preview": preview}

    with session_scope() as s:
        loc = s.get(Locale, locale)
        if loc is None:
            raise ValueError(f"Язык '{locale}' не найден")
        next_version = (loc.ui_dictionary_version or 0) + 1
        s.add(
            UiDictionary(
                locale=locale,
                version=next_version,
                data=data,
                note=note,
                uploaded_by=uploaded_by,
            )
        )
        loc.ui_dictionary_version = next_version

    if user is not None:
        audit.record(
            user,
            audit.UI_DICTIONARY_IMPORT,
            audit.TARGET_LOCALE,
            target_id=locale,
            new_value={"version": next_version, "total": len(new_keys)},
            ip_address=ip_address,
            meta={
                "note": note,
                "added": len(preview["added"]),
                "removed": len(preview["removed"]),
            },
        )
    return {"errors": [], "applied": True, "version": next_version, "preview": preview}


def seed_english_copy(locale: str, *, user=None, ip_address: str | None = None) -> bool:
    """Автосид en-копии при активации языка без runtime-override (фаза 2).

    Модель «активный ≠ UI-язык» (08.09.2026): язык можно активировать без релиза
    со словарём. Чтобы активированный язык сразу имел работающий словарь (а
    редактор «Перевод интерфейса» — отправную точку для перевода на целевой язык),
    при активации без override засевается ПОЛНЫЙ en-словарь (ui_en.json):
    английский — международный язык-источник.

    Контракт — любая неудача = warning + False, результат активации НЕ затронут:
      - ru/en       → False (ru — fallback-источник; en — идентичная копия, бессмысленна);
      - есть override → False (идемпотентность: никогда не перезаписываем);
      - applied=False (ошибки валидации: ru/uk-модель плюралов и т.п.) → warning + False;
      - IntegrityError по unique(locale, version) — гонка параллельной активации:
        оба конкурента вычислили version 1, проигравшего режет БД-констрейнт
        (не дублируется и audit: он пишется только после успешного коммита) →
        warning + False;
      - прочие Exception → warning + exc_info + False.
    """
    if locale in ("ru", "en"):
        return False
    if get_active(locale) is not None:
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
            user=user,
            ip_address=ip_address,
        )
    except Exception:
        logger.warning(
            "Автосид en-копии для '%s' не выполнен (конкурентная активация "
            "или ошибка БД) — активный словарь останется без override",
            locale,
            exc_info=True,
        )
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


def rollback(locale: str, version_id: int, *, user=None, ip_address: str | None = None) -> dict:
    """Возвращает locales.ui_dictionary_version на выбранную историческую версию."""
    with session_scope() as s:
        row = s.get(UiDictionary, version_id)
        if row is None or row.locale != locale:
            raise NotFoundError("Версия словаря не найдена", code=codes.DICT_VERSION_NOT_FOUND)
        loc = s.get(Locale, locale)
        if loc is None:
            raise ValueError(f"Язык '{locale}' не найден")
        loc.ui_dictionary_version = row.version
        version = row.version

    if user is not None:
        audit.record(
            user,
            audit.UI_DICTIONARY_ROLLBACK,
            audit.TARGET_LOCALE,
            target_id=locale,
            new_value={"version": version},
            ip_address=ip_address,
        )
    return {"locale": locale, "version": version, "applied": True}
