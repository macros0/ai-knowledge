"""Глобальный справочник тегов (data/tags.json), накапливается от всех пользователей."""
import json
import threading

from app.config import get_settings


def normalize_tags(tags: list[str] | None) -> list[str]:
    """Обрезка пробелов, отсев пустых и дубликатов (порядок сохранён)."""
    if not tags:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for t in tags:
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result


class TagRegistry:
    def __init__(self):
        self.path = get_settings().data_dir / "tags.json"
        self._lock = threading.Lock()
        self._tags: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._tags = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self._tags = {}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._tags, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, tags: list[str] | None) -> None:
        normalized = normalize_tags(tags)
        if not normalized:
            return
        with self._lock:
            for tag in normalized:
                self._tags[tag] = self._tags.get(tag, 0) + 1
            self._save()

    def all(self) -> list[dict]:
        with self._lock:
            items = [{"name": name, "count": count} for name, count in self._tags.items()]
        items.sort(key=lambda t: t["name"].lower())
        return items
