"""Alembic-миграции против моделей: схема из `upgrade head` обязана совпасть.

Дыра, которую закрывает тест: в dev схему создаёт init_db() (create_all), и
новая колонка в models.py работает сразу — без миграции. В production
init_db() пропускается (app/main.py), схему ведёт только Alembic, и такая
колонка превращается в UndefinedColumn на первом же select. Так уехала
documents.problem (a0736b5, 03.09.2026): CI накатывал миграции, но ни одного
запроса против получившейся схемы не делал.

SQLite, а не Postgres: тест обязан быть герметичным (см. .github/workflows/ci.yml).
Различия диалектов в типах здесь не важны — сверяются структурные расхождения
(таблица/колонка есть или нет), они от диалекта не зависят.
"""
from pathlib import Path

import pytest
from sqlalchemy import create_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Расхождения, которые ломают production. Различия типов/nullable в выборку не
# берём: SQLite репортит их и для совпадающих схем (String vs VARCHAR и т.п.).
STRUCTURAL = ("add_table", "remove_table", "add_column", "remove_column")


@pytest.fixture
def migrated_db_url(tmp_path, monkeypatch):
    """Чистая БД, накатанная `alembic upgrade head`."""
    from alembic import command
    from alembic.config import Config

    from app.config import get_settings

    db_url = f"sqlite:///{(tmp_path / 'alembic.db').as_posix()}"
    # env.py берёт URL из get_settings().db_url — она lru_cache'ится, поэтому
    # кэш сбрасывается и на входе, и на выходе (иначе протечёт в другие тесты).
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        command.upgrade(Config(str(BACKEND_DIR / "alembic.ini")), "head")
        yield db_url
    finally:
        get_settings.cache_clear()


def test_migrations_match_models(migrated_db_url):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.db import models  # noqa: F401  (регистрирует таблицы в metadata)
    from app.db.base import Base

    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    finally:
        engine.dispose()

    drift = [d for d in diffs if isinstance(d, tuple) and d and d[0] in STRUCTURAL]
    assert not drift, (
        "Модели разъехались с миграциями — в production схему ведёт только "
        f"Alembic, и этих объектов там не будет: {drift}"
    )
