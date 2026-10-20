"""Управление языками и стоп-словами (Этап 7 roadmap, «Поддержка языков»).

Сервис покрывает фазу A: CRUD locales (draft/active/disabled), импорт/правка
стоп-слов с preview→confirm, история и rollback через независимые снимки импорта,
read-only probe по тестовым запросам (без изменения Qdrant), и резолв локали
пользователя из cookie okf.locale.

Правила активации (двухуровневая модель, 08.09.2026): язык должен иметь хотя бы
один набор stopwords (kind=bm25). Гейта «присутствие в статическом манифесте
фронтенда» НЕТ: язык можно активировать для корпуса/стоп-слов без релиза UI.
«Активный» ≠ «UI-язык»: в переключатель языка попадают только языки манифеста
фронтенда (ru/en/de); для остальных активных UI недоступен, а runtime-словарь
при активации автосидируется en-копией (ui_dictionary.seed_english_copy).
Переводы справочников — не блокер (фаза B).
"""
from __future__ import annotations

import re
from datetime import datetime

from app.db.models import Locale, Stopword, StopwordImportSnapshot, ReferenceResourceState
from app.db.session import session_scope
from app.services import audit
from sqlalchemy import select
from app.services.reference_resources import ReferenceKey
from app.services.reference_concurrency import (ReferenceError, lock_reference_resources,
    bump_reference_revision, initialize_reference_resource, reference_preview_digest)
from app.services.reference_mutation import guard_reference_writes
from app.services.sparse import TOKEN_EXTRA_LETTERS, normalize_for_tokens
from app.services.stopwords import (
    KIND_BM25,
    KIND_MARKER,
    KINDS,
    LOCALE_STATUS_ACTIVE,
    LOCALE_STATUS_DISABLED,
    LOCALE_STATUS_DRAFT,
    LOCALE_STATUSES,
    invalidate,
)

_LOCALE_CODE_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})*$")
# Единый источник алфавита слов — TOKEN_EXTRA_LETTERS из sparse.py (та же
# константа, что и TOKEN_RE): слово, которое не может быть токеном поиска,
# хранить в стоп-листе бессмысленно. Класс не должен расходиться с токенайзером.
_WORD_RE = re.compile(rf"^[a-zа-яё0-9{TOKEN_EXTRA_LETTERS}]+$")
_MAX_WORD_LEN = 64


class LocaleError(ValueError):
    """Ошибка валидации локали/стоп-слов (API → 422/409)."""


class LocaleNotFoundError(LookupError):
    """Локали нет (API → 404)."""


def _now() -> datetime:
    from app.db.models import _utcnow

    return _utcnow()


# --- Локали ---


def _locale_to_dict(loc: Locale, *, reference_revision: int = 1, bm25_count: int = 0, marker_count: int = 0) -> dict:
    return {
        "code": loc.code,
        "reference_revision": reference_revision,
        "name": loc.name,
        "status": loc.status,
        "ui_dictionary_version": loc.ui_dictionary_version,
        "created_at": loc.created_at,
        "updated_at": loc.updated_at,
        "stopwords_bm25_count": bm25_count,
        "stopwords_marker_count": marker_count,
    }


def list_locales() -> list[dict]:
    with session_scope() as session:
        codes = list(session.scalars(select(Locale.code)))
        keys = [ReferenceKey("locale", code) for code in codes]
        states = lock_reference_resources(session, keys)
        locs = session.scalars(select(Locale).where(Locale.code.in_(codes)).order_by(Locale.code)
            .execution_options(populate_existing=True)).all()
        counts = {}
        for row in session.execute(select(Stopword.locale, Stopword.kind)):
            counts.setdefault(row.locale, {}).setdefault(row.kind, 0)
            counts[row.locale][row.kind] += 1
        return [_locale_to_dict(loc, reference_revision=states[ReferenceKey("locale", loc.code)].revision,
            bm25_count=counts.get(loc.code, {}).get(KIND_BM25, 0),
            marker_count=counts.get(loc.code, {}).get(KIND_MARKER, 0)) for loc in locs]


