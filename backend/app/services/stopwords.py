"""Единый источник стоп-слов (Этап 7 roadmap, «Поддержка языков»).

Стоп-слова хранятся как данные (таблицы locales/stopwords), а не как две
независимые константы в services/sparse.py и services/context_builder.py —
это снимает класс багов рассинхронизации между BM25-фильтром запроса и
анти-шумовым фильтром контекста (AGENTS.md, риск «Дублирование логики
стоп-слов»).

Ключевое архитектурное разделение:

- **Индексная формула ЗАМОРОЖЕНА.** `services/sparse.to_sparse_vector` (индексация
  точек через `_sparse_text`) использует константу `_STOPWORDS` и НИКОГДА не
  читает эту таблицу. Динамические стоп-слова применяются только на стороне
  ЗАПРОСА. Следствия:
    - добавление стоп-слова действует сразу (термин перестаёт участвовать в
      BM25-запросе и маркерах) — реиндекс не нужен;
    - удаление слова из исходного ru-набора возвращает его в запросы, но НЕ в
      индекс (индексные точки его не содержат) — поэтому удаление дефолтного
      слова не даёт поискового эффекта, UI показывает соответствующее
      предупреждение.

- **kind** различает два набора с разной ролью:
    - `bm25`   — фильтр лексической ветки (query-токенизация BM25);
    - `marker` — фильтр маркеров Matched terms / Title match (context_builder).

- **Объединение по активным locales.** `get_stopwords(kind)` возвращает frozenset
  слов всех АКТИВНЫХ locales данного kind — запросы к корпусу языка-нейтральны
  (EN-вопрос фильтрует и RU-, и EN-служебные слова).

- **Кэш TTL + синхронная инвалидация.** Импорт/правка слов вызывает `invalidate()`
  ДО возврата HTTP-ответа, поэтому следующий запрос `/chat` гарантированно видит
  новый набор (см. приёмку фазы A).
"""
from __future__ import annotations

import threading
import time

from app.config import get_settings
from app.db.models import Locale, Stopword
from app.db.session import session_scope
from app.services.context_builder import _MARKER_STOPWORDS as _RU_MARKER_SEED
from app.services.sparse import _STOPWORDS as _RU_BM25_SEED

KIND_BM25 = "bm25"
KIND_MARKER = "marker"
KINDS = (KIND_BM25, KIND_MARKER)

LOCALE_STATUS_DRAFT = "draft"
LOCALE_STATUS_ACTIVE = "active"
LOCALE_STATUS_DISABLED = "disabled"
LOCALE_STATUSES = (LOCALE_STATUS_DRAFT, LOCALE_STATUS_ACTIVE, LOCALE_STATUS_DISABLED)

# EN-служебные слова (>= 2 символов; однобуквенные отсеивает MIN_TOKEN_LEN).
# Лечит диагносцированный сценарий: EN-вопрос с the/of/for против RU-корпуса с
# латинскими SAP-идентификаторами давал ложные BM25-хиты.
_EN_BM25_SEED = frozenset(
    {
        "the", "of", "for", "from", "and", "or", "are", "is", "was", "were",
        "be", "been", "being", "to", "in", "on", "at", "by", "with", "an",
        "as", "it", "its", "this", "that", "these", "those", "do", "does",
        "did", "not", "but", "if", "then", "than", "so", "we", "you", "they",
        "he", "she", "his", "her", "their", "what", "which", "who", "whom",
        "when", "where", "why", "how", "all", "any", "each", "some", "such",
        "no", "nor", "only", "own", "same", "into", "over", "under", "again",
        "once", "here", "there", "will", "would", "should", "could", "may",
        "might", "has", "have", "had", "am", "about", "also", "between",
        "after", "before", "up", "down", "off", "out", "too", "very",
    }
)

# Дефолтные записи локали: (code, name, status). ru — fallback, всегда активен.
DEFAULT_LOCALES = (
    ("ru", "Русский", LOCALE_STATUS_ACTIVE),
    ("en", "English", LOCALE_STATUS_ACTIVE),
)


def _default_stopwords_by_locale() -> dict[str, dict[str, frozenset]]:
    """Сид стоп-слов по локали (единый источник для Alembic и ensure_seeded)."""
    return {
        "ru": {KIND_BM25: frozenset(_RU_BM25_SEED), KIND_MARKER: frozenset(_RU_MARKER_SEED)},
        "en": {KIND_BM25: _EN_BM25_SEED, KIND_MARKER: frozenset()},
    }


def default_stopwords() -> dict[str, dict[str, frozenset]]:
    return _default_stopwords_by_locale()


# --- Кэш ---
# kind -> (frozenset, expires_monotonic). Только чтение через get_stopwords;
# инвалидация — из импорта/правки слов (синхронно до ответа API).
_cache: dict[str, tuple[frozenset, float]] = {}
_cache_lock = threading.Lock()


