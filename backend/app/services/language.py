"""Детекция языка текста (Этап 7 фаза D): кириллица/латиница эвристикой.

Достаточно для разметки source_locale (язык исходного документа) и query_locale:
технические документы — преимущественно проза на одном языке с вкраплениями
латинских идентификаторов (SAP/XML), поэтому порог по доле кириллицы надёжно
разделяет ru/en без LLM. Смешанный/недостаточный текст → None.
"""
from __future__ import annotations


def detect_language(text: str | None, *, min_letters: int = 20) -> str | None:
    """Возвращает 'ru' | 'en' | None по доле кириллицы среди букв текста."""
    if not text:
        return None
    cyr = 0
    lat = 0
    for ch in text:
        cp = ord(ch)
        if 0x0400 <= cp <= 0x04FF:
            cyr += 1
        elif (0x41 <= cp <= 0x5A) or (0x61 <= cp <= 0x7A):
            lat += 1
    total = cyr + lat
    if total < min_letters:
        return None
    ratio = cyr / total
    if ratio >= 0.6:
        return "ru"
    if ratio <= 0.4:
        return "en"
    return None  # смешанный текст — язык не детерминируется
