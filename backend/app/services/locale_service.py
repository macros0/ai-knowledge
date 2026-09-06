"""Управление языками и стоп-словами (Этап 7 roadmap, «Поддержка языков»).

Сервис покрывает фазу A: CRUD locales (draft/active/disabled), импорт/правка
стоп-слов с preview→confirm, история и rollback через append-only audit_log,
read-only probe по тестовым запросам (без изменения Qdrant), и резолв локали
пользователя из cookie okf.locale.

Правила активации (roadmap 7.1): язык должен присутствовать в статическом
манифесте фронтенда (SHIPPED_LOCALES) и иметь хотя бы один набор stopwords.
Переводы справочников — не блокер (фаза B).
"""
from __future__ import annotations

import re
from datetime import datetime

from app.db.models import AuditLog, Locale, Stopword
from app.db.session import session_scope
from app.services import audit
from app.services.stopwords import (
    KIND_BM25,
    KIND_MARKER,
    KINDS,
    LOCALE_STATUS_ACTIVE,
    LOCALE_STATUS_DISABLED,
    LOCALE_STATUS_DRAFT,
    LOCALE_STATUSES,
    SHIPPED_LOCALES,
    invalidate,
)

_LOCALE_CODE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$")
_WORD_RE = re.compile(r"^[a-zа-яё0-9]+$")
_MAX_WORD_LEN = 64


class LocaleError(ValueError):
    """Ошибка валидации локали/стоп-слов (API → 422/409)."""


class LocaleNotFoundError(LookupError):
    """Локали нет (API → 404)."""


def _now() -> datetime:
    from app.db.models import _utcnow

    return _utcnow()


# --- Локали ---


def _locale_to_dict(loc: Locale, *, bm25_count: int = 0, marker_count: int = 0) -> dict:
    return {
        "code": loc.code,
        "name": loc.name,
        "status": loc.status,
        "ui_dictionary_version": loc.ui_dictionary_version,
        "created_at": loc.created_at,
        "updated_at": loc.updated_at,
        "stopwords_bm25_count": bm25_count,
        "stopwords_marker_count": marker_count,
    }


def list_locales() -> list[dict]:
    from sqlalchemy import select

    with session_scope() as s:
        locs = s.execute(select(Locale).order_by(Locale.code)).scalars().all()
        counts: dict[str, dict[str, int]] = {}
        for row in s.execute(select(Stopword.locale, Stopword.kind)).all():
            counts.setdefault(row.locale, {}).setdefault(row.kind, 0)
            counts[row.locale][row.kind] += 1
    out = []
    for loc in locs:
        c = counts.get(loc.code, {})
        out.append(
            _locale_to_dict(
                loc, bm25_count=c.get(KIND_BM25, 0), marker_count=c.get(KIND_MARKER, 0)
            )
        )
    return out


def get_locale(code: str) -> dict:
    from sqlalchemy import select

    with session_scope() as s:
        loc = s.execute(select(Locale).where(Locale.code == code)).scalar_one_or_none()
    if loc is None:
        raise LocaleNotFoundError(code)
    return _locale_to_dict(loc)


def active_locale_codes() -> frozenset:
    from sqlalchemy import select

    with session_scope() as s:
        rows = s.execute(
            select(Locale.code).where(Locale.status == LOCALE_STATUS_ACTIVE)
        ).scalars().all()
    return frozenset(rows)


def create_locale(code: str, name: str) -> dict:
    code = (code or "").strip().lower()
    name = (name or "").strip()
    if not _LOCALE_CODE_RE.match(code):
        raise LocaleError("Некорректный код локали (ожидается BCP 47, например 'ru' или 'pt-BR')")
    if not name:
        raise LocaleError("Название языка обязательно")
    with session_scope() as s:
        if s.query(Locale).filter(Locale.code == code).first() is not None:
            raise LocaleError(f"Язык '{code}' уже существует")
        s.add(Locale(code=code, name=name, status=LOCALE_STATUS_DRAFT))
    return get_locale(code)


