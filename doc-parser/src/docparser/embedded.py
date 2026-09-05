"""Разбор встроенных объектов: OLE (.bin) и вложений PDF/xlsx, с рекурсией в parse_document.

docx / xlsx хранят встроенные файлы как OLE Compound Files (`word/embeddings/*.bin`,
`xl/embeddings/*.bin`). Для Office 2007+ внутри такого .bin лежит стрим `Package` —
полноценный OOXML-пакет (zip), который можно рекурсивно прогнать через наш парсер.
PDF-вложения извлекаются через `pypdf.reader.attachments`.
"""
import io
import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import olefile

from docparser.blocks import Block
from docparser.extensions import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)

# Анти-DoS (2026-09-01): цепочки вложений (docx → вложение → вложение → ...)
# и zip-бомбы множат объём работы/файлов на один загруженный файл неограниченно.
MAX_ATTACHMENT_DEPTH = 3  # уровней вложенности: уровни 1-2 разбираются, 3+ — только маркер
MAX_ATTACHMENT_PAYLOAD = 50 * 1024 * 1024   # 50 МБ — одно вложение (payload или сырое)
MAX_TOTAL_PAYLOAD = 200 * 1024 * 1024       # 200 МБ — суммарно распакованных вложений на документ

PROGID_EXT = {
    "Excel.Sheet.12": ".xlsx",
    "Excel.Sheet.8": ".xls",
    "Excel.Sheet.5": ".xls",
    "Word.Document.12": ".docx",
    "Word.Document.8": ".doc",
    "Word.Document": ".doc",
    "AcroExch.Document": ".pdf",
    "PowerPoint.Show.12": ".pptx",
}


class AttachmentBudget:
    """Кумулятивный лимит распакованных байтов вложений на один документ.

    Каждый parsed-payload вычитается из бюджета; исчерпание — вложения дальше
    не разбираются (маркер-блок), пайплайн не падает.
    """

    def __init__(self, total: int = MAX_TOTAL_PAYLOAD):
        self.remaining = total

    def consume(self, size: int) -> bool:
        if size > self.remaining:
            self.remaining = 0
            return False
        self.remaining -= size
        return True


@dataclass
class Attachment:
    name: str
    prog_id: str
    caption: str
    data: bytes
    kind: str = "other"  # zip | pdf | other
    saved_path: str | None = None


def unwrap_ole(data: bytes) -> tuple[str, bytes]:
    """Возвращает (kind, payload): kind — 'zip' | 'pdf' | 'other'."""
    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
        try:
            if ole.exists("Package"):
                payload = ole.openstream("Package").read()
                if _is_zip(payload):
                    return "zip", payload
            for stream in ("Ole10Native", "Ole", "CONTENTS"):
                if ole.exists(stream):
                    payload = ole.openstream(stream).read()
                    if stream == "Ole10Native":
                        payload = _unwrap_ole10_native(payload)
                    if payload.startswith(b"%PDF"):
                        return "pdf", payload
                    if _is_zip(payload):
                        return "zip", payload
        finally:
            ole.close()
    except Exception:
        pass
    if data.startswith(b"%PDF"):
        return "pdf", data
    if _is_zip(data):
        return "zip", data
    return "other", data


