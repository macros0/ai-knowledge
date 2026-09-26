"""Разбор встроенных объектов: OLE (.bin) и вложений PDF/xlsx, с рекурсией в parse_document.

docx / xlsx хранят встроенные файлы как OLE Compound Files (`word/embeddings/*.bin`,
`xl/embeddings/*.bin`). Для Office 2007+ внутри такого .bin лежит стрим `Package` —
полноценный OOXML-пакет (zip), который можно рекурсивно прогнать через наш парсер.
PDF-вложения извлекаются через `pypdf.reader.attachments`.
"""
import io
import logging
import os
import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import olefile

from docparser.blocks import Block
from docparser.extensions import SUPPORTED_EXTENSIONS
from docparser.mail_images import raster_extension
from docparser.paths import portable_name
from docparser.storage_errors import is_storage_full_error

logger = logging.getLogger(__name__)

# Анти-DoS (2026-09-01): цепочки вложений (docx → вложение → вложение → ...)
# и zip-бомбы множат объём работы/файлов на один загруженный файл неограниченно.
MAX_ATTACHMENT_DEPTH = 3  # уровней вложенности: уровни 1-2 разбираются, 3+ — только маркер
MAX_ATTACHMENT_PAYLOAD = 50 * 1024 * 1024   # 50 МБ — одно вложение (payload или сырое)
MAX_TOTAL_PAYLOAD = 200 * 1024 * 1024       # 200 МБ — суммарно распакованных вложений на документ
MAX_ATTACHMENT_NODES = 1_000                # включает маленькие MIME/MSG части
MAX_TEXT_CHARS = 2_000_000                  # канонический текст одного документа до индексации

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

    def __init__(
        self,
        total: int = MAX_TOTAL_PAYLOAD,
        max_nodes: int = MAX_ATTACHMENT_NODES,
        max_text_chars: int | None = None,
    ):
        self.remaining = total
        self.remaining_nodes = max_nodes
        self.remaining_text = MAX_TEXT_CHARS if max_text_chars is None else max_text_chars

    def reserve_node(self) -> bool:
        if self.remaining_nodes <= 0:
            return False
        self.remaining_nodes -= 1
        return True

    def consume(self, size: int) -> bool:
        if size > self.remaining:
            self.remaining = 0
            return False
        self.remaining -= size
        return True

    def consume_text(self, char_count: int) -> int:
        """Reserve canonical Unicode characters and return the permitted prefix length."""
        permitted = min(max(char_count, 0), self.remaining_text)
        self.remaining_text -= permitted
        return permitted



class AttachmentStorageError(RuntimeError):
    """The parser refused a destination that could escape its storage root."""


@dataclass
class Attachment:
    name: str
    prog_id: str
    caption: str
    data: bytes
    kind: str = "other"  # zip | pdf | other
    saved_path: str | None = None
    source_id: str | None = None


def attachment_count_marker(context=None, parent_source_id: str = "root") -> Block:
    """One diagnostic for a container's unvisited remainder, without reading it."""
    att = Attachment("Остальные вложения", "", "", b"")
    if context is not None:
        att.source_id = context.add_child(parent_source_id, att.name, "attachment")
        context.warn(att.source_id, "attachment_count_exceeded")
    return marker_block(att, note="превышен лимит количества вложений", extraction_status="skipped_count")


def attachment_size_marker(name: str, context=None, parent_source_id: str = "root") -> Block:
    """Reject a declared oversized payload without reading or saving it."""
    att = Attachment(name, "", "", b"")
    if context is not None:
        att.source_id = context.add_child(parent_source_id, name, "attachment")
        context.warn(att.source_id, "attachment_size_exceeded")
    return marker_block(att, note="превышен лимит размера вложений", extraction_status="skipped_size")


def attachment_parse_marker(name: str, context=None, parent_source_id: str = "root") -> Block:
    """Retain a failed child without cancelling readable siblings."""
    att = Attachment(name, "", "", b"")
    if context is not None:
        att.source_id = context.add_child(parent_source_id, name, "attachment")
        context.warn(att.source_id, "attachment_parse_failed")
    return marker_block(att, note="не удалось извлечь вложение", extraction_status="skipped_parse")