def update_locale(code: str, name: str | None = None, status: str | None = None) -> dict:
    if status is not None and status not in LOCALE_STATUSES:
        raise LocaleError(f"Недопустимый статус: {status}")
    with session_scope() as s:
        loc = s.query(Locale).filter(Locale.code == code).first()
        if loc is None:
            raise LocaleNotFoundError(code)
        if name is not None:
            name = (name or "").strip()
            if not name:
                raise LocaleError("Название языка не может быть пустым")
            loc.name = name
        if status is not None:
            loc.status = status
    return get_locale(code)


def _validate_activation(code: str) -> None:
    if code not in SHIPPED_LOCALES:
        raise LocaleError(
            f"Язык '{code}' отсутствует в статическом манифесте фронтенда — "
            "активация требует релиза со словарём UI"
        )
    with session_scope() as s:
        n_bm25 = (
            s.query(Stopword)
            .filter(Stopword.locale == code, Stopword.kind == KIND_BM25)
            .count()
        )
    if n_bm25 == 0:
        raise LocaleError("Нельзя активировать язык без набора stopwords (kind=bm25)")


def activate(code: str) -> dict:
    _validate_activation(code)
    return update_locale(code, status=LOCALE_STATUS_ACTIVE)


def disable(code: str) -> dict:
    if code == "ru":
        raise LocaleError("Нельзя отключить fallback-язык 'ru'")
    return update_locale(code, status=LOCALE_STATUS_DISABLED)


# --- Стоп-слова ---