def get_locale(code: str) -> dict:
    with session_scope() as session:
        if session.get(Locale, code) is None:
            raise LocaleNotFoundError(code)
        state = lock_reference_resources(session, [ReferenceKey("locale", code)])[ReferenceKey("locale", code)]
        loc = session.get(Locale, code, populate_existing=True)
        return _locale_to_dict(loc, reference_revision=state.revision)


def active_locale_codes() -> frozenset:
    from sqlalchemy import select

    with session_scope() as s:
        rows = s.execute(
            select(Locale.code).where(Locale.status == LOCALE_STATUS_ACTIVE)
        ).scalars().all()
    return frozenset(rows)


def create_locale(code: str, name: str, *, user=None, ip_address=None) -> dict:
    code, name = (code or "").strip().lower(), (name or "").strip()
    if not _LOCALE_CODE_RE.match(code):
        raise LocaleError("Некорректный код локали (ожидается BCP 47, например 'ru' или 'pt-BR')")
    if not name:
        raise LocaleError("Название языка обязательно")
    key = ReferenceKey("locale", code)
    with session_scope() as session:
        existed = session.get(ReferenceResourceState, key.canonical) is not None
        lock_reference_resources(session, [key])
        if session.get(Locale, code) is not None:
            raise LocaleError(f"Язык '{code}' уже существует")
        loc = Locale(code=code, name=name, status=LOCALE_STATUS_DRAFT)
        session.add(loc)
        session.flush()
        state = lock_reference_resources(session, [key])[key]
        if existed:
            from app.services.reference_edit_lock import invalidate_reference_lease
            bump_reference_revision(state)
            invalidate_reference_lease(state)
        for kind in KINDS:
            initialize_reference_resource(session, ReferenceKey("stopword_set", code, kind=kind))
        initialize_reference_resource(session, ReferenceKey("ui_dictionary", code))
        if user is not None:
            _locale_audit(session, user, audit.LOCALE_CREATE, code, None, {"name": name}, ip_address)
        return _locale_to_dict(loc, reference_revision=state.revision)


def _locale_audit(session, user, action, code, before, after, ip_address):
    audit.record_in_session(session, action_type=action, user_id=getattr(user, "user_id", None),
        username=getattr(user, "username", None), target_type=audit.TARGET_LOCALE,
        target_id=code, old_value=before, new_value=after, ip_address=ip_address)


def update_locale(code: str, name: str | None = None, status: str | None = None, *, reference_context=None,
    user=None, ip_address=None, action=audit.LOCALE_UPDATE) -> dict:
    if status is not None and status not in LOCALE_STATUSES:
        raise LocaleError(f"Недопустимый статус: {status}")
    if code == "ru" and status == LOCALE_STATUS_DISABLED:
        raise LocaleError("Нельзя отключить fallback-язык 'ru'")
    key = ReferenceKey("locale", code)
    with session_scope() as session:
        keys = [key] + ([ReferenceKey("stopword_set", code, kind=KIND_BM25)] if status == LOCALE_STATUS_ACTIVE else [])
        lock_reference_resources(session, keys)
        loc = session.get(Locale, code, populate_existing=True)
        if loc is None:
            raise LocaleNotFoundError(code)
        state = guard_reference_writes(session, [key], reference_context)[key]
        if status == LOCALE_STATUS_ACTIVE and not session.scalar(select(Stopword.word)
            .where(Stopword.locale == code, Stopword.kind == KIND_BM25).limit(1)):
            raise LocaleError("Нельзя активировать язык без набора stopwords (kind=bm25)")
        old = {"name": loc.name, "status": loc.status}
        if name is not None:
            name = (name or "").strip()
            if not name:
                raise LocaleError("Название языка не может быть пустым")
            loc.name = name
        if status is not None:
            loc.status = status
        changed = old != {"name": loc.name, "status": loc.status}
        if changed:
            bump_reference_revision(state)
            if user is not None:
                _locale_audit(session, user, action, code, old, {"name": loc.name, "status": loc.status}, ip_address)
        result = _locale_to_dict(loc, reference_revision=state.revision)
    if changed:
        invalidate()
    return result