def unwrap_ole(data: bytes) -> tuple[str, bytes]:
    """Возвращает (kind, payload): kind — 'zip' | 'pdf' | 'other'."""
    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
        try:
            for stream in ("Package", "\x01Ole10Native", "Ole10Native", "Ole", "CONTENTS"):
                if ole.exists(stream):
                    payload = ole.openstream(stream).read()
                    if stream in {"\x01Ole10Native", "Ole10Native"}:
                        payload = _unwrap_ole10_native(payload)
                    if payload is None:
                        continue
                    if payload.startswith(b"%PDF"):
                        return "pdf", payload
                    if _is_zip(payload):
                        return "zip", payload
                    if _looks_like_eml(payload) or _looks_like_msg(payload):
                        return "other", payload
        finally:
            ole.close()
    except Exception:  # noqa: BLE001, S110 - malformed OLE is an opaque attachment
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
    except Exception:  # noqa: BLE001 - malformed ZIP is not an OOXML attachment
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
    context=None,
    parent_source_id: str = "root",
    _node_reserved: bool = False,
    inline_image: bool = False,
) -> list[Block]:
    """Разбирает встроенный файл. Возвращает блоки (маркер + рекурсивно разобранный контент).

    depth — уровень вложенности (1 = прямое вложение документа). Глубже
    MAX_ATTACHMENT_DEPTH рекурсия не идёт — только маркер (анти-DoS).
    budget — кумулятивный лимит распакованных байтов вложений документа.
    """
    effective_budget = budget or _default_budget()
    if not _node_reserved and not effective_budget.reserve_node():
        att = Attachment(name=name, prog_id=prog_id, caption=caption, data=data)
        if context is not None:
            att.source_id = context.add_child(parent_source_id, name, "attachment")
            context.warn(att.source_id, "attachment_count_exceeded")
        return [
            marker_block(
                att,
                note="превышен лимит количества вложений",
                extraction_status="skipped_count",
            )
        ]

    # Reject raw inputs and forbidden recursion before opening any container.
    # Keep a bounded original at the depth boundary for explicit download.
    if len(data) > MAX_ATTACHMENT_PAYLOAD or depth >= MAX_ATTACHMENT_DEPTH:
        att = Attachment(name=name, prog_id=prog_id, caption=caption, data=data)
        if context is not None:
            att.source_id = context.add_child(parent_source_id, name, "attachment")
        if len(data) > MAX_ATTACHMENT_PAYLOAD:
            if context is not None:
                context.warn(att.source_id, "attachment_size_exceeded")
            return [marker_block(att, note="превышен лимит размера вложений", extraction_status="skipped_size")]
        if attachments_dir is not None:
            if not effective_budget.consume(len(data)):
                if context is not None:
                    context.warn(att.source_id, "attachment_size_exceeded")
                return [marker_block(att, note="превышен лимит размера вложений", extraction_status="skipped_size")]
            try:
                att.saved_path = str(save_attachment(att, attachments_dir, index))
            except AttachmentStorageError:
                _warn_storage_blocked(context, att.source_id or parent_source_id)
                return [marker_block(att, note="небезопасный каталог вложений", extraction_status="skipped_storage")]
        if context is not None:
            context.warn(att.source_id, "attachment_depth_exceeded")
        return [marker_block(att, note="превышена глубина вложенности", extraction_status="skipped_depth")]

    kind, payload = unwrap_ole(data)
    att = Attachment(name=name, prog_id=prog_id, caption=caption, data=data, kind=kind)
    ext = _recognized_extension(payload, name, prog_id, kind)
    if context is not None:
        att.source_id = context.add_child(
            parent_source_id,
            name,
            "mail" if ext in {".eml", ".msg"} else "attachment",
        )

    if ext in {".eml", ".msg"} and context is not None and not context.mail_enabled:
        # Keep the exact original bytes for download, but do not extract or
        # index a mail body while imports are disabled. Sibling attachments
        # continue through the ordinary recursive path.
        if len(data) > MAX_ATTACHMENT_PAYLOAD or not effective_budget.consume(len(data)):
            context.warn(att.source_id, "attachment_size_exceeded")
            return [marker_block(att, note="превышен лимит размера вложений", extraction_status="skipped_size")]
        if attachments_dir is not None:
            try:
                att.saved_path = str(save_attachment(att, attachments_dir, index))
            except AttachmentStorageError:
                _warn_storage_blocked(context, att.source_id or parent_source_id)
                return [marker_block(att, note="небезопасный каталог вложений", extraction_status="skipped_storage")]
        context.warn(att.source_id or parent_source_id, "mail_import_disabled")
        return [marker_block(att, note="импорт писем отключён", extraction_status="skipped_disabled")]

    if ext in SUPPORTED_EXTENSIONS:
        if len(payload) > MAX_ATTACHMENT_PAYLOAD or not effective_budget.consume(len(payload)):
            if context is not None:
                context.warn(att.source_id, "attachment_size_exceeded")
            logger.warning(
                "Вложение %r: размер %d байт превышает лимит — не разбираем",
                name, len(payload),
            )
            return [
                marker_block(
                    att,
                    note="превышен лимит размера вложений",
                    extraction_status="skipped_size",
                )
            ]
        try:
            blocks = _parse_payload(
                payload,
                ext,
                att,
                attachments_dir,
                index,
                depth=depth,
                budget=effective_budget,
                context=context,
            )
        except AttachmentStorageError:
            _warn_storage_blocked(context, att.source_id or parent_source_id)
            return [
                marker_block(
                    att,
                    note="небезопасный каталог вложений",
                    extraction_status="skipped_storage",
                )
            ]
        except Exception as exc:  # malformed children must not cancel sibling sources
            if is_storage_full_error(exc):
                raise
            warning_code = "mail_parse_failed" if ext in {".eml", ".msg"} else "attachment_parse_failed"
            if context is not None and hasattr(context, "warn"):
                context.warn(att.source_id or parent_source_id, warning_code)
            logger.warning("Вложение %r не удалось извлечь", name, exc_info=True)
            return [
                marker_block(
                    att,
                    note="не удалось извлечь вложение",
                    extraction_status="skipped_parse",
                )
            ]
        return [marker_block(att, parsed=True, extraction_status="parsed")] + blocks

    if not effective_budget.consume(len(data)):
        if context is not None:
            context.warn(att.source_id, "attachment_size_exceeded")
        logger.warning("Вложение %r: суммарный бюджет исчерпан — сохранение пропущено", name)
        return [marker_block(att, note="превышен лимит размера вложений", extraction_status="skipped_size")]
    image_ext = raster_extension(data) if inline_image else None
    if attachments_dir is not None:
        try:
            att.saved_path = str(save_attachment(att, attachments_dir, index, extension=image_ext))
        except AttachmentStorageError:
            _warn_storage_blocked(context, att.source_id or parent_source_id)
            return [
                marker_block(
                    att,
                    note="небезопасный каталог вложений",
                    extraction_status="skipped_storage",
                )
            ]
    # Supported payloads have already taken the recursive path above. Remaining
    # ZIP/CFB containers and PST/TNEF signatures may hide text we cannot extract.
    # Diagnose the container by bytes, including renamed files; this is not a
    # claim that its internal structure is valid. Keep the exact original.
    if payload.startswith((
        b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08",
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        b"!BDN", b"\x78\x9f\x3e\x22",
    )):
        if context is not None:
            context.warn(att.source_id or parent_source_id, "unsupported_attachment_format")
        return [marker_block(att, note="формат вложения не поддерживается", extraction_status="unsupported")]
    marker = marker_block(att, extraction_status="saved" if att.saved_path else "unsupported")
    if image_ext:
        marker.meta["image_format"] = image_ext[1:]
    return [marker]


