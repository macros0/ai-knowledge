"""Декларативная база SQLAlchemy.

Модели объявлены в app.db.models; импорт этого модуля через metadata
(например, в alembic/env.py) требует явного импорта моделей.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
