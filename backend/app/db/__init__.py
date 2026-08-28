"""Слой реляционной БД (SQLAlchemy): модели, сессии, движок.

Заменяет JSON-хранилища (documents.json/tags.json/manifest.json) на PostgreSQL
(прод) / SQLite (dev) без изменения интерфейса сервисов — см. MIGRATION_PLAN.md.
"""
