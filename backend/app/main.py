# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api import (
    admin_locales,
    attributes,
    audit,
    chat,
    chat_history,
    developments,
    documents,
    jobs,
    locales,
    search,
    tags,
    users,
)
from app.api.settings import router as settings_router
from app.auth.api import router as auth_router
from app.auth.service import require_user
from app.config import get_settings
from app.prompts.store import get_store
from app.services.errors import DependencyUnavailableError
from app.services.health import get_health
from app.services.vector_store import VectorStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CatchAllErrorsMiddleware(BaseHTTPMiddleware):
    """Перехватывает не-DependencyUnavailableError исключения → 500 с русским сообщением.

    DependencyUnavailableError пропускается дальше к ExceptionMiddleware,
    где зарегистрирован handler → 503 с code/service.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except DependencyUnavailableError:
            raise
        except Exception as exc:
            logger.exception("Необработанная ошибка: %s", exc)
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Внутренняя ошибка сервера. Обратитесь к администратору.",
                    "code": "internal_error",
                },
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создание отсутствующих таблиц БД (идемпотентно). Мягкий старт: если БД
    # недоступна — не валить процесс, репозитории будут пытаться при запросах.
    try:
        # create_all — удобство локального запуска. В production схема версионируется
        # Alembic (alembic upgrade head), поэтому init_db() здесь не вызываем — иначе
        # при дрейфе моделей таблицы создавались бы в обход миграций.
        if get_settings().environment != "production":
            from app.db.session import init_db

            init_db()
        # Идемпотентный посев locales/stopwords (Этап 7). Для dev (create_all без
        # Alembic-сида) — обязателен; для prod (сид через миграцию) — no-op.
        from app.services.stopwords import ensure_seeded

        ensure_seeded()
        from app.services.registry import reset_stale_statuses

        reset_stale_statuses()
    except Exception as exc:
        logging.warning("Не удалось инициализировать БД при старте: %s", exc)

    # Мягкий старт: не валить процесс, если Qdrant недоступен.
    # ensure_collection будет повторена при первом запросе или бэкфилле.
    try:
        vs = VectorStore()
        vs.ensure_collection()
    except DependencyUnavailableError as exc:
        logging.warning("Qdrant недоступен при старте: %s. Поиск будет возвращать 503.", exc.user_message)
    except Exception as exc:
        logging.warning("Не удалось инициализировать Qdrant при старте: %s", exc)

    def run_backfills():
        try:
            vs = VectorStore()
            vs.ensure_collection()
        except Exception:
            logging.warning("Бэкфиллы пропущены: Qdrant недоступен")
            return
        try:
            n = vs.backfill_sparse()
            if n:
                logging.info("Бэкфилл sparse-векторов: %d точек", n)
        except Exception:
            logging.exception("Бэкфилл sparse-векторов не удался — поиск BM25/гибрид может быть неполным")
        try:
            n = vs.backfill_relations()
            if n:
                logging.info("Бэкфилл relations: %d точек", n)
        except Exception:
            logging.exception("Бэкфилл relations не удался")
        try:
            from app.services.embedder import Embedder

            emb = Embedder()
            n = vs.backfill_chunks(emb)
            if n:
                logging.info("Бэкфилл чанков: %d точек", n)
        except Exception:
            logging.exception("Бэкфилл чанков не удался — поиск по чанкам может быть неполным")

    t = threading.Thread(target=run_backfills, daemon=True)
    t.start()

    # Автоочистка корзины (Этап 4a.2): фоновый демон-поток физически удаляет
    # документы с истёкшим окном хранения. Ленивый импорт — поток не роняет
    # сервер, если trash-сервис недоступен на старте.
    try:
        from app.services.trash import start_purge_loop

        start_purge_loop()
    except Exception as exc:
        logging.warning("Автоочистка корзины не запущена: %s", exc)

    # Автоочистка истории чата (Этап 6): фоновый демон-поток физически удаляет
    # soft-deleted треды по истечении окна хранения (chat_history_retention_days).
    try:
        from app.services.chat_history import start_chat_purge_loop

        start_chat_purge_loop()
    except Exception as exc:
        logging.warning("Автоочистка истории чата не запущена: %s", exc)

    yield


def create_app() -> FastAPI:
    settings = get_settings()
    get_store().ensure()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(CatchAllErrorsMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret_key,
        max_age=settings.auth_session_ttl_seconds,
        same_site="lax",
        https_only=settings.auth_session_https_only,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router, prefix=settings.api_prefix)

    # Защищённые роуты: в disabled-режиме require_user пропускает всех,
    # в simulation/sso — требует сессию (401 без неё).
    protected = APIRouter(dependencies=[Depends(require_user)])
    protected.include_router(documents.router)
    protected.include_router(search.router)
    protected.include_router(chat.router)
    protected.include_router(chat_history.router)
    protected.include_router(tags.router)
    protected.include_router(developments.router)
    protected.include_router(attributes.router)
    protected.include_router(settings_router)
    protected.include_router(jobs.router)
    protected.include_router(audit.router)
    protected.include_router(users.router)
    protected.include_router(locales.router)
    protected.include_router(admin_locales.router)
    app.include_router(protected, prefix=settings.api_prefix)

    @app.exception_handler(DependencyUnavailableError)
    async def dependency_error_handler(request: Request, exc: DependencyUnavailableError):
        return JSONResponse(
            status_code=503,
            content={
                "detail": exc.user_message,
                "code": "dependency_unavailable",
                "service": exc.service,
            },
        )

    @app.get("/health")
    def health() -> dict:
        return get_health()

    return app


app = create_app()
