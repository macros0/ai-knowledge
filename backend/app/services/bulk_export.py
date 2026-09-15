"""Потоковое построение ZIP-частей для массовой выгрузки исходников.

Модуль намеренно не зависит от БД, HTTP или очереди. Admission передаёт ему
уже проверенные исходники с fingerprint, а worker владеет очисткой каталога,
если сборка прервана.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import zipfile

from app.services.storage import StorageFullError, is_storage_full


_CHUNK_SIZE = 1024 * 1024
_SAFE_DOC_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


class ExportBuildError(RuntimeError):
    """Базовая ошибка чистого этапа построения архива."""


class ExportSourceChangedError(ExportBuildError):
    """Исходник больше не соответствует fingerprint preflight."""

    def __init__(self, doc_id: str):
        super().__init__(f"Исходный файл документа {doc_id} изменился до или во время экспорта")


class ExportPartTooLargeError(ExportBuildError):
    """Документ либо итоговая ZIP-часть не помещается в разрешённый размер."""

    def __init__(self, subject: str):
        super().__init__(f"Экспортная часть не помещается в заданный лимит: {subject}")


@dataclass(frozen=True)
class ExportDocument:
    doc_id: str
    filename: str
    path: Path
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True)
class ExportPart:
    number: int
    documents: tuple[ExportDocument, ...]
    source_bytes: int


@dataclass(frozen=True)
class BuiltExportPart:
    number: int
    path: Path
    filename: str
    size_bytes: int
    document_count: int


def safe_archive_filename(filename: str) -> str:
    """Возвращает basename, пригодный только для записи внутри ZIP.

    Исходное имя никогда не задаёт путь в файловой системе сервера: обе
    разновидности разделителя удаляются, управляющие символы вырезаются.
    """
    basename = str(filename).replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    safe = "".join(char for char in basename if ord(char) >= 32 and ord(char) != 127)
    if safe in {"", ".", ".."}:
        return "document.bin"
    return safe


def partition_documents(
    documents: Sequence[ExportDocument], payload_limit_bytes: int
) -> list[ExportPart]:
    """Жадно и стабильно разбивает документы по полезному лимиту части."""
    if payload_limit_bytes <= 0:
        raise ValueError("Лимит полезной части должен быть положительным")

    parts: list[ExportPart] = []
    current: list[ExportDocument] = []
    current_bytes = 0
    for document in documents:
        if document.size_bytes < 0:
            raise ValueError("Размер исходного документа не может быть отрицательным")
        if document.size_bytes > payload_limit_bytes:
            raise ExportPartTooLargeError(document.doc_id)
        if current and current_bytes + document.size_bytes > payload_limit_bytes:
            parts.append(
                ExportPart(
                    number=len(parts) + 1,
                    documents=tuple(current),
                    source_bytes=current_bytes,
                )
            )
            current = []
            current_bytes = 0
        current.append(document)
        current_bytes += document.size_bytes

    if current:
        parts.append(
            ExportPart(
                number=len(parts) + 1,
                documents=tuple(current),
                source_bytes=current_bytes,
            )
        )
    return parts


def build_export_parts(
    job_id: int,
    parts: Sequence[ExportPart],
    building_dir: Path,
    *,
    archive_limit_bytes: int,
    on_part_built: Callable[[BuiltExportPart], None] | None = None,
) -> list[BuiltExportPart]:
    """Строит ZIP_STORED-части без загрузки источника или архива целиком в RAM."""
    if archive_limit_bytes <= 0:
        raise ValueError("Лимит ZIP-части должен быть положительным")
    if not parts:
        raise ValueError("Невозможно построить экспорт без частей")

    try:
        building_dir.mkdir(parents=True, exist_ok=False)
        total_parts = len(parts)
        built_parts: list[BuiltExportPart] = []
        for part in parts:
            if not part.documents:
                raise ValueError("Экспортная часть не может быть пустой")
            filename = (
                f"documents-export-{job_id}-part-{part.number:03d}-of-{total_parts:03d}.zip"
            )
            archive_path = building_dir / filename
            archive_paths = _archive_paths(part.documents)
            with zipfile.ZipFile(
                archive_path,
                mode="w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive:
                for document in part.documents:
                    _write_verified_document(archive, document, archive_paths[document.doc_id])
                archive.writestr(
                    "_manifest.json",
                    _manifest_bytes(job_id, part, total_parts, archive_paths),
                    compress_type=zipfile.ZIP_STORED,
                )

            size_bytes = archive_path.stat().st_size
            if size_bytes > archive_limit_bytes:
                raise ExportPartTooLargeError(filename)
            built = BuiltExportPart(
                number=part.number,
                path=archive_path,
                filename=filename,
                size_bytes=size_bytes,
                document_count=len(part.documents),
            )
            built_parts.append(built)
            if on_part_built is not None:
                on_part_built(built)
        return built_parts
    except OSError as exc:
        if is_storage_full(exc):
            raise StorageFullError() from exc
        raise


def _write_verified_document(
    archive: zipfile.ZipFile, document: ExportDocument, arcname: str
) -> None:
    _assert_path_fingerprint(document)
    try:
        with document.path.open("rb") as source:
            before = os.fstat(source.fileno())
            _assert_fingerprint_matches(document, before)
            info = zipfile.ZipInfo(arcname)
            info.compress_type = zipfile.ZIP_STORED
            copied = 0
            with archive.open(info, mode="w") as target:
                while chunk := source.read(_CHUNK_SIZE):
                    copied += len(chunk)
                    target.write(chunk)
            after = os.fstat(source.fileno())
    except OSError as exc:
        if is_storage_full(exc):
            raise StorageFullError() from exc
        raise ExportSourceChangedError(document.doc_id) from exc

    if copied != document.size_bytes:
        raise ExportSourceChangedError(document.doc_id)
    _assert_fingerprint_matches(document, after)
    _assert_path_fingerprint(document)


def _assert_path_fingerprint(document: ExportDocument) -> None:
    try:
        stat = document.path.stat()
    except OSError as exc:
        if is_storage_full(exc):
            raise StorageFullError() from exc
        raise ExportSourceChangedError(document.doc_id) from exc
    _assert_fingerprint_matches(document, stat)


def _assert_fingerprint_matches(document: ExportDocument, stat: os.stat_result) -> None:
    if stat.st_size != document.size_bytes or stat.st_mtime_ns != document.mtime_ns:
        raise ExportSourceChangedError(document.doc_id)


def _archive_path(document: ExportDocument) -> str:
    if not _SAFE_DOC_ID.fullmatch(document.doc_id):
        raise ValueError("Недопустимый идентификатор документа для экспортного архива")
    return safe_archive_filename(document.filename)


def _archive_paths(documents: Sequence[ExportDocument]) -> dict[str, str]:
    """Allocate filesystem-safe ZIP-root names, including Windows-safe collisions."""
    paths: dict[str, str] = {}
    occupied: set[str] = set()
    for document in documents:
        base = _archive_path(document)
        candidate = base
        index = 1
        while candidate.casefold() in occupied:
            stem, extension = os.path.splitext(base)
            suffix = f"__{document.doc_id}" if index == 1 else f"__{document.doc_id}-{index}"
            candidate = f"{stem}{suffix}{extension}"
            index += 1
        paths[document.doc_id] = candidate
        occupied.add(candidate.casefold())
    return paths


def _manifest_bytes(
    job_id: int, part: ExportPart, parts_total: int, archive_paths: dict[str, str]
) -> bytes:
    documents = []
    for document in part.documents:
        filename = safe_archive_filename(document.filename)
        documents.append(
            {
                "doc_id": document.doc_id,
                "filename": filename,
                "archive_path": archive_paths[document.doc_id],
                "size_bytes": document.size_bytes,
            }
        )
    manifest = {
        "job_id": job_id,
        "part_number": part.number,
        "parts_total": parts_total,
        "documents": documents,
    }
    return json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