def _load_stopwords(kind: str) -> frozenset:
    from sqlalchemy import select

    with session_scope() as s:
        rows = s.execute(
            select(Stopword.word)
            .join(Locale, Stopword.locale == Locale.code)
            .where(Locale.status == LOCALE_STATUS_ACTIVE, Stopword.kind == kind)
        ).scalars().all()
    return frozenset(rows)


def _has_active_locales() -> bool:
    from sqlalchemy import select

    with session_scope() as s:
        return (
            s.execute(
                select(Locale.code)
                .where(Locale.status == LOCALE_STATUS_ACTIVE)
                .limit(1)
            ).first()
            is not None
        )


def get_stopwords(kind: str) -> frozenset:
    """Объединённый набор стоп-слов активных locales для указанного kind (с кэшем).

    Фолбэк: если активных locales нет вовсе (БД не засеяна — dev create_all без
    ensure_seeded, либо админ отключил все языки) — возвращается ru-дефолт, чтобы
    BM25-запрос и маркеры не деградировали в пустой фильтр.
    """
    if kind not in KINDS:
        raise ValueError(f"Неизвестный kind стоп-слов: {kind}")
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(kind)
        if entry is not None and entry[1] > now:
            return entry[0]
    words = _load_stopwords(kind)
    if not words and not _has_active_locales():
        words = _default_stopwords_by_locale()["ru"].get(kind, frozenset())
    ttl = get_settings().stopwords_cache_ttl_seconds
    with _cache_lock:
        _cache[kind] = (words, time.monotonic() + ttl)
    return words


def invalidate(kind: str | None = None) -> None:
    """Сбрасывает кэш (kind=None — весь). Синхронно: вызывать ДО возврата ответа."""
    with _cache_lock:
        if kind is None:
            _cache.clear()
        else:
            _cache.pop(kind, None)


def ensure_seeded() -> dict:
    """Идемпотентный посев locales/stopwords per-PK (для dev: create_all без Alembic).

    Проверяется существование КОНКРЕТНЫХ строк (per-PK), а не «таблица пуста»:
    первый ручной INSERT администратором до перезапуска не блокирует досев
    остальных строк. Семантика эквивалентна `INSERT ... ON CONFLICT DO NOTHING`
    (в однопроцессном приложении на старте гонок нет). Дефолтные ru-слова,
    удалённые админом, восстанавливаются при старте — осознанно: удаление слова
    из замороженного индексного набора не имеет поискового эффекта.
    """
    from sqlalchemy import select
    from app.db.models import ReferenceResourceState
    from app.services.reference_resources import ReferenceKey
    from app.services.reference_concurrency import lock_reference_resources, bump_reference_revision
    from app.services.reference_edit_lock import live, invalidate_reference_lease

    defaults = _default_stopwords_by_locale()
    keys = [ReferenceKey("locale", code) for code, _name, _status in DEFAULT_LOCALES]
    keys += [ReferenceKey("ui_dictionary", code) for code, _name, _status in DEFAULT_LOCALES]
    keys += [ReferenceKey("stopword_set", code, kind=kind) for code, kinds in defaults.items() for kind in kinds]
    created = {"locales": 0, "stopwords": 0}
    with session_scope() as session:
        prior_keys = set(session.scalars(select(ReferenceResourceState.resource_key)
            .where(ReferenceResourceState.resource_key.in_([key.canonical for key in keys]))))
        states = lock_reference_resources(session, keys)
        existing_locales = set(session.scalars(select(Locale.code)))
        new_locales = set()
        for code, name, status in DEFAULT_LOCALES:
            if code not in existing_locales:
                session.add(Locale(code=code, name=name, status=status))
                created["locales"] += 1
                new_locales.add(code)
                key = ReferenceKey("locale", code)
                if key.canonical in prior_keys:
                    bump_reference_revision(states[key])
                    invalidate_reference_lease(states[key])
                dictionary_key = ReferenceKey("ui_dictionary", code)
                if dictionary_key.canonical in prior_keys:
                    bump_reference_revision(states[dictionary_key])
                    invalidate_reference_lease(states[dictionary_key])
        session.flush()
        existing_words = set(session.execute(select(Stopword.locale, Stopword.word, Stopword.kind)).all())
        for locale, kinds in defaults.items():
            for kind, words in kinds.items():
                missing = [word for word in words if (locale, word, kind) not in existing_words]
                if not missing:
                    continue
                key = ReferenceKey("stopword_set", locale, kind=kind)
                state = states[key]
                if live(state) and locale not in new_locales:
                    created["skipped_locked"] = created.get("skipped_locked", 0) + 1
                    continue
                if locale not in new_locales or key.canonical in prior_keys:
                    bump_reference_revision(state)
                if locale in new_locales and key.canonical in prior_keys:
                    invalidate_reference_lease(state)
                session.add_all([Stopword(locale=locale, word=word, kind=kind, created_by="seed") for word in missing])
                created["stopwords"] += len(missing)
    invalidate()
    return created