def detect_ooxml_ext(payload: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = set(zf.namelist())
    except Exception:
        return None
    if "word/document.xml" in names:
        return ".docx"
    if "xl/workbook.xml" in names:
        return ".xlsx"
    if "ppt/presentation.xml" in names:
        return ".pptx"
    return None


def process_embedded(
    data: bytes,
    name: str,
    prog_id: str = "",
    caption: str = "",
    attachments_dir: str | Path | None = None,
    index: int = 0,
    depth: int = 1,
    budget: AttachmentBudget | None = None,
) -> list[Block]:
    """Разбирает встроенный файл. Возвращает блоки (маркер + рекурсивно разобранный контент).

    depth — уровень вложенности (1 = прямое вложение документа). Глубже
    MAX_ATTACHMENT_DEPTH рекурсия не идёт — только маркер (анти-DoS).
    budget — кумулятивный лимит распакованных байтов вложений документа.
    """
    kind, payload = unwrap_ole(data)
    att = Attachment(name=name, prog_id=prog_id, caption=caption, data=data, kind=kind)

    if depth >= MAX_ATTACHMENT_DEPTH:
        logger.warning(
            "Вложение %r: глубина вложенности %d >= %d — не разбираем",
            name, depth, MAX_ATTACHMENT_DEPTH,
        )
        return [marker_block(att, note="превышена глубина вложенности")]

    if kind in ("zip", "pdf"):
        if len(payload) > MAX_ATTACHMENT_PAYLOAD or not (budget or _default_budget()).consume(
            len(payload)
        ):
            logger.warning(
                "Вложение %r: размер %d байт превышает лимит — не разбираем",
                name, len(payload),
            )
            return [marker_block(att, note="превышен лимит размера вложений")]
        ext = ".pdf" if kind == "pdf" else (detect_ooxml_ext(payload) or PROGID_EXT.get(prog_id, ""))
        if ext in SUPPORTED_EXTENSIONS:
            blocks = _parse_payload(payload, ext, att, attachments_dir, index, depth=depth, budget=budget)
            return [marker_block(att, parsed=True)] + blocks

    if attachments_dir is not None:
        if len(data) > MAX_ATTACHMENT_PAYLOAD:
            logger.warning(
                "Вложение %r: размер %d байт превышает лимит — сохранение пропущено",
                name, len(data),
            )
            return [marker_block(att, note="превышен лимит размера вложений")]
        att.saved_path = str(save_attachment(att, attachments_dir, index))
    return [marker_block(att)]


def marker_block(att: Attachment, note: str = "", parsed: bool = False) -> Block:
    meta: dict = {
        "attachment": True,
        "name": att.name,
        "prog_id": att.prog_id,
        "kind": att.kind,
        "caption": att.caption,
    }
    if note:
        meta["note"] = note
    if parsed:
        meta["parsed"] = True
    if att.saved_path:
        meta["saved_path"] = att.saved_path
    label = att.name or "вложение"
    text = f"Вложение: {label} ({att.kind})"
    if note:
        text += f" — {note}"
    return Block("attachment", text, meta=meta)


def save_attachment(att: Attachment, dest_dir: str | Path, index: int = 0) -> Path:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    ext = PROGID_EXT.get(att.prog_id, _ext_from_kind(att.kind))
    target = _unique(dest / f"{_base_name(att.name, index)}{ext}")
    target.write_bytes(att.data)
    return target


def save_image_file(
    data: bytes,
    dest_dir: str | Path | None,
    index: int,
    preferred_name: str = "",
    ext: str = "",
) -> Path | None:
    """Сохраняет извлечённое изображение с уникальным именем `image-{index}{ext}`.

    Если dest_dir не задан — файл не пишется, возвращается None.
    """
    if dest_dir is None:
        return None
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    if not ext:
        ext = Path(preferred_name).suffix.lower() or ".png"
    target = _unique(dest / f"image-{index}{ext}")
    target.write_bytes(data)
    return target


# ---------------------------------------------------------------- internal
def _default_budget() -> AttachmentBudget:
    """Budget для прямых вызовов process_embedded без парсера-родителя."""
    return AttachmentBudget()


def _parse_payload(
    payload: bytes,
    ext: str,
    att: Attachment,
    attachments_dir,
    index: int,
    *,
    depth: int = 1,
    budget: AttachmentBudget | None = None,
) -> list[Block]:
    from docparser.parser import parse_document  # локальный импорт — избегаем цикла

    name = f"{_base_name(att.name, index)}{ext}"
    path = _write_file(payload, name, attachments_dir)
    if attachments_dir is not None:
        att.saved_path = str(path)
    try:
        return parse_document(
            path, filename=name, attachments_dir=attachments_dir, depth=depth, budget=budget
        )
    finally:
        if attachments_dir is None:
            path.unlink(missing_ok=True)


def _write_file(payload: bytes, name: str, attachments_dir) -> Path:
    if attachments_dir is not None:
        dest = Path(attachments_dir)
        dest.mkdir(parents=True, exist_ok=True)
        path = _unique(dest / name)
        path.write_bytes(payload)
        return path
    fd, tmp = tempfile.mkstemp(suffix=Path(name).suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)
    return Path(tmp)


def _is_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"


def _unwrap_ole10_native(payload: bytes) -> bytes:
    """Office 97-2003 embedding: 2 байта длины заголовка + данные оригинального файла."""
    if len(payload) < 4:
        return payload
    cb = int.from_bytes(payload[:2], "little")
    if cb < 2 or 2 + cb > len(payload):
        return payload
    return payload[2 + cb :]


def _ext_from_kind(kind: str) -> str:
    if kind == "zip":
        return ".zip"
    if kind == "pdf":
        return ".pdf"
    return ".bin"


def _base_name(name: str, index: int) -> str:
    stem = Path(name).stem or ""
    if not stem or stem.lower().startswith("oleobject"):
        stem = f"embedded-{index}"
    return stem


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 1000):
        cand = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not cand.exists():
            return cand
    return path