def marker_block(
    att: Attachment,
    note: str = "",
    parsed: bool = False,
    extraction_status: str | None = None,
) -> Block:
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
    if extraction_status:
        meta["extraction_status"] = extraction_status
    if att.saved_path:
        meta["saved_path"] = att.saved_path
    if att.source_id:
        meta["source_id"] = att.source_id
    # Происхождение из вложения (программный тег «attachment» в бэкенде).
    # from_attachment — булев признак (используется для атрибуции чанков);
    # attachment_name — basename сохранённого файла (reserved for Stage 2c
    # provenance tracking, not consumed yet). ТОЛЬКО basename — без абсолютного
    # пути (см. фикс утечки saved_path 2026-09-07).
    meta["from_attachment"] = True
    meta["attachment_name"] = portable_name(att.name or "")
    label = att.name or "вложение"
    text = f"Вложение: {label} ({att.kind})"
    if note:
        text += f" — {note}"
    return Block("attachment", text, meta=meta)


def save_attachment(att: Attachment, dest_dir: str | Path, index: int = 0, *, extension: str | None = None) -> Path:
    ext = extension or PROGID_EXT.get(att.prog_id, _ext_from_kind(att.kind))
    return _write_generated_file(dest_dir, _storage_stem(att.source_id, index), ext, att.data)


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
    try:
        dest = _safe_storage_directory(dest_dir)
    except AttachmentStorageError:
        logger.warning("Отказ от записи изображения в небезопасный каталог")
        return None
    if not ext:
        ext = Path(preferred_name).suffix.lower() or ".png"
    return _write_generated_file(dest, f"image-{index}", ext, data)



