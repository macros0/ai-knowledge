"""Ручная коррекция языка исходного документа (`source_locale`, Этап 7 фаза D).

Паттерн «машинное значение → правка человеком → защита от перезаписи» — тот же,
что у подтверждения переводов справочников: `documents.source_locale` ставится
py3langid при финализации, а `documents.source_locale_source` фиксирует источник
('detected' | 'manual'). Ручная правка не перетирается следующим `regenerate`
(guard в `pipeline._finalize`).

Allowlist ручного выбора — `KNOWN_SOURCE_LOCALES` (реально значимые для корпуса
языки) ∪ коды из таблицы `locales` (любой статус). Полный список 139 языков
модели НЕ предлагается вручную — это UX-шум; экзотика добавляется одной строкой.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import Locale
from app.db.session import session_scope

# Канонический ISO 639-1, нижний регистр. Синхронизировать с фронтовым
# frontend/src/lib/sourceLocales.mjs.
KNOWN_SOURCE_LOCALES = frozenset(
    {
        "ar", "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr",
        "he", "hr", "hu", "is", "it", "ja", "kk", "ko", "lt", "lv", "nl",
        "no", "pl", "pt", "ro", "ru", "sk", "sl", "sq", "sr", "sv", "tr",
        "uk", "zh",
    }
)


def normalize_source_locale(code: str) -> str:
    return (code or "").strip().lower()


def is_valid_source_locale(code: str) -> bool:
    """Код допустим для РУЧНОЙ установки: статический allowlist ∪ `locales`."""
    normalized = normalize_source_locale(code)
    if not normalized:
        return False
    if normalized in KNOWN_SOURCE_LOCALES:
        return True
    with session_scope() as s:
        row = s.execute(select(Locale.code).where(Locale.code == normalized)).first()
    return row is not None
