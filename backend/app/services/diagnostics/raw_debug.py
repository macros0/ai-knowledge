"""Separate development-only raw LLM output. Never an input to diagnostic ZIPs."""
from datetime import datetime, timezone
import re
from uuid import uuid4

from .schema import DiagnosticLimits, MIB
from .store import DiagnosticStore

RAW_BYTES = 20 * MIB
RAW_TTL_SECONDS = 24 * 3600
_OWNED_FILE = re.compile(r"llm_raw_[0-9a-f]{32}\.txt\Z")


def _store(settings):
    # Only the backend subtree is used by this independent development store.
    # Its byte accounting includes the lock, temporary files and unknown files.
    limits = DiagnosticLimits(total_bytes=40 * MIB, backend_bytes=RAW_BYTES,
                              frontend_bytes=20 * MIB, baseline_bytes=RAW_BYTES,
                              session_bytes=RAW_BYTES, bundle_bytes=RAW_BYTES)
    return DiagnosticStore(settings.data_dir / "dev-llm-debug", limits)


def _sweep(store, now):
    deleted = 0
    for path in store.root.iterdir():
        path = store.safe_path(path)
        if _OWNED_FILE.fullmatch(path.name) and path.is_file() and now.timestamp() - path.stat().st_mtime >= RAW_TTL_SECONDS:
            path.unlink()
            store.note_deleted(path)
            deleted += 1
    return deleted


def sweep_raw_debug(settings, *, now=None):
    """Maintenance may expire owned files even after development opt-in is off."""
    if getattr(settings, "environment", None) != "development":
        return 0
    if not (settings.data_dir / "dev-llm-debug").exists():
        return 0
    try:
        with _store(settings) as store:
            return _sweep(store, now or datetime.now(timezone.utc))
    except Exception:
        return 0


def write_raw_debug(settings, text):
    if (getattr(settings, "environment", None) != "development"
            or not getattr(settings, "llm_raw_debug_enabled", False)
            or not isinstance(text, str) or len(text) > RAW_BYTES):
        return False
    try:
        encoded = text.encode("utf-8")
        if len(encoded) > RAW_BYTES:
            return False
        with _store(settings) as store:
            _sweep(store, datetime.now(timezone.utc))
            store.write_bytes(store.root / f"llm_raw_{uuid4().hex}.txt", encoded)
        return True
    except Exception:
        # Diagnostics must not replace the original parse error or expose paths.
        return False
