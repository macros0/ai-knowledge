"""Legacy writer commands cannot silently operate beside distributed writers."""
import re


def require_single_process_writer(operation, *, settings=None):
    from app.config import get_settings
    from .admission import durable_documents
    if not isinstance(operation, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", operation) is None:
        raise ValueError("Invalid operator command identity")
    if durable_documents(settings if settings is not None else get_settings()):
        raise SystemExit(f"{operation}: distributed maintenance protocol required; this legacy writer is disabled")
