# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT
"""Консервативная семантическая идентичность извлечённого письма.

Отпечаток предназначен для *кандидата* похожего документа, никогда не для
hard reject: byte-identical upload по-прежнему определяется file_hash. Поля
нормализуются только на уровне Unicode и переноса строк; подписи, числа,
история переписки и пробелы внутри строк тела сохраняются значимыми.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from email.header import decode_header, make_header
from email.utils import getaddresses
from pathlib import Path
from typing import Any


_HEADER_FIELDS = ("subject", "sender", "to", "cc", "sent_at", "message_id")


def _text(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")


def _header(value: object) -> str | list[str]:
    if isinstance(value, (list, tuple)):
        # Address order is structural header data. Normalize each value but do
        # not sort them: recipient order can be meaningful in the source.
        return [_text(item) for item in value]
    return _text(value)


def _decoded(value: object) -> str:
    raw = _text(value)
    try:
        return _text(str(make_header(decode_header(raw))))
    except (LookupError, UnicodeError, ValueError):
        return raw


def _addresses(value: object) -> list[str]:
    values = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for raw in values:
        decoded = _decoded(raw)
        parsed = getaddresses([decoded])
        if not parsed:
            out.append(decoded)
            continue
        for name, address in parsed:
            if address:
                display = _text(name)
                out.append(f"{display} <{address}>" if display else address)
            elif decoded:
                out.append(decoded)
    return out


def normalized_mail_identity(
    envelope: Mapping[str, Any], full_body: str, attachment_hashes: Iterable[str]
) -> dict[str, object]:
    """Строит сериализуемую identity без эвристической потери бизнес-текста.

    Hashes вложений сортируются с сохранением повторов. Пустой или неполный
    список нельзя подменять догадкой: вызывающий код обязан использовать
    отпечаток только когда ему известны bytes всех учитываемых вложений.
    """
    headers = {name: _header(envelope.get(name)) for name in _HEADER_FIELDS}
    for name in ("sender", "to", "cc"):
        headers[name] = _addresses(envelope.get(name))
    hashes = Counter(_text(value) for value in attachment_hashes)
    return {
        "headers": headers,
        "body": _text(full_body),
        "attachments": sorted(hashes.items()),
    }


def mail_fingerprint(envelope: Mapping[str, Any], full_body: str, attachment_hashes: Iterable[str]) -> str:
    """SHA-256 canonical mail identity for similar-document review candidates."""
    payload = json.dumps(
        normalized_mail_identity(envelope, full_body, attachment_hashes),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def mail_fingerprint_from_parse(sources, blocks, attachments_dir: Path) -> str | None:
    """Возвращает отпечаток standalone-mail только при полной известной структуре.

    Если парсер пропустил хотя бы одно вложение из-за лимита/неподдерживаемого
    формата или путь вышел за временное хранилище, кандидат не создаётся.
    Это предотвращает ложное совпадение двух писем по неполному извлечению.
    """
    root = next((source for source in sources if source.source_id == "root"), None)
    metadata = getattr(root, "metadata", {}) if root is not None else {}
    if not metadata.get("mail"):
        return None
    if any(getattr(source, "warnings", None) for source in sources):
        return None

    root_dir = attachments_dir.resolve()
    hashes: list[str] = []
    for block in blocks:
        meta = getattr(block, "meta", {}) or {}
        if not meta.get("attachment"):
            continue
        if meta.get("extraction_status") not in {"parsed", "saved"}:
            return None
        saved = meta.get("saved_path")
        if not saved:
            return None
        try:
            path = Path(saved).resolve()
            path.relative_to(root_dir)
        except (OSError, ValueError):
            return None
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes.append(digest.hexdigest())

    body = "\n\n".join(
        block.text
        for block in blocks
        if (getattr(block, "meta", {}) or {}).get("source_id") == "root"
        and getattr(block, "type", "") not in {"heading", "attachment"}
    )
    return mail_fingerprint(metadata, body, hashes)
