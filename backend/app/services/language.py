"""Детекция языка текста (Этап 7 фаза D): кириллица/латиница эвристикой.

Достаточно для разметки source_locale (язык исходного документа): технические
документы — преимущественно проза на одном языке с вкраплениями латинских
идентификаторов (SAP/XML), поэтому порог по доле кириллицы надёжно разделяет
ru/не-ru без LLM.

С 08.09.2026 добавляется «de»: латинский текст, содержащий немецкие маркерные
буквы (ä/ö/ü/ß), помечается немецким. Лимит: немецкий технический текст БЕЗ
умлаутов неотличим от английского — классифицируется как «en» (информационная
метка source_locale, на поиск не влияет).
"""
from __future__ import annotations

# Буквы-маркеры немецкого в нижнем регистре (текст ниже приводится к lower).
_DE_MARKERS = frozenset("äöüß")
# Порог доли немецких маркеров среди букв текста для решения «de».
_DE_MIN_RATIO = 0.005
# Минимальное число вхождений маркеров, чтобы не спотыкаться о случайные
# немецкие имена в английском тексте.
_DE_MIN_HITS = 3


def detect_language(text: str | None, *, min_letters: int = 20) -> str | None:
    """Возвращает 'ru' | 'de' | 'en' | None по составу букв текста."""
    if not text:
        return None
    lowered = text.lower()
    cyr = 0
    lat = 0
    de_hits = 0
    for ch in lowered:
        cp = ord(ch)
        if 0x0400 <= cp <= 0x04FF:
            cyr += 1
        elif (0x41 <= cp <= 0x5A) or (0x61 <= cp <= 0x7A) or ch in _DE_MARKERS:
            lat += 1
            if ch in _DE_MARKERS:
                de_hits += 1
    total = cyr + lat
    if total < min_letters:
        return None
    ratio = cyr / total
    if ratio >= 0.6:
        return "ru"
    if ratio <= 0.4:
        if de_hits >= _DE_MIN_HITS and de_hits / total >= _DE_MIN_RATIO:
            return "de"
        return "en"
    return None  # смешанный текст — язык не детерминируется
