"""Движок и сессии SQLAlchemy (синхронные).

Приложение целиком синхронное (эндпоинты def + pipeline в threading.Thread),
поэтому используется синхронный SQLAlchemy 2.0:
  - prod: PostgreSQL через psycopg3 (postgresql+psycopg://);
  - dev:  SQLite (sqlite:///…/data/app.db), zero-config.

Отклонение от MIGRATION_PLAN.md (там предполагался async engine) — осознанное:
перевод на async def потребовал бы рефакторинга всех роутов и фонового
пайплайна без выгоды для текущей нагрузки.

Функция configure_for_tests подменяет движок на in-memory SQLite — для
изоляции юнит-тестов от реальной БД.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker | None = None
# RLock (не Lock): get_session_factory() вызывает get_engine() внутри своей
# критической секции — обычный Lock дал бы deadlock при первом создании движка.
_lock = threading.RLock()


def _make_engine(url: str) -> Engine:
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        # Фоновые потоки пайплайна и request-потоки делят один SQLite-файл:
        # отключаем привязку соединения к потоку.
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # Встроенные LOWER()/UPPER() в SQLite регистронезависимы только для
        # ASCII — кириллица (основной язык корпуса) остаётся регистрозависимой.
        # Переопределяем их на Python-реализацию str.lower/upper, которая
        # корректно работает с Unicode. Это нужно для .ilike()/func.lower()
        # в поиске по списку документов, чтобы он вёл себя одинаково на
        # SQLite (dev/тесты) и PostgreSQL (ILIKE).
        event.listen(engine, "connect", _on_sqlite_connect)
    return engine


def _on_sqlite_connect(dbapi_connection, _connection_record) -> None:
    # Встроенные LOWER()/UPPER() в SQLite регистронезависимы только для ASCII,
    # а кириллица (основной язык корпуса) остаётся регистрозависимой. Обёртки
    # null-safe: LOWER(NULL) должен возвращать NULL, как встроенная функция (а не
    # кидать TypeError) — иначе ILIKE по nullable-колонкам (uploaded_by, module)
    # падает при NULL-значениях.
    dbapi_connection.create_function(
        "lower", 1, lambda v: v.lower() if v is not None else None, deterministic=True
    )
    dbapi_connection.create_function(
        "upper", 1, lambda v: v.upper() if v is not None else None, deterministic=True
    )


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                # Динамический import: чтобы тесты, подменяющие
                # app.config.get_settings, влияли и на URL движка.
                from app.config import get_settings

                _engine = _make_engine(get_settings().db_url)
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        with _lock:
            if _session_factory is None:
                _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


@contextmanager
def session_scope():
    """Контекстный менеджер сессии: commit при успехе, rollback при ошибке.

    Используется и в эндпоинтах, и в фоновом пайплайне (по одной операции на
    сессию, commit сразу после записи — поведение как у прежнего _save()).
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def configure_for_tests(url: str = "sqlite:///:memory:") -> None:
    """Подменяет глобальный движок (для тестов — in-memory SQLite)."""
    global _engine, _session_factory
    with _lock:
        _engine = _make_engine(url)
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)


def init_db() -> None:
    """Создаёт отсутствующие таблицы (идемпотентно, zero-config dev).

    Используется на старте приложения и в миграционных скриптах. Для версионированных
    миграций prod есть Alembic (alembic upgrade head); create_all здесь — удобство
    для локального запуска без отдельного шага миграции.
    """
    from app.db.base import Base
    from app.db import models  # noqa: F401  (регистрация таблиц)

    Base.metadata.create_all(get_engine())
    # The glossary writer lock is a singleton row.  Seed it before request
    # threads can start so two first writers cannot race on its INSERT.
    if hasattr(models, "GlossaryState"):
        with session_scope() as session:
            if session.get(models.GlossaryState, 1) is None:
                has_terms = session.query(models.DomainTerm.id).limit(1).first() is not None
                session.add(models.GlossaryState(id=1, revision=0, identities_ready=not has_terms))
