"""Хранилище промптов с каскадным разрешением и горячей перезагрузкой.

Порядок поиска промпта по ключу:
1. <data_dir>/prompts/<key>.md  — runtime-оверрайд (правится без рестарта, gitignored);
2. <backend>/prompts/<key>.md   — канонический файл (версионируется в git);
3. Дефолт-константа из okf.py   — всегда валидный fallback.

Перезагрузка происходит по изменению mtime файла при каждом обращении к get().
Битый файл (пустой, не-UTF-8, OSError) не перезаписывает кэш: возвращается
предыдущее валидное значение или выполняется переход к следующему уровню каскада.
"""
import logging
import re
import threading
from pathlib import Path
from typing import Final, Mapping

from app.prompts import okf as _defaults

logger = logging.getLogger(__name__)

PROMPT_DEFAULTS: Final = {
    "chat_system": _defaults.SYSTEM_CHAT_PROMPT,
    "chat_user": _defaults.USER_CHAT_PROMPT,
    "okf_system": _defaults.SYSTEM_OKF_PROMPT,
    "okf_user": _defaults.USER_OKF_PROMPT,
    "okf_chunk": _defaults.CHUNK_OKF_PROMPT,
}

REQUIRED_PLACEHOLDERS: Final = {
    "chat_system": (),
    "chat_user": ("context", "query"),
    "okf_system": (),
    "okf_user": ("filename", "content"),
    "okf_chunk": ("filename", "index", "total", "content"),
}

CANONICAL_DIR: Final = Path(__file__).resolve().parents[2] / "prompts"

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class _SafeDict(dict):
    """dict без KeyError: неизвестные плейсхолдеры сохраняются дословно."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class PromptStore:
    def __init__(self, override_dir: Path | None = None, canonical_dir: Path | None = None):
        self.canonical_dir = Path(canonical_dir) if canonical_dir else CANONICAL_DIR
        self._override_dir = Path(override_dir) if override_dir is not None else None
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def override_dir(self) -> Path:
        if self._override_dir is None:
            from app.config import get_settings

            self._override_dir = get_settings().data_dir / "prompts"
        return self._override_dir

    def get(self, key: str) -> str:
        if key not in PROMPT_DEFAULTS:
            raise KeyError(f"Unknown prompt key: {key!r}")
        with self._lock:
            return self._load_locked(key)

    def format(self, key: str, **fields: object) -> str:
        raw = self.get(key)
        required = REQUIRED_PLACEHOLDERS[key]
        if not _has_placeholders(raw, required):
            logger.warning(
                "Prompt '%s' не содержит обязательные плейсхолдеры %s — использую дефолт из кода",
                key,
                required,
            )
            raw = PROMPT_DEFAULTS[key]
        return _safe_format(raw, fields)

    def ensure(self) -> None:
        """Создаёт override-каталог и сидирует канонические файлы из дефолтов, если их нет."""
        self.override_dir.mkdir(parents=True, exist_ok=True)
        for key, default in PROMPT_DEFAULTS.items():
            path = self.canonical_dir / f"{key}.md"
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(default.strip() + "\n", encoding="utf-8")

    def _load_locked(self, key: str) -> str:
        for source in (self.override_dir / f"{key}.md", self.canonical_dir / f"{key}.md"):
            cached = self._cache.get(key)
            if not source.exists():
                if cached is not None and cached.get("path") == source:
                    self._cache.pop(key, None)
                continue
            mtime = source.stat().st_mtime
            if cached is not None and cached.get("path") == source and cached.get("mtime") == mtime:
                return cached["value"]
            value = _read_file(source)
            if value is None:
                if cached is not None and cached.get("path") == source:
                    logger.warning("Prompt file %s битый — возвращаю предыдущее валидное значение", source)
                    return cached["value"]
                logger.warning("Prompt file %s битый или пустой — пропускаю", source)
                continue
            self._cache[key] = {"path": source, "mtime": mtime, "value": value}
            return value
        cached = self._cache.get(key)
        if cached is not None:
            return cached["value"]
        self._cache[key] = {"path": None, "mtime": None, "value": PROMPT_DEFAULTS[key]}
        return PROMPT_DEFAULTS[key]


def _read_file(path: Path) -> str | None:
    try:
        data = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not data:
        return None
    return data


def _has_placeholders(template: str, required: tuple[str, ...]) -> bool:
    found = set(_PLACEHOLDER_RE.findall(template))
    return all(r in found for r in required)


def _safe_format(template: str, fields: Mapping[str, object]) -> str:
    try:
        return template.format_map(_SafeDict(fields))
    except (KeyError, ValueError, IndexError):
        logger.warning("Ошибка форматирования промпта — возвращаю шаблон без подстановки")
        return template


_store: PromptStore | None = None
_store_lock = threading.Lock()


def get_store() -> PromptStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = PromptStore()
    return _store