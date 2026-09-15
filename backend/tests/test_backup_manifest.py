from __future__ import annotations

import hashlib

import pytest

import scripts.write_backup_manifest as backup_manifest
from scripts.write_backup_manifest import archived_files, compose_images


def test_archived_files_records_upload_originals_and_attachments(tmp_path):
    original = tmp_path / "uploads" / "document.pdf"
    attachment = tmp_path / "uploads" / "document" / "attachments" / "appendix.docx"
    ignored = tmp_path / "okf" / "document" / "bundle.md"
    original.parent.mkdir(parents=True)
    attachment.parent.mkdir(parents=True)
    ignored.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    attachment.write_bytes(b"attachment")
    ignored.write_bytes(b"not an upload")

    assert archived_files(tmp_path) == [
        {
            "path": "uploads/document/attachments/appendix.docx",
            "sha256": hashlib.sha256(b"attachment").hexdigest(),
        },
        {
            "path": "uploads/document.pdf",
            "sha256": hashlib.sha256(b"original").hexdigest(),
        },
    ]


def test_schema_revision_refuses_manifest_when_alembic_version_is_unavailable(monkeypatch):
    class MissingAlembicConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            raise RuntimeError("alembic_version table is missing")

    class MissingAlembicEngine:
        def connect(self):
            return MissingAlembicConnection()

    monkeypatch.setattr(backup_manifest, "get_engine", lambda: MissingAlembicEngine())

    with pytest.raises(RuntimeError, match="schema revision"):
        backup_manifest.schema_revision()


def test_compose_images_accepts_compose_json_array_output(tmp_path):
    inventory = tmp_path / "compose-images.json"
    inventory.write_text(
        '[{"ID":"sha256:abc","Repository":"okf-backend","Tag":"local"}]\n',
        encoding="utf-8",
    )

    assert compose_images(inventory) == [
        {"ID": "sha256:abc", "Repository": "okf-backend", "Tag": "local"}
    ]