def _warn_storage_blocked(context, source_id: str) -> None:
    if context is not None and hasattr(context, "warn"):
        context.warn(source_id, "attachment_storage_blocked")


def _is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _safe_storage_directory(dest_dir: str | Path) -> Path:
    dest = Path(dest_dir)
    _reject_reparse_ancestors(dest)
    dest.mkdir(parents=True, exist_ok=True)
    _reject_reparse_ancestors(dest)
    if not dest.is_dir():
        raise AttachmentStorageError(f"Attachment destination is not a directory: {dest}")
    return dest


def _reject_reparse_ancestors(path: Path) -> None:
    """Reject the target and every existing parent before opening a file there."""
    current = path
    while True:
        if _is_reparse_point(current):
            raise AttachmentStorageError(f"Refusing reparse-point attachment directory: {current}")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _write_generated_file(dest_dir: str | Path, stem: str, ext: str, data: bytes) -> Path:
    dest = _safe_storage_directory(dest_dir)
    for suffix in range(1000):
        name = f"{stem}{ext}" if suffix == 0 else f"{stem}-{suffix}{ext}"
        candidate = dest / name
        try:
            with candidate.open("xb") as target:
                target.write(data)
            return candidate
        except FileExistsError:
            continue
    raise AttachmentStorageError("Не удалось выделить уникальное имя вложения")


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
    context=None,
) -> list[Block]:
    from docparser.parser import parse_document  # локальный импорт — избегаем цикла

    name = f"{_storage_stem(att.source_id, index)}{ext}"
    path = _write_file(payload, name, attachments_dir)
    if attachments_dir is not None:
        att.saved_path = str(path)
    try:
        blocks = parse_document(
            path, filename=name, attachments_dir=attachments_dir, depth=depth, budget=budget,
            context=context, source_id=att.source_id or "root",
        )
        # Происхождение из вложения (программный тег «attachment» в бэкенде).
        # Помечаем ВСЕ блоки распарсенного payload; «не перезаписывать» —
        # при вложенности побеждает внутренний (самый вложенный) источник.
        # attachment_name — сохранённое имя (basename, без абсолютного пути;
        # reserved for Stage 2c provenance tracking, not consumed yet).
        for b in blocks:
            b.meta.setdefault("from_attachment", True)
            b.meta.setdefault("attachment_name", portable_name(att.name or ""))
        return blocks
    finally:
        if attachments_dir is None:
            path.unlink(missing_ok=True)


