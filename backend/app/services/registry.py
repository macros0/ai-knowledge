"""Простой персистентный реестр документов (JSON-файл в data/)."""
import json
import logging
import threading
from datetime import datetime, timezone

from app.config import get_settings
from app.services.jsonio import write_json_atomic

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentRegistry:
    def __init__(self):
        self.path = get_settings().data_dir / "documents.json"
        self._lock = threading.Lock()
        self._docs: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Реестр %s не разобран (%s) — стартуем с пустым", self.path, exc)
                raw = {}
            self._docs = self._sane_entries(raw)
        self._reset_stale_statuses()
        self._reconcile_okf_counts()

    def _sane_entries(self, raw: object) -> dict[str, dict]:
        """Отбрасывает записи неверной формы.

        Битый JSON конструктор переживал и раньше, а вот валидный JSON неверной
        формы ронял его — и вместе с ним весь backend: get_registry() вызывается
        на уровне модуля в api/documents.py, так что сервис не стартовал вовсе и
        отдавал трейсбек вместо объяснения. documents.json при этом ровно тот
        файл, который правят руками.
        """
        if not isinstance(raw, dict):
            logger.warning(
                "Реестр %s: ожидался объект, получен %s — стартуем с пустым реестром",
                self.path,
                type(raw).__name__,
            )
            return {}
        good = {k: v for k, v in raw.items() if isinstance(v, dict)}
        dropped = sorted(set(raw) - set(good))
        if dropped:
            logger.warning(
                "Реестр %s: записи неверной формы пропущены и будут удалены из файла "
                "при следующей записи: %s",
                self.path,
                ", ".join(dropped),
            )
        return good

    def _reconcile_okf_counts(self) -> None:
        """Держит okf_concept_count в синхроне с фактическим числом .md-файлов бандла.

        Миграция со старого поля okf_file_count (где могло храниться число чанков
        из-за регрессии) — пересчёт из производных данных на диске.
        """
        okf_dir = get_settings().okf_dir
        changed = False
        for doc_id, doc in self._docs.items():
            if doc.get("status") != "done":
                continue
            # ключ словаря и есть doc_id (см. create) — подстраховка на случай
            # записи без "id" после ручной правки или частичного восстановления
            bundle = okf_dir / (doc.get("id") or doc_id)
            count = len(list(bundle.glob("*.md"))) if bundle.is_dir() else 0
            if doc.get("okf_concept_count") != count:
                doc["okf_concept_count"] = count
                changed = True
            if "okf_file_count" in doc:
                doc.pop("okf_file_count")
                changed = True
        if changed:
            self._save()

    def _reset_stale_statuses(self) -> None:
        stale = {"splitting", "processing", "indexing", "uploaded"}
        changed = False
        for doc in self._docs.values():
            if doc.get("status") in stale:
                doc["status"] = "paused"
                doc["error"] = "Сервер был перезапущен. Нажмите «Возобновить»"
                changed = True
        if changed:
            self._save()

    def _save(self) -> None:
        write_json_atomic(self.path, self._docs)

    def create(self, doc_id: str, filename: str, content_type: str, size: int, tags: list[str] | None = None) -> dict:
        doc = {
            "id": doc_id,
            "filename": filename,
            "content_type": content_type,
            "size": size,
            "status": "uploaded",
            "error": None,
            "okf_concept_count": 0,
            "total_chunks": 0,
            "processed_chunks": 0,
            "current_chunk": 0,
            "tags": tags or [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self._lock:
            self._docs[doc_id] = doc
            self._save()
        return doc

    def get(self, doc_id: str) -> dict | None:
        return self._docs.get(doc_id)

    def list(self) -> list[dict]:
        return list(self._docs.values())

    def update(self, doc_id: str, **fields) -> None:
        with self._lock:
            if doc_id in self._docs:
                self._docs[doc_id].update(fields)
                self._docs[doc_id]["updated_at"] = _now()
                self._save()

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            existed = self._docs.pop(doc_id, None) is not None
            if existed:
                self._save()
        return existed


_INSTANCE: DocumentRegistry | None = None
_INSTANCE_LOCK = threading.Lock()


def get_registry() -> DocumentRegistry:
    """Общий для процесса реестр — один инстанс на всех потребителей."""
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = DocumentRegistry()
    return _INSTANCE
