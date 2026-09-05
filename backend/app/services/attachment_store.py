"""Хранилище вложений в реляционной БД (Этап 2b: PostgreSQL SSOT).

saved_path — относительный путь от корня хранилища документа (uploads/<doc_id>/),
например "attachments/appendix.docx". Байты остаются в локальной FS; здесь —
описание, принадлежность, размер, SHA-256 и статус обработки. sha256/size
считаются потоково (блоками по 1 МБ) — файл целиком в память не грузится.
"""
from __future__ import annotations

import hashlib
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from app.db.models import OkfAttachment

_STREAM_CHUNK = 1024 * 1024


def _guess_content_type(name: str) -> str | None:
    ctype, _ = mimetypes.guess_type(name)
    return ctype


def _file_stats(path: Path) -> tuple[int | None, str | None]:
    if not path.is_file():
        return None, None
    size = path.stat().st_size
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_STREAM_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return size, digest.hexdigest()


def replace_attachments(
    session,
    doc_id: str,
    attachments: list[dict],
    storage_root: Path,
) -> None:
    """Заменяет вложения документа (delete + insert) в переданной сессии.

    attachments: [{name, kind, caption, saved_path, is_processable,
    extraction_status}, ...]. saved_path — относительный; size/sha256/content_type
    считаются потоково с диска (storage_root / saved_path). Дедуп по saved_path
    (last wins) защищает UNIQUE(doc_id, saved_path).
    """
    session.query(OkfAttachment).filter(OkfAttachment.doc_id == doc_id).delete(
        synchronize_session=False
    )
    by_path: dict[str, dict] = {}
    for a in attachments:
        sp = a.get("saved_path")
        if sp:
            by_path[sp] = a
    now = datetime.now(timezone.utc)
    for saved_path, a in by_path.items():
        size, sha = _file_stats(storage_root / saved_path)
        is_processable = bool(a.get("is_processable", False))
        session.add(
            OkfAttachment(
                doc_id=doc_id,
                name=a.get("name", ""),
                kind=a.get("kind", "other"),
                caption=a.get("caption", ""),
                saved_path=saved_path,
                content_type=_guess_content_type(str(saved_path)),
                size=size,
                sha256=sha,
                is_processable=is_processable,
                extraction_status=a.get("extraction_status"),
                processed_at=now if is_processable else None,
            )
        )