def _write_file(payload: bytes, name: str, attachments_dir) -> Path:
    if attachments_dir is not None:
        path = _write_generated_file(
            attachments_dir,
            Path(name).stem,
            Path(name).suffix,
            payload,
        )
        return path
    fd, tmp = tempfile.mkstemp(suffix=Path(name).suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)
    return Path(tmp)


def _is_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"


def _recognized_extension(payload: bytes, name: str, prog_id: str, kind: str) -> str:
    """Возвращает безопасно распознанный тип вложения, иначе пустую строку.

    Расширение имени — только подсказка: обычный бинарник с суффиксом `.eml`
    не должен попадать в MIME-парсер. Для ZIP и PDF сохраняется прежняя
    сигнатурная проверка.
    """
    if kind == "pdf":
        return ".pdf"
    if kind == "zip":
        return detect_ooxml_ext(payload) or PROGID_EXT.get(prog_id, "")
    if _looks_like_eml(payload):
        return ".eml"
    if _looks_like_msg(payload):
        return ".msg"
    return ""


def _looks_like_eml(payload: bytes) -> bool:
    """Проверяет заголовок RFC 5322 без полного разбора неограниченного тела."""
    header, separator, _body = payload[:16 * 1024].replace(b"\r\n", b"\n").partition(b"\n\n")
    if not separator:
        return False
    return bool(
        re.search(
            rb"(?mi)^(?:from|to|date|message-id|mime-version|content-type):[^\n]*$",
            header,
        )
    )


def _looks_like_msg(payload: bytes) -> bool:
    """Проверяет CFB signature и обязательный MSG property stream без Outlook."""
    if not payload.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return False
    try:
        with olefile.OleFileIO(io.BytesIO(payload)) as compound:
            return compound.exists("__properties_version1.0")
    except Exception:  # noqa: BLE001 - only a successful CFB/MAPI probe accepts MSG
        return False


def _unwrap_ole10_native(payload: bytes) -> bytes | None:
    """Read MS-OLEDS 2.3.6 NativeData and the optional Packager file record.

    NativeDataSize is a DWORD, not a two-byte header offset. Packager fields
    follow the layout documented by Apache POI's Ole10Native reader. Paths
    and commands are skipped as data and never used for I/O or execution.
    """
    if len(payload) < 4 or int.from_bytes(payload[:4], "little") != len(payload) - 4:
        return None
    native = payload[4:]
    if not native.startswith(b"\x02\0"):
        return native

    # Packager: flags, two NUL-terminated strings, two WORD fields, a
    # DWORD-length command, then DWORD-length file data. Reject truncation
    # rather than searching for a familiar signature in arbitrary bytes.
    offset = 2
    for _ in range(2):
        end = native.find(b"\0", offset, offset + 4096)
        if end < 0:
            return None
        offset = end + 1
    offset += 4
    if offset + 4 > len(native):
        return None
    command_size = int.from_bytes(native[offset:offset + 4], "little")
    offset += 4
    if command_size > 4096 or command_size > len(native) - offset:
        return None
    offset += command_size
    if offset + 4 > len(native):
        return None
    size = int.from_bytes(native[offset:offset + 4], "little")
    offset += 4
    if size > len(native) - offset:
        return None
    return native[offset:offset + size]


def _ext_from_kind(kind: str) -> str:
    if kind == "zip":
        return ".zip"
    if kind == "pdf":
        return ".pdf"
    return ".bin"


def _storage_stem(source_id: str | None, index: int) -> str:
    """Generate an ASCII filename from the deterministic source path only."""
    if source_id and re.fullmatch(r"root(?:/[0-9]+)*", source_id):
        return "source-" + source_id.replace("/", "-")
    return f"source-attachment-{index}"

def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(1, 1000):
        cand = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not cand.exists():
            return cand
    return path