def activate(code: str, **kwargs) -> dict:
    return update_locale(code, status=LOCALE_STATUS_ACTIVE, action=audit.LOCALE_ACTIVATE, **kwargs)


def disable(code: str, **kwargs) -> dict:
    return update_locale(code, status=LOCALE_STATUS_DISABLED, action=audit.LOCALE_DISABLE, **kwargs)


# --- Стоп-слова ---


def _normalize_words(words: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    invalid_chars: list[str] = []
    invalid_sep: list[str] = []
    for raw in words:
        # Та же нормализация, что у токенайзера: турецкое «İstanbul» иначе не
        # прошло бы валидатор (после lower() в слове остаётся combining-точка).
        w = normalize_for_tokens(raw).strip()
        if not w:
            continue
        if len(w) > _MAX_WORD_LEN:
            raise LocaleError(f"Слово длиннее {_MAX_WORD_LEN} символов: {raw!r}")
        if not _WORD_RE.match(w):
            # Разделяем причины: символы, по которым токенайзер режет токен
            # (апостроф/дефис/пробел/подчёркивание — слово НИКОГДА не станет
            # токеном, хранить его бессмысленно), и буквы вне алфавита.
            if re.search(r"['\-\s_.]", w):
                invalid_sep.append(raw)
            else:
                invalid_chars.append(raw)
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    if invalid_sep or invalid_chars:
        parts: list[str] = []
        if invalid_sep:
            shown = ", ".join(repr(x) for x in invalid_sep[:10])
            more = f" (и ещё {len(invalid_sep) - 10})" if len(invalid_sep) > 10 else ""
            parts.append(
                "содержат апостроф/дефис/пробел — токенайзер режет по ним, такое "
                f"слово никогда не станет токеном поиска; уберите его или разбейте "
                f"(например, 'celle-ci' → 'celle'): {shown}{more}"
            )
        if invalid_chars:
            shown = ", ".join(repr(x) for x in invalid_chars[:10])
            more = f" (и ещё {len(invalid_chars) - 10})" if len(invalid_chars) > 10 else ""
            parts.append(
                "содержат буквы вне алфавита токенайзера "
                f"(латиница с европейскими диакритиками, кириллица, цифры): {shown}{more}"
            )
        raise LocaleError("Некорректное слово: " + "; ".join(parts))
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


def _stopword_key(code, kind):
    if kind not in KINDS:
        raise LocaleError(f"Неизвестный kind: {kind}")
    return ReferenceKey("stopword_set", code, kind=kind)


def _words_in_session(session, code, kind):
    return list(session.scalars(select(Stopword.word).where(Stopword.locale == code, Stopword.kind == kind).order_by(Stopword.word)))


def stopword_snapshot(code: str, kind: str | None = None) -> dict:
    kinds = list(KINDS) if kind is None else [kind]
    keys = [_stopword_key(code, value) for value in kinds]
    with session_scope() as session:
        if session.get(Locale, code) is None:
            raise LocaleNotFoundError(code)
        states = lock_reference_resources(session, keys)
        rows = session.scalars(select(Stopword).where(Stopword.locale == code, Stopword.kind.in_(kinds))
            .order_by(Stopword.kind, Stopword.word)).all()
        return {"locale": code, "reference_revisions": {value: states[_stopword_key(code, value)].revision for value in kinds},
            "words": [{"word": row.word, "kind": row.kind, "updated_by": row.updated_by, "updated_at": row.updated_at,
                "reference_revision": states[_stopword_key(code, row.kind)].revision} for row in rows]}


def list_stopwords(code: str, kind: str | None = None) -> list[dict]:
    return stopword_snapshot(code, kind)["words"]


def _word_state(session, code, kind, reference_context=None, *, write=True):
    key = _stopword_key(code, kind)
    state = lock_reference_resources(session, [key])[key]
    if session.get(Locale, code) is None:
        raise LocaleNotFoundError(code)
    return guard_reference_writes(session, [key], reference_context)[key] if write else state


def _apply_words_in_session(session, code, kind, words, updated_by):
    current = _words_in_session(session, code, kind)
    if current == sorted(words):
        return False
    session.query(Stopword).filter(Stopword.locale == code, Stopword.kind == kind).delete()
    session.add_all([Stopword(locale=code, word=word, kind=kind, created_by=updated_by, updated_by=updated_by) for word in words])
    return True


def _apply_words(code: str, kind: str, words: list[str], *, updated_by: str) -> None:
    with session_scope() as session:
        state = _word_state(session, code, kind)
        if _apply_words_in_session(session, code, kind, words, updated_by):
            bump_reference_revision(state)
    invalidate(kind)


def _confirm_preview(reference_context, supplied, actual):
    if reference_context is not None and not supplied:
        raise ReferenceError("reference_precondition_required", 428)
    if supplied is not None and supplied != actual:
        raise ReferenceError("reference_preview_stale")


def _word_diff(code, kind, mode, current, incoming, revision, operation=None):
    final = set(incoming) if mode == "replace" else set(current) | set(incoming)
    result = {"locale": code, "kind": kind, "mode": mode, "added": sorted(final - set(current)),
        "removed": sorted(set(current) - final), "unchanged": sorted(set(incoming) & set(current)),
        "total_after": len(final), "applied": False, "reference_revision": revision,
        "requires_empty_replace_confirmation": mode == "replace" and not incoming and bool(current)}
    result["preview_digest"] = reference_preview_digest({"operation": operation or "import", "locale": code,
        "kind": kind, "mode": mode, "words": sorted(incoming), "revision": revision, "final": sorted(final)})
    return result, sorted(final)


def import_stopwords(code: str, words: list[str], kind: str, mode: str, *, confirm: bool, user,
    ip_address: str | None = None, confirm_empty_replace: bool = False, reference_context=None, preview_digest=None) -> dict:
    if mode not in ("replace", "merge"):
        raise LocaleError("mode должен быть 'replace' или 'merge'")
    incoming = _normalize_words(words)
    if len(incoming) > get_max_words():
        raise LocaleError(f"Набор превышает лимит {get_max_words()} слов")
    with session_scope() as session:
        state = _word_state(session, code, kind, reference_context, write=confirm)
        current = _words_in_session(session, code, kind)
        result, final = _word_diff(code, kind, mode, current, incoming, state.revision)
        if not confirm:
            return result
        _confirm_preview(reference_context, preview_digest, result["preview_digest"])
        if result["requires_empty_replace_confirmation"] and not confirm_empty_replace:
            return result
        if _apply_words_in_session(session, code, kind, final, getattr(user, "username", None) or "anonymous"):
            bump_reference_revision(state)
        result.update(applied=True, reference_revision=state.revision)
        import_meta = {"mode": mode, "added": len(result["added"]), "removed": len(result["removed"]),
            "unchanged": len(result["unchanged"]),
            **({"empty_replace": True, "removed_count": len(result["removed"])} if result["requires_empty_replace_confirmation"] else {})}
        session.add(StopwordImportSnapshot(locale=code, kind=kind,
            username=getattr(user, "username", None), old_words=current, new_words=final, import_meta=import_meta))
        audit.record_in_session(session, action_type=audit.STOPWORDS_IMPORT,
            user_id=getattr(user, "user_id", None), username=getattr(user, "username", None),
            target_type=audit.TARGET_LOCALE, target_id=code,
            old_value={"words": current, "kind": kind}, new_value={"words": final, "kind": kind}, ip_address=ip_address,
            meta=import_meta)
    invalidate(kind)
    return result


def _mutate_word(code, word, kind, *, user, new_word=None, delete=False, reference_context=None, ip_address=None):
    (word,) = _normalize_words([word])
    if new_word is not None:
        (new_word,) = _normalize_words([new_word])
    username = getattr(user, "username", None) or "anonymous"
    with session_scope() as session:
        state = _word_state(session, code, kind, reference_context)
        row = session.get(Stopword, (code, word, kind))
        changed = False
        if delete or new_word is not None:
            if row is None:
                raise LocaleNotFoundError(f"{word} ({kind})")
            if delete or new_word != word:
                if new_word is not None and session.get(Stopword, (code, new_word, kind)) is not None:
                    raise LocaleError("Такое стоп-слово уже существует")
                session.delete(row)
                if not delete:
                    session.add(Stopword(locale=code, word=new_word, kind=kind, created_by=username, updated_by=username))
                changed = True
        elif row is None:
            session.add(Stopword(locale=code, word=word, kind=kind, created_by=username, updated_by=username))
            changed = True
        if changed:
            bump_reference_revision(state)
            audit.record_in_session(session, action_type=audit.STOPWORDS_UPDATE, user_id=getattr(user, "user_id", None),
                username=username, target_type=audit.TARGET_LOCALE, target_id=code,
                old_value={"word": word, "kind": kind} if delete or new_word is not None else None,
                new_value=None if delete else {"word": new_word or word, "kind": kind}, ip_address=ip_address)
        result = {"word": new_word or word, "kind": kind, "reference_revision": state.revision}
    if changed:
        invalidate(kind)
    return result


def add_stopword(code: str, word: str, kind: str, *, user, ip_address=None, reference_context=None) -> dict:
    return _mutate_word(code, word, kind, user=user, ip_address=ip_address, reference_context=reference_context)


def delete_stopword(code: str, word: str, kind: str, *, user, ip_address=None, reference_context=None) -> dict:
    return _mutate_word(code, word, kind, delete=True, user=user, ip_address=ip_address, reference_context=reference_context)


def rename_stopword(code: str, word: str, kind: str, new_word: str, *, user, ip_address=None, reference_context=None) -> dict:
    return _mutate_word(code, word, kind, new_word=new_word, user=user, ip_address=ip_address, reference_context=reference_context)


def stopwords_history(code: str) -> list[dict]:
    with session_scope() as s:
        rows = (
            s.query(StopwordImportSnapshot)
            .filter(StopwordImportSnapshot.locale == code)
            .order_by(StopwordImportSnapshot.created_at.desc(), StopwordImportSnapshot.id.desc())
            .limit(100)
            .all()
        )
    return [
        {
            "id": r.id,
            "created_at": r.created_at,
            "username": r.username,
            "kind": r.kind,
            "meta": r.import_meta,
            "words": r.new_words or [],
        }
        for r in rows
    ]


def rollback_stopwords(code: str, entry_id: int, *, user, ip_address=None, confirm=True,
    reference_context=None, preview_digest=None, expected_kind=None) -> dict:
    with session_scope() as session:
        row = session.scalar(select(StopwordImportSnapshot).where(StopwordImportSnapshot.id == entry_id,
            StopwordImportSnapshot.locale == code))
        if row is None:
            raise LocaleNotFoundError(f"Запись импорта {entry_id}")
        kind = row.kind
        words = list(row.old_words or [])
        if kind not in KINDS or not words:
            raise LocaleError("Снапшот импорта пуст или некорректен")
        if expected_kind is not None and expected_kind != kind:
            raise ReferenceError("reference_preview_stale")
        state = _word_state(session, code, kind, reference_context, write=confirm)
        current = _words_in_session(session, code, kind)
        result, final = _word_diff(code, kind, "replace", current, words, state.revision, f"rollback:{entry_id}")
        if not confirm:
            return result
        _confirm_preview(reference_context, preview_digest, result["preview_digest"])
        if _apply_words_in_session(session, code, kind, final, getattr(user, "username", None) or "anonymous"):
            bump_reference_revision(state)
        result.update(applied=True, reference_revision=state.revision)
        audit.record_in_session(session, action_type=audit.STOPWORDS_ROLLBACK, user_id=getattr(user, "user_id", None),
            username=getattr(user, "username", None), target_type=audit.TARGET_LOCALE, target_id=code,
            old_value={"kind": kind, "from_entry": entry_id, "words": current},
            new_value={"words": final, "kind": kind}, ip_address=ip_address)
    invalidate(kind)
    return result


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
    from app.services.embedder import Embedder
    from app.services.sparse import to_sparse_vector
    from app.services.stopwords import get_stopwords
    from app.services.vector_store import VectorStore

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
