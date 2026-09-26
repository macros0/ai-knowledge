"""Generation cleanup cannot remove published content or cross document roots."""
import os
import subprocess

import pytest

from app.config import Settings
from app.db.models import Document
from app.db.session import session_scope
from app.services.generation_store import (
    GenerationConflict, abandon_generation, begin_generation,
    mark_generation_ready, publish_generation,
)


DOC_ID = "generationdoc001"


def test_legacy_backfill_accepts_identical_files_and_adds_nested_files(tmp_path):
    from app.services.generation_files import merge_legacy_backfill_files

    settings = Settings(_env_file=None, data_dir=tmp_path)
    original = settings.uploads_dir / DOC_ID / "attachments" / "keep.bin"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"keep")
    attempt = tmp_path / "attempt"
    (attempt / "nested").mkdir(parents=True)
    (attempt / "keep.bin").write_bytes(b"keep")
    (attempt / "nested" / "new.bin").write_bytes(b"new")
    before = original.stat().st_mtime_ns
    for _ in range(2):
        destination = merge_legacy_backfill_files(settings, DOC_ID, attempt)
    assert original.read_bytes() == b"keep"
    assert original.stat().st_mtime_ns == before
    assert (destination / "nested" / "new.bin").read_bytes() == b"new"


def test_legacy_backfill_checks_all_conflicts_before_copying(tmp_path):
    from app.services.generation_files import merge_legacy_backfill_files, GenerationStorageError

    settings = Settings(_env_file=None, data_dir=tmp_path)
    original = settings.uploads_dir / DOC_ID / "attachments" / "z.bin"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"keep")
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "a.bin").write_bytes(b"new")
    (attempt / "z.bin").write_bytes(b"changed")
    with pytest.raises(GenerationStorageError):
        merge_legacy_backfill_files(settings, DOC_ID, attempt)
    assert not (original.parent / "a.bin").exists()
    assert original.read_bytes() == b"keep"


def _setup(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with session_scope() as session:
        session.add(Document(id=DOC_ID, filename="letter.eml", status="done"))
        session.flush()
        generation_id = begin_generation(session, DOC_ID).id
    legacy = settings.uploads_dir / DOC_ID / "attachments" / "old.eml"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"old mail")
    return settings, generation_id, legacy


def test_generation_files_are_isolated_from_active_legacy_files(tmp_path):
    from app.services.generation_files import prepare_generation_paths

    settings, generation_id, legacy = _setup(tmp_path)
    with session_scope() as session:
        paths = prepare_generation_paths(session, settings, DOC_ID, generation_id)
    (paths.attachments / "new.eml").write_bytes(b"new mail")
    assert legacy.read_bytes() == b"old mail"
    assert paths.attachments.relative_to(settings.uploads_dir / DOC_ID).as_posix() == (
        f"generations/{generation_id}/attachments"
    )
    assert paths.bundle.is_dir()


def test_abandoned_cleanup_removes_only_its_own_generation(tmp_path):
    from app.services.generation_files import cleanup_generation_files, prepare_generation_paths

    settings, generation_id, legacy = _setup(tmp_path)
    with session_scope() as session:
        paths = prepare_generation_paths(session, settings, DOC_ID, generation_id)
        abandon_generation(session, DOC_ID, generation_id)
    (paths.attachments / "new.eml").write_bytes(b"candidate")
    sibling = settings.uploads_dir / "anotherdoc" / "keep.txt"
    sibling.parent.mkdir(parents=True)
    sibling.write_text("keep")
    with session_scope() as session:
        cleanup_generation_files(session, settings, DOC_ID, generation_id)
        cleanup_generation_files(session, settings, DOC_ID, generation_id)
    assert not paths.uploads_root.exists()
    assert not paths.bundle.exists()
    assert legacy.read_bytes() == b"old mail"
    assert sibling.read_text() == "keep"


def test_cleanup_cannot_remove_active_or_preparing_generation(tmp_path):
    from app.services.generation_files import cleanup_generation_files, prepare_generation_paths

    settings, generation_id, _legacy = _setup(tmp_path)
    with session_scope() as session:
        paths = prepare_generation_paths(session, settings, DOC_ID, generation_id)
    original = paths.attachments / "new.eml"
    original.write_bytes(b"keep")
    with pytest.raises(GenerationConflict):
        with session_scope() as session:
            cleanup_generation_files(session, settings, DOC_ID, generation_id)
    with session_scope() as session:
        mark_generation_ready(session, DOC_ID, generation_id)
        publish_generation(session, DOC_ID, generation_id)
    with pytest.raises(GenerationConflict):
        with session_scope() as session:
            cleanup_generation_files(session, settings, DOC_ID, generation_id)
    assert original.read_bytes() == b"keep"


@pytest.mark.parametrize("doc_id,generation_id", [
    ("../outside", "a" * 32), ("safe", "../outside"),
    ("C:\\outside", "a" * 32), ("safe", "/tmp/outside"),
])
def test_generation_path_rejects_traversal(tmp_path, doc_id, generation_id):
    from app.services.generation_files import generation_paths

    settings = Settings(_env_file=None, data_dir=tmp_path)
    with pytest.raises(ValueError):
        generation_paths(settings, doc_id, generation_id)


@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_cleanup_rejects_link_before_removing_any_generation_file(tmp_path, link_kind):
    from app.services.generation_files import (
        GenerationStorageError, cleanup_generation_files, prepare_generation_paths,
    )

    settings, generation_id, legacy = _setup(tmp_path)
    with session_scope() as session:
        paths = prepare_generation_paths(session, settings, DOC_ID, generation_id)
        abandon_generation(session, DOC_ID, generation_id)
    safe = paths.attachments / "keep.eml"
    safe.write_bytes(b"candidate")
    link = paths.bundle / "linked"
    if link_kind == "junction":
        if os.name != "nt":
            pytest.skip("NTFS junctions are Windows-specific")
        subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(link), str(legacy.parent)],
            check=True, capture_output=True,
        )
    else:
        try:
            link.symlink_to(legacy.parent, target_is_directory=True)
        except OSError:
            pytest.skip("Creating symlinks is not permitted on this host")
    with pytest.raises(GenerationStorageError):
        with session_scope() as session:
            cleanup_generation_files(session, settings, DOC_ID, generation_id)
    assert safe.read_bytes() == b"candidate"
    assert legacy.read_bytes() == b"old mail"


def test_preparation_rejects_ready_generation(tmp_path):
    from app.services.generation_files import prepare_generation_paths

    settings, generation_id, _legacy = _setup(tmp_path)
    with session_scope() as session:
        mark_generation_ready(session, DOC_ID, generation_id)
    with pytest.raises(GenerationConflict):
        with session_scope() as session:
            prepare_generation_paths(session, settings, DOC_ID, generation_id)
