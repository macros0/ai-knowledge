"""Контракт чистого построителя экспортных ZIP-частей."""

from __future__ import annotations

import errno
import json
from pathlib import Path
import zipfile

import pytest

from app.services.bulk_export import (
    BuiltExportPart,
    ExportDocument,
    ExportPartTooLargeError,
    ExportSourceChangedError,
    build_export_parts,
    partition_documents,
    safe_archive_filename,
)


def _document(tmp_path: Path, index: int, size_bytes: int, *, filename: str | None = None) -> ExportDocument:
    path = tmp_path / f"source-{index}.bin"
    path.write_bytes(bytes([65 + index]) * size_bytes)
    stat = path.stat()
    return ExportDocument(
        doc_id=f"doc-{index:02d}",
        filename=filename or f"report-{index}.bin",
        path=path,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def test_safe_archive_filename_removes_paths_controls_and_empty_segments():
    assert safe_archive_filename(r"C:\temp\report.docx") == "report.docx"
    assert safe_archive_filename("../report.docx") == "report.docx"
    assert safe_archive_filename("a\x00b.pdf") == "ab.pdf"
    assert safe_archive_filename("..") == "document.bin"


def test_partition_preserves_request_order_at_exact_boundary(tmp_path: Path):
    documents = (
        _document(tmp_path, 0, 4),
        _document(tmp_path, 1, 6),
        _document(tmp_path, 2, 2),
    )

    parts = partition_documents(documents, payload_limit_bytes=10)

    assert [[doc.doc_id for doc in part.documents] for part in parts] == [
        ["doc-00", "doc-01"],
        ["doc-02"],
    ]
    assert [part.source_bytes for part in parts] == [10, 2]


def test_partition_rejects_source_that_cannot_fit_one_part(tmp_path: Path):
    document = _document(tmp_path, 0, 11)

    with pytest.raises(ExportPartTooLargeError):
        partition_documents((document,), payload_limit_bytes=10)


def test_builder_creates_stored_zip_parts_and_path_free_manifest(tmp_path: Path):
    first = _document(tmp_path, 0, 4, filename=r"C:\draft\same-name.bin")
    second = _document(tmp_path, 1, 4, filename="same-name.bin")
    parts = partition_documents((first, second), payload_limit_bytes=5)

    built = build_export_parts(
        42,
        parts,
        tmp_path / "42.building",
        archive_limit_bytes=100_000,
    )

    assert [part.filename for part in built] == [
        "documents-export-42-part-001-of-002.zip",
        "documents-export-42-part-002-of-002.zip",
    ]
    assert all(isinstance(part, BuiltExportPart) for part in built)
    assert all(part.size_bytes <= 100_000 for part in built)

    with zipfile.ZipFile(built[0].path) as archive:
        info = archive.getinfo("same-name.bin")
        assert info.compress_type == zipfile.ZIP_STORED
        assert archive.testzip() is None
        manifest = json.loads(archive.read("_manifest.json"))

    assert manifest == {
        "job_id": 42,
        "part_number": 1,
        "parts_total": 2,
        "documents": [
            {
                "doc_id": "doc-00",
                "filename": "same-name.bin",
                "archive_path": "same-name.bin",
                "size_bytes": 4,
            }
        ],
    }
    assert str(tmp_path) not in json.dumps(manifest)


def test_builder_places_files_at_zip_root_and_suffixes_duplicate_names(tmp_path: Path):
    first = _document(tmp_path, 0, 4, filename=r"C:\draft\same-name.bin")
    second = _document(tmp_path, 1, 4, filename="same-name.bin")
    parts = partition_documents((first, second), payload_limit_bytes=8)

    built = build_export_parts(42, parts, tmp_path / "building-root", archive_limit_bytes=100_000)

    with zipfile.ZipFile(built[0].path) as archive:
        assert archive.namelist() == ["same-name.bin", "same-name__doc-01.bin", "_manifest.json"]
        manifest = json.loads(archive.read("_manifest.json"))
    assert manifest["documents"] == [
        {"doc_id": "doc-00", "filename": "same-name.bin", "archive_path": "same-name.bin", "size_bytes": 4},
        {"doc_id": "doc-01", "filename": "same-name.bin", "archive_path": "same-name__doc-01.bin", "size_bytes": 4},
    ]


def test_builder_rejects_source_changed_after_preflight(tmp_path: Path):
    document = _document(tmp_path, 0, 4)
    part = partition_documents((document,), payload_limit_bytes=10)
    document.path.write_bytes(b"changed")

    with pytest.raises(ExportSourceChangedError):
        build_export_parts(
            7,
            part,
            tmp_path / "7.building",
            archive_limit_bytes=100_000,
        )


def test_builder_rejects_source_modified_while_streaming(tmp_path: Path, monkeypatch):
    document = _document(tmp_path, 0, 1)
    parts = partition_documents((document,), payload_limit_bytes=10)
    original_open = zipfile.ZipFile.open
    changed = False

    class _MutatingTarget:
        def __init__(self, target):
            self._target = target

        def __enter__(self):
            self._target.__enter__()
            return self

        def __exit__(self, *args):
            return self._target.__exit__(*args)

        def write(self, data):
            nonlocal changed
            written = self._target.write(data)
            if not changed:
                changed = True
                document.path.write_bytes(b"ZZ")
            return written

    def open_with_source_mutation(archive, name, mode="r", pwd=None, *, force_zip64=False):
        target = original_open(archive, name, mode, pwd, force_zip64=force_zip64)
        return _MutatingTarget(target) if mode == "w" and not changed else target

    monkeypatch.setattr(zipfile.ZipFile, "open", open_with_source_mutation)

    with pytest.raises(ExportSourceChangedError):
        build_export_parts(
            9,
            parts,
            tmp_path / "9.building",
            archive_limit_bytes=100_000,
        )


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: OSError(errno.ENOSPC, "No space left on device"),
        lambda: _windows_disk_full_error(),
    ],
    ids=["linux-enospc", "windows-disk-full"],
)
def test_builder_classifies_disk_full_without_claiming_source_changed(tmp_path: Path, monkeypatch, make_error):
    """Replacing the storage-full branch with source-changed is a user-visible misdiagnosis."""
    document = _document(tmp_path, 0, 4)
    parts = partition_documents((document,), payload_limit_bytes=10)
    original_open = zipfile.ZipFile.open

    class _FullTarget:
        def __init__(self, target):
            self.target = target

        def __enter__(self):
            self.target.__enter__()
            return self

        def __exit__(self, *args):
            return self.target.__exit__(*args)

        def write(self, data):
            self.target.write(data[:1])
            raise make_error()

    def open_with_full_disk(archive, name, mode="r", pwd=None, *, force_zip64=False):
        target = original_open(archive, name, mode, pwd, force_zip64=force_zip64)
        return _FullTarget(target) if mode == "w" and isinstance(name, zipfile.ZipInfo) else target

    monkeypatch.setattr(zipfile.ZipFile, "open", open_with_full_disk)

    with pytest.raises(Exception) as excinfo:
        build_export_parts(9, parts, tmp_path / "9.building", archive_limit_bytes=100_000)

    assert getattr(excinfo.value, "code", None) == "storage_full"


def test_builder_rejects_part_when_actual_zip_exceeds_limit(tmp_path: Path):
    document = _document(tmp_path, 0, 1)
    parts = partition_documents((document,), payload_limit_bytes=10)

    with pytest.raises(ExportPartTooLargeError):
        build_export_parts(
            8,
            parts,
            tmp_path / "8.building",
            archive_limit_bytes=10,
        )


def _windows_disk_full_error() -> OSError:
    error = OSError("The disk is full")
    error.winerror = 112
    return error