def _normalize_words(words: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in words:
        w = (raw or "").strip().lower()
        if not w:
            continue
        if len(w) > _MAX_WORD_LEN:
            raise LocaleError(f"Слово длиннее {_MAX_WORD_LEN} символов: {raw!r}")
        if not _WORD_RE.match(w):
            raise LocaleError(f"Некорректное слово (допустимы буквы/цифры, без пробелов): {raw!r}")
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _current_words(code: str, kind: str) -> list[str]:
    from sqlalchemy import select

    with session_scope() as s:
        rows = s.execute(
            select(Stopword.word)
            .where(Stopword.locale == code, Stopword.kind == kind)
            .order_by(Stopword.word)
        ).scalars().all()
    return list(rows)


def list_stopwords(code: str, kind: str | None = None) -> list[dict]:
    from sqlalchemy import select

    kinds = list(KINDS) if kind is None else [kind]
    if kind is not None and kind not in KINDS:
        raise LocaleError(f"Неизвестный kind: {kind}")
    with session_scope() as s:
        rows = s.execute(
            select(Stopword)
            .where(Stopword.locale == code, Stopword.kind.in_(kinds))
            .order_by(Stopword.kind, Stopword.word)
        ).scalars().all()
    return [
        {
            "word": r.word,
            "kind": r.kind,
            "updated_by": r.updated_by,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


def _apply_words(code: str, kind: str, words: list[str], *, updated_by: str) -> None:
    """Заменяет набор слов (одна транзакция) + синхронная инвалидация кэша."""
    with session_scope() as s:
        s.query(Stopword).filter(Stopword.locale == code, Stopword.kind == kind).delete()
        for w in words:
            s.add(Stopword(locale=code, word=w, kind=kind, created_by=updated_by, updated_by=updated_by))
    invalidate(kind)


def import_stopwords(
    code: str,
    words: list[str],
    kind: str,
    mode: str,
    *,
    confirm: bool,
    user,
    ip_address: str | None = None,
) -> dict:
    """Импорт набора стоп-слов: без confirm — preview diff, с confirm — применение.

    Возвращает dict: {locale, kind, mode, added, removed, unchanged, total_after,
    applied}. Применение — одна транзакция, инвалидация кэша синхронна, audit.
    """
    get_locale(code)  # 404, если нет
    if kind not in KINDS:
        raise LocaleError(f"Неизвестный kind: {kind}")
    if mode not in ("replace", "merge"):
        raise LocaleError("mode должен быть 'replace' или 'merge'")
    incoming = _normalize_words(words)
    if len(incoming) > get_max_words():
        raise LocaleError(f"Набор превышает лимит {get_max_words()} слов")

    current = set(_current_words(code, kind))
    incoming_set = set(incoming)
    added = [w for w in incoming if w not in current]
    removed = [w for w in current if w not in incoming_set] if mode == "replace" else []
    unchanged = [w for w in incoming if w in current]

    result = {
        "locale": code,
        "kind": kind,
        "mode": mode,
        "added": sorted(added),
        "removed": sorted(removed),
        "unchanged": sorted(unchanged),
        "total_after": len(incoming_set) if mode == "replace" else len(current | incoming_set),
        "applied": False,
    }
    if not confirm:
        return result

    final = incoming if mode == "replace" else sorted(current | incoming_set)
    username = getattr(user, "username", None) or "anonymous"
    _apply_words(code, kind, final, updated_by=username)
    result["applied"] = True
    audit.record(
        user,
        audit.STOPWORDS_IMPORT,
        audit.TARGET_LOCALE,
        target_id=code,
        old_value={"words": sorted(current), "kind": kind},
        new_value={"words": sorted(final), "kind": kind},
        ip_address=ip_address,
        meta={
            "mode": mode,
            "added": len(added),
            "removed": len(removed),
            "unchanged": len(unchanged),
        },
    )
    return result


def add_stopword(code: str, word: str, kind: str, *, user, ip_address: str | None = None) -> dict:
    get_locale(code)
    if kind not in KINDS:
        raise LocaleError(f"Неизвестный kind: {kind}")
    (word,) = _normalize_words([word])
    username = getattr(user, "username", None) or "anonymous"
    from sqlalchemy import select

    with session_scope() as s:
        exists = s.execute(
            select(Stopword).where(Stopword.locale == code, Stopword.word == word, Stopword.kind == kind)
        ).scalar_one_or_none()
        if exists is None:
            s.add(Stopword(locale=code, word=word, kind=kind, created_by=username, updated_by=username))
    invalidate(kind)
    audit.record(
        user,
        audit.STOPWORDS_UPDATE,
        audit.TARGET_LOCALE,
        target_id=code,
        old_value=None,
        new_value={"word": word, "kind": kind},
        ip_address=ip_address,
    )
    return {"word": word, "kind": kind}


def delete_stopword(code: str, word: str, kind: str, *, user, ip_address: str | None = None) -> dict:
    get_locale(code)
    if kind not in KINDS:
        raise LocaleError(f"Неизвестный kind: {kind}")
    (word,) = _normalize_words([word])
    with session_scope() as s:
        row = s.query(Stopword).filter(
            Stopword.locale == code, Stopword.word == word, Stopword.kind == kind
        ).first()
        if row is None:
            raise LocaleNotFoundError(f"{word} ({kind})")
        s.delete(row)
    invalidate(kind)
    audit.record(
        user,
        audit.STOPWORDS_UPDATE,
        audit.TARGET_LOCALE,
        target_id=code,
        old_value={"word": word, "kind": kind},
        new_value=None,
        ip_address=ip_address,
    )
    return {"word": word, "kind": kind}


def rename_stopword(code: str, word: str, kind: str, new_word: str, *, user, ip_address: str | None = None) -> dict:
    """Переименование слова (delete + insert в одной транзакции, один audit)."""
    get_locale(code)
    if kind not in KINDS:
        raise LocaleError(f"Неизвестный kind: {kind}")
    (word,) = _normalize_words([word])
    (new_word,) = _normalize_words([new_word])
    if word == new_word:
        return {"word": word, "kind": kind}
    username = getattr(user, "username", None) or "anonymous"
    with session_scope() as s:
        row = s.query(Stopword).filter(
            Stopword.locale == code, Stopword.word == word, Stopword.kind == kind
        ).first()
        if row is None:
            raise LocaleNotFoundError(f"{word} ({kind})")
        s.delete(row)
        s.add(Stopword(locale=code, word=new_word, kind=kind, created_by=username, updated_by=username))
    invalidate(kind)
    audit.record(
        user,
        audit.STOPWORDS_UPDATE,
        audit.TARGET_LOCALE,
        target_id=code,
        old_value={"word": word, "kind": kind},
        new_value={"word": new_word, "kind": kind},
        ip_address=ip_address,
    )
    return {"word": new_word, "kind": kind}


def stopwords_history(code: str) -> list[dict]:
    with session_scope() as s:
        rows = (
            s.query(AuditLog)
            .filter(
                AuditLog.action_type == audit.STOPWORDS_IMPORT,
                AuditLog.target_id == code,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(100)
            .all()
        )
    return [
        {
            "id": r.id,
            "created_at": r.created_at,
            "username": r.username,
            "kind": (r.new_value or {}).get("kind"),
            "meta": r.meta,
            "words": (r.new_value or {}).get("words") or [],
        }
        for r in rows
    ]


def rollback_stopwords(code: str, entry_id: int, *, user, ip_address: str | None = None) -> dict:
    get_locale(code)
    with session_scope() as s:
        row = s.query(AuditLog).filter(
            AuditLog.id == entry_id,
            AuditLog.action_type == audit.STOPWORDS_IMPORT,
            AuditLog.target_id == code,
        ).first()
    if row is None:
        raise LocaleNotFoundError(f"Запись импорта {entry_id}")
    kind = (row.old_value or {}).get("kind")
    words = list((row.old_value or {}).get("words") or [])
    if kind not in KINDS or not words:
        raise LocaleError("Снапшот импорта пуст или некорректен")
    username = getattr(user, "username", None) or "anonymous"
    _apply_words(code, kind, words, updated_by=username)
    audit.record(
        user,
        audit.STOPWORDS_ROLLBACK,
        audit.TARGET_LOCALE,
        target_id=code,
        old_value={"kind": kind, "from_entry": entry_id},
        new_value={"words": sorted(words), "kind": kind},
        ip_address=ip_address,
    )
    return {"locale": code, "kind": kind, "total_after": len(words), "applied": True}


def get_max_words() -> int:
    from app.config import get_settings

    return get_settings().stopwords_max_words


# --- Локаль пользователя и probe ---


def request_locale(request, fallback: str = "ru") -> str:
    """Резолв UI-локали из cookie okf.locale с клампом к активным языкам.

    Parity с frontend/src/i18n/core.js (normalizeLocale/resolveServerLocale):
    cookie побеждает, неизвестный/неактивный код → fallback ru.
    """
    code = (request.cookies.get("okf.locale") or "").strip().lower()
    if code in active_locale_codes():
        return code
    return fallback


def probe(queries: list[str]) -> list[dict]:
    """Read-only прогон тестовых запросов через композитный поиск (без записи).

    Возвращает [{query, hits: [{title, score}]}]. Требует доступного Qdrant —
    при недоступности пробрасывается DependencyUnavailableError → 503.
    """
    from app.config import get_settings
    from app.services.embedder import Embedder
    from app.services.sparse import to_sparse_vector
    from app.services.stopwords import get_stopwords
    from app.services.vector_store import VectorStore

    settings = get_settings()
    vs = VectorStore()
    emb = Embedder()
    bm25_sw = get_stopwords(KIND_BM25)
    out = []
    for q in queries:
        dense = emb.embed(q)
        sparse = to_sparse_vector(q, stopwords=bm25_sw)
        hits = vs.search_composite(
            dense_vec=dense,
            sparse_vec=sparse,
            tags=None,
            branches={"dense", "bm25"},
            top_k=5,
        )
        out.append(
            {
                "query": q,
                "hits": [
                    {
                        "title": h.payload.get("title", ""),
                        "score": round(float(h.score), 4),
                    }
                    for h in hits
                ],
            }
        )
    return out
