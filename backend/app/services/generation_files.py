"""Isolated generation directories and fail-closed cleanup of inactive artifacts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat

from app.services.generation_store import lock_cleanup_generation, lock_writable_generation


class GenerationStorageError(ValueError):
    """A path cannot be safely used as a generation artifact directory."""


@dataclass(frozen=True)
class GenerationPaths:
    uploads_root: Path
    attachments: Path
    bundle: Path


def _validate_ids(doc_id: str, generation_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", doc_id or "") or re.fullmatch(
        r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])", doc_id, re.IGNORECASE,
    ):
        raise GenerationStorageError("Invalid document path identifier")
    if not re.fullmatch(r"[0-9a-f]{32}", generation_id or ""):
        raise GenerationStorageError("Invalid generation path identifier")


def _reject_links(path: Path) -> None:
    for current in (path, *path.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise GenerationStorageError("Generation path contains a symlink or reparse point")


def _checked_root(base: Path, doc_id: str, generation_id: str) -> Path:
    base = Path(os.path.abspath(base))
    target = base / doc_id / "generations" / generation_id
    _reject_links(target)
    resolved = target.resolve()
    if resolved == base.resolve() or not resolved.is_relative_to(base.resolve()):
        raise GenerationStorageError("Generation path leaves its storage root")
    return target


def generation_paths(settings, doc_id: str, generation_id: str) -> GenerationPaths:
    _validate_ids(doc_id, generation_id)
    uploads = _checked_root(settings.uploads_dir, doc_id, generation_id)
    bundle = _checked_root(settings.okf_dir, doc_id, generation_id)
    return GenerationPaths(uploads, uploads / "attachments", bundle)


def active_bundle_path(settings, doc_id: str) -> Path:
    from app.db.session import session_scope
    from app.services.generation_store import get_generation_state

    with session_scope() as session:
        state = get_generation_state(session, doc_id)
        generation_id = state.active_generation_id if state else None
    if generation_id:
        return generation_paths(settings, doc_id, generation_id).bundle
    return settings.okf_dir / doc_id


def prepare_generation_paths(session, settings, doc_id: str, generation_id: str) -> GenerationPaths:
    lock_writable_generation(session, doc_id, generation_id)
    paths = generation_paths(settings, doc_id, generation_id)
    for path in (paths.attachments, paths.bundle):
        _reject_links(path)
        path.mkdir(parents=True, exist_ok=True)
        _reject_links(path)
    return paths


def _check_tree(root: Path) -> None:
    _reject_links(root)
    if not root.exists():
        return
    if not root.is_dir():
        raise GenerationStorageError("Generation root is not a directory")

    def failed(error):
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        for name in (*dirs, *files):
            path = Path(directory) / name
            _reject_links(path)
            if not path.resolve().is_relative_to(root.resolve()):
                raise GenerationStorageError("Generation member leaves its root")


def merge_legacy_backfill_files(settings, doc_id: str, attempt: Path) -> Path:
    """Add missing legacy files without replacing any existing bytes.

    Caller holds the Document write lock and rechecked that it is still legacy.
    A SQL failure may leave unreferenced complete files; retries accept identical
    bytes. A read-time backfill must never repair a conflicting file in place.
    """
    paths = generation_paths(settings, doc_id, "0" * 32)
    destination = paths.uploads_root.parent.parent / "attachments"
    _check_tree(attempt)
    _check_tree(destination)
    missing: list[tuple[Path, Path]] = []
    for source in sorted(attempt.rglob("*")):
        if not source.is_file():
            continue
        target = destination / source.relative_to(attempt)
        _reject_links(target)
        if target.exists():
            if not target.is_file():
                raise GenerationStorageError("Legacy attachment path conflicts with a directory")
            with source.open("rb") as left, target.open("rb") as right:
                if hashlib.file_digest(left, "sha256").digest() != hashlib.file_digest(right, "sha256").digest():
                    raise GenerationStorageError("Legacy attachment differs; regenerate the document")
        else:
            missing.append((source, target))
    for source, target in missing:
        _reject_links(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects even against an unexpected outside writer.
        with target.open("xb") as output:
            try:
                with source.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output)
            except BaseException:
                output.close()
                target.unlink()
                raise
    return destination


def cleanup_generation_files(session, settings, doc_id: str, generation_id: str) -> None:
    """Delete only the selected inactive generation, under a document DB lock.

    Check both trees before deleting either one. Missing trees are already
    cleaned, so retry after a crash or a partially failed cleanup is safe.
    The caller must separately clean Qdrant before forgetting the durable row.
    """
    lock_cleanup_generation(session, doc_id, generation_id)
    paths = generation_paths(settings, doc_id, generation_id)
    roots = (paths.uploads_root, paths.bundle)
    for root in roots:
        _check_tree(root)
    for root in roots:
        _check_tree(root)
        if root.exists():
            shutil.rmtree(root)


def cleanup_legacy_files(settings, doc_id: str, active_generation_id: str) -> None:
    """Remove only known flat-layout artifacts, preserving generations and originals.

    The caller holds the document publication lock and has checked that no
    canonical source or attachment still references the legacy layout.
    """
    paths = generation_paths(settings, doc_id, active_generation_id)
    legacy_attachments = paths.uploads_root.parent.parent / "attachments"
    legacy_bundle = paths.bundle.parent.parent
    targets = [legacy_attachments]
    if legacy_bundle.is_dir():
        targets.extend(legacy_bundle / name for name in ("chunks", "attachments", "_files.json", "sources.json"))
        targets.extend(legacy_bundle.glob("*.md"))
    for path in targets:
        _reject_links(path)
        if path.is_dir():
            _check_tree(path)
    for path in targets:
        _reject_links(path)
        if path.is_dir():
            _check_tree(path)
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
