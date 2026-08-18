import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, documents, search, tags
from app.api.settings import router as settings_router
from app.config import get_settings
from app.prompts.store import get_store
from app.services.vector_store import VectorStore

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    vs = VectorStore()
    vs.ensure_collection()
    try:
        n = vs.backfill_sparse()
        if n:
            logging.info("Бэкфилл sparse-векторов: %d точек", n)
    except Exception:
        logging.exception("Бэкфилл sparse-векторов не удался — поиск BM25/гибрид может быть неполным")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    get_store().ensure()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(documents.router, prefix=settings.api_prefix)
    app.include_router(search.router, prefix=settings.api_prefix)
    app.include_router(chat.router, prefix=settings.api_prefix)
    app.include_router(tags.router, prefix=settings.api_prefix)
    app.include_router(settings_router, prefix=settings.api_prefix)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
