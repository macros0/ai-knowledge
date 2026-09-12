"""Телеметрия деградации качества генерации OKF (thread-local).

События пишутся из глубины llm_client/_parse_json (salvage частичного JSON)
и field_table (fallback на XML-эвристику), а пайплайн после каждого
generate_chunk дренирует их в staging-манифест (chunks_data → degradation).
При финализации события агрегируются в разные problem-коды: salvage получает
"llm_partial_result", а fallback классификатора — отдельный код
"llm_classifier_fallback".
(инцидент 03.09.2026: «ФС 3509» тихо завершался done с 4-5 концептами
вместо 11 из-за молчаливого отбрасывания хвоста JSON).

Thread-local, а не поле клиента: генерация документов идёт параллельно в
потоках пайплайна (LLM-стрим — в daemon-потоке, но парсинг JSON и события —
в потоке вызывающего), а OKFGenerator/LLMClient — общие инстансы.
"""
from __future__ import annotations

import threading

_local = threading.local()

# Событие деградации: чанк сохранён частично после обрезания ответа LLM
# (salvage последней надежды / данные после закрытой JSON-структуры).
LLM_SALVAGE = "llm_salvage"
# Событие деградации: LLM-классификатор таблиц упал, сработала XML-эвристика
# (потенциально менее точное извлечение).
CLASSIFIER_FALLBACK = "classifier_fallback"

KNOWN_EVENTS = {LLM_SALVAGE, CLASSIFIER_FALLBACK}


def record(event: str, detail: str = "") -> None:
    """Фиксирует событие деградации в thread-local буфер.

    Вызывается из любого места генерации; вне потока пайплайна (например,
    dev-детекция или интерактивный чат) события просто никем не читаются
    и умирают вместе с буфером потока — это безопасно.
    """
    if event not in KNOWN_EVENTS:
        return
    events = getattr(_local, "events", None)
    if events is None:
        events = []
        _local.events = events
    events.append({"event": event, "detail": detail})


def drain() -> list[dict]:
    """Забирает и очищает буфер событий текущего потока."""
    events = getattr(_local, "events", None)
    _local.events = []
    return list(events) if events else []
