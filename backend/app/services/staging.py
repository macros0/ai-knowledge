"""Staging-хранилище инкрементальной генерации OKF (checkpointing/resume).

Состояние задачи (manifest) хранится в реляционной БД (document_staging), сырые
файлы чанков — в FS под data/staging/{doc_id}/:
  chunk_XX.json — нормализованные концепты чанка (JSON-массив dict)
  chunk_XX.md   — сырой текст чанка (LLM-вход)

Результаты каждого чанка пишутся сразу после генерации LLM, поэтому при падении
обработанные чанки сохраняются, а повторный запуск пропускает их (has_chunk /
processed_chunks). При финализации концепты читаются из чанков в порядке
индексов, бандл собирается в okf_bundles, staging удаляется.

Замена data/staging/{doc_id}/manifest.json → таблица document_staging (JSONB),
как в MIGRATION_PLAN.md §3.3/§5.
"""
from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.db.models import DocumentStaging
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.json_atomic import write_json_atomic
from app.services.okf_generator import _slugify


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Лок на append_chunk общий для всех экземпляров StagingStore одного doc_id:
# per-instance threading.Lock() не защищал бы при двух потоках пайплайна на один
# staging (у каждого экземпляра был бы свой лок). Ключ — doc_id.
_STAGING_LOCKS: dict[str, threading.Lock] = {}
_STAGING_LOCKS_GUARD = threading.Lock()


def _staging_lock(doc_id: str) -> threading.Lock:
    with _STAGING_LOCKS_GUARD:
        lock = _STAGING_LOCKS.get(doc_id)
        if lock is None:
            lock = threading.Lock()
            _STAGING_LOCKS[doc_id] = lock
        return lock


class StagingStore:
    def __init__(self, doc_id: str, staging_root: Path | None = None):
        self.doc_id = doc_id
        self.dir = staging_root or get_settings().staging_dir / doc_id
        self._lock = _staging_lock(doc_id)

    def exists(self) -> bool:
        return self.load() is not None

    def create(self, total_chunks: int, global_tags: list[str] | None = None) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "task_id": self.doc_id,
            "status": "in_progress",
            "total_chunks": total_chunks,
            "last_updated": _now(),
            "processed_chunks": [],
            "used_slugs": [],
            "global_tags": global_tags or [],
            "chunks_data": {},
        }
        self._write(manifest)
        return manifest

    def load(self) -> dict | None:
        with session_scope() as s:
            row = s.get(DocumentStaging, self.doc_id)
            if row is None:
                return None
            return {
                "task_id": row.doc_id,
                "status": row.status,
                "total_chunks": row.total_chunks,
                "last_updated": row.updated_at.isoformat() if row.updated_at else None,
                "processed_chunks": list(row.processed_chunks or []),
                "used_slugs": list(row.used_slugs or []),
                "global_tags": list(row.global_tags or []),
                "chunks_data": dict(row.chunks_data or {}),
            }

    def _write(self, manifest: dict) -> None:
        with session_scope() as s:
            row = s.get(DocumentStaging, self.doc_id)
            if row is None:
                row = DocumentStaging(doc_id=self.doc_id)
                s.add(row)
            row.total_chunks = manifest.get("total_chunks", 0)
            row.processed_chunks = list(manifest.get("processed_chunks", []))
            row.used_slugs = list(manifest.get("used_slugs", []))
            row.global_tags = list(manifest.get("global_tags", []))
            row.chunks_data = dict(manifest.get("chunks_data", {}))
            row.status = manifest.get("status", "in_progress")

    @property
    def processed_chunks(self) -> list[int]:
        manifest = self.load()
        if not manifest:
            return []
        return sorted(manifest.get("processed_chunks", []))

    def has_chunk(self, index: int) -> bool:
        return index in self.processed_chunks

    def save_chunk_text(self, index: int, text: str) -> None:
        """Сохраняет сырой текст чанка (LLM-вход) для просмотра в UI.

        Файлы chunk_XX.md (текст) не конфликтуют с chunk_XX.json (концепты).
        Идемпотентно: повторная запись перезаписывает тот же текст.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"chunk_{index:02d}.md").write_text(text, encoding="utf-8")

    def append_chunk(
        self,
        index: int,
        concepts: list[Concept],
        degradation: list[dict] | None = None,
    ) -> list[str]:
        """Сохраняет концепты чанка и обновляет manifest. Возвращает занятые слаги.

        degradation — события деградации генерации этого чанка (телеметрия
        gen_quality: salvage JSON, fallback классификатора таблиц). Хранятся
        в chunks_data, при финализации агрегируются в documents.problem.
        """
        with self._lock:
            manifest = self.load() or self.create(index + 1, global_tags=[])
            processed = list(manifest.get("processed_chunks", []))
            if index not in processed:
                processed.append(index)
                processed.sort()
            used = list(manifest.get("used_slugs", []))
            slugs = self._allocate_slugs(concepts, used)
            used.extend(slugs)
            chunk_file = f"chunk_{index:02d}.json"
            write_json_atomic(self.dir / chunk_file, [c.model_dump() for c in concepts])
            chunks_data = dict(manifest.get("chunks_data", {}))
            info = {"file": chunk_file, "concepts_count": len(concepts), "slugs": slugs}
            if degradation:
                info["degradation"] = degradation
            chunks_data[str(index)] = info
            manifest.update(
                {
                    "processed_chunks": processed,
                    "used_slugs": used,
                    "chunks_data": chunks_data,
                    "status": "in_progress",
                }
            )
            self._write(manifest)
            return slugs

    def concepts(self) -> list[Concept]:
        manifest = self.load()
        if not manifest:
            return []
        result: list[Concept] = []
        for index in sorted(int(k) for k in manifest.get("chunks_data", {})):
            info = manifest["chunks_data"][str(index)]
            path = self.dir / info["file"]
            if not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for item in raw:
                if isinstance(item, dict):
                    try:
                        result.append(Concept(**item))
                    except Exception:
                        continue
        return result

    def slugs(self) -> list[str]:
        manifest = self.load() or {}
        slugs: list[str] = []
        for index in sorted(int(k) for k in manifest.get("chunks_data", {})):
            slugs.extend(manifest["chunks_data"][str(index)].get("slugs", []))
        return slugs

    def remove(self) -> None:
        with session_scope() as s:
            row = s.get(DocumentStaging, self.doc_id)
            if row is not None:
                s.delete(row)
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)

    @staticmethod
    def _allocate_slugs(concepts: list[Concept], used: list[str]) -> list[str]:
        """Дедупликация слагов в процессе: коллизии получают суффикс -1, -2, ..."""
        used_set = set(used)
        slugs: list[str] = []
        for concept in concepts:
            base = _slugify(concept.title)
            slug = base
            n = 1
            while slug in used_set:
                slug = f"{base}-{n}"
                n += 1
            used_set.add(slug)
            slugs.append(slug)
        return slugs
