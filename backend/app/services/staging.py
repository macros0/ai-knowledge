"""Staging-хранилище инкрементальной генерации OKF (checkpointing/resume).

Задача (doc_id) занимает каталог data/staging/{doc_id}/:
  manifest.json  — состояние: обработанные чанки, занятые слаги, теги
  chunk_XX.json  — нормализованные концепты чанка (JSON-массив dict)

Результаты каждого чанка пишутся на диск сразу после генерации LLM, поэтому
при падении на N-м чанке обработанные чанки сохраняются, а повторный запуск
пропускает их (has_chunk / processed_chunks). При финализации концепты
читаются из чанков в порядке индексов, бандл собирается в okf_bundles,
а staging удаляется.
"""
import json
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.models.schemas import Concept
from app.services.jsonio import write_json_atomic
from app.services.okf_generator import _slugify


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Локи на каталог staging, общие для всех StagingStore одного документа.
# Экземпляров на документ много — пайплайн пишет, эндпоинты читают, — и лок
# в поле экземпляра не даёт взаимного исключения вообще: у каждого свой.
_DIR_LOCKS: dict[str, threading.RLock] = {}
_DIR_LOCKS_GUARD = threading.Lock()


def _lock_for(directory: Path) -> threading.RLock:
    key = str(directory)
    with _DIR_LOCKS_GUARD:
        lock = _DIR_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _DIR_LOCKS[key] = lock
        return lock


class StagingStore:
    def __init__(self, doc_id: str, staging_root: Path | None = None):
        self.doc_id = doc_id
        self.dir = staging_root or get_settings().staging_dir / doc_id
        self.manifest_path = self.dir / "manifest.json"
        # RLock: append_chunk держит лок и внутри может позвать create()
        self._lock = _lock_for(self.dir)

    def exists(self) -> bool:
        return self.manifest_path.is_file()

    def create(self, total_chunks: int, global_tags: list[str] | None = None) -> dict:
        with self._lock:
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
        if not self.exists():
            return None
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write(self, manifest: dict) -> None:
        manifest["last_updated"] = _now()
        # manifest.json — единственная запись о том, какие чанки уже сгенерированы;
        # усечь его при обрыве значит потерять ровно ту точку восстановления,
        # ради которой существует staging
        write_json_atomic(self.manifest_path, manifest)

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

    def append_chunk(self, index: int, concepts: list[Concept]) -> list[str]:
        """Сохраняет концепты чанка и обновляет manifest. Возвращает занятые слаги."""
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
            # сначала данные, затем manifest, который на них ссылается
            write_json_atomic(self.dir / chunk_file, [c.model_dump() for c in concepts])
            chunks_data = dict(manifest.get("chunks_data", {}))
            chunks_data[str(index)] = {"file": chunk_file, "concepts_count": len(concepts), "slugs": slugs}
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
        with self._lock:
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