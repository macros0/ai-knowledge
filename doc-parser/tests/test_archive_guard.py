import zipfile

import pytest

from docparser.archive_guard import ArchiveLimitError, ArchiveLimits, validate_zip


def _write_zip(path, *, name="word/document.xml", data=b"ok"):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)


def test_rejects_unsafe_member_path(tmp_path):
    path = tmp_path / "unsafe.docx"
    _write_zip(path, name="../outside.xml")
    with pytest.raises(ArchiveLimitError, match="unsafe member path"):
        validate_zip(path)


def test_rejects_uncompressed_size_limit(tmp_path):
    path = tmp_path / "large.docx"
    _write_zip(path, data=b"x" * 128)
    with pytest.raises(ArchiveLimitError, match="uncompressed size"):
        validate_zip(path, ArchiveLimits(max_uncompressed_bytes=64))


def test_rejects_compression_ratio_limit(tmp_path):
    path = tmp_path / "bomb.docx"
    _write_zip(path, data=b"x" * 100_000)
    with pytest.raises(ArchiveLimitError, match="compression ratio"):
        validate_zip(path, ArchiveLimits(max_compression_ratio=2))


def test_rejects_too_many_members(tmp_path):
    path = tmp_path / "many.docx"
    with zipfile.ZipFile(path, "w") as archive:
        for index in range(4):
            archive.writestr(f"word/{index}.xml", b"x")
    with pytest.raises(ArchiveLimitError, match="member count"):
        validate_zip(path, ArchiveLimits(max_members=3))
