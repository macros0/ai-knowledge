"""Простой персистентный реестр документов (JSON-файл в data/)."""
import json
import threading
from datetime import datetime, timezone

from app.config import get_settings


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
                self._docs = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._docs = {}
        self._reset_stale_statuses()

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
        self.path.write_text(json.dumps(self._docs, ensure_ascii=False, indent=2), encoding="utf-8")

    def create(self, doc_id: str, filename: str, content_type: str, size: int, tags: list[str] | None = None) -> dict:
        doc = {
            "id": doc_id,
            "filename": filename,
            "content_type": content_type,
            "size": size,
            "status": "uploaded",
            "error": None,
            "okf_file_count": 0,
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
