"""Retain already extracted excluded originals without parsing their contents."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tempfile
import os
from sqlalchemy import select

from app.db.models import DocumentSource
from app.db.session import session_scope
from app.services.generation_files import (
    GenerationStorageError, _reject_links, generation_paths, processing_review_paths,
)
from app.services.generation_store import lock_writable_generation


def _copy_original(source, destination, fingerprint):
    for path in (source, destination):
        _reject_links(path)
    if destination.is_file():
        with destination.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != fingerprint:
                raise GenerationStorageError("Retained scope original has changed")
        return
    if not source.is_file() or source.stat().st_size > 50 * 1024 * 1024:
        raise GenerationStorageError("Review original is unavailable")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _reject_links(destination.parent)
    temporary = None
    try:
        with source.open("rb") as incoming, tempfile.NamedTemporaryFile(
            dir=destination.parent, delete=False, prefix=".original-"
        ) as outgoing:
            temporary = Path(outgoing.name)
            digest = hashlib.sha256()
            size = 0
            while data := incoming.read(65536):
                size += len(data)
                if size > 50 * 1024 * 1024:
                    raise GenerationStorageError("Review original exceeds size limit")
                digest.update(data)
                outgoing.write(data)
            if digest.hexdigest() != fingerprint:
                raise GenerationStorageError("Review original has changed")
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def retain_scope_originals(settings, doc_id, generation_id, snapshot, sources, attachments):
    """Caller owns DTOs. Only the writable candidate directory may be changed."""
    if not snapshot or not snapshot.get("review_id"):
        return
    nodes = {node["source_key"]: node for node in snapshot["inventory"]["nodes"]}
    decisions = {d["source_key"]: d for d in snapshot["decisions"]}
    by_key = {(row.get("metadata") or {}).get("source_key"): row for row in sources}
    paths = generation_paths(settings, doc_id, generation_id)
    document_root = (Path(settings.uploads_dir) / doc_id).resolve()
    review_root = processing_review_paths(settings, doc_id, snapshot["review_id"]) / "attachments"

    def source_row(key):
        if key in by_key:
            return by_key[key]
        node = nodes[key]
        parent = source_row(node["parent_key"]) if node["parent_key"] != "root" else {"source_id": "root"}
        parent_id = parent["source_id"]
        decision = decisions[key]
        row = {
            "source_id": "scope/" + key, "parent_source_id": parent_id,
            "ordinal": 1 + max((s["ordinal"] for s in sources if s["parent_source_id"] == parent_id), default=-1),
            "kind": "mail" if node["kind"] == "mail" else "attachment",
            "display_name": node["display_name"], "saved_path": None,
            "extraction_status": "excluded_by_policy", "artifact_kind": "container_only",
            "container_source_id": parent_id, "parser_version": snapshot["parser_version"], "warnings": [],
            "metadata": {"source_key": key, "source_locator": node["locator"],
                         "processing_included": False, "processing_reason": decision["reason"],
                         "processing_assessment": node["assessment"], "processing_origin": decision["origin"]},
        }
        by_key[key] = row
        sources.append(row)
        return row

    with session_scope() as session:
        lock_writable_generation(session, doc_id, generation_id)
        published = {
            (source.metadata_json or {}).get("source_key"): source.saved_path
            for source in session.scalars(select(DocumentSource).where(DocumentSource.doc_id == doc_id))
            if source.saved_path
        }
        total = 0
        for key, node in nodes.items():
            name = node.get("saved_path")
            if decisions[key]["effective_included"] or not name:
                continue
            row = by_key.get(key)
            if row and row.get("saved_path"):
                continue  # The detailed parser already retained this original.
            fingerprint = node.get("content_fingerprint") or ""
            if Path(name).name != name or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise GenerationStorageError("Invalid scope original reference")
            target = paths.attachments / "review-originals" / name
            source = review_root / name
            _reject_links(source)
            if not source.is_file() and key in published:
                # Regenerate can reuse a published snapshot after its review
                # has been replaced/cleaned up. Canonical originals remain owned
                # by the active generation until publication switches atomically.
                source = document_root / published[key]
                _reject_links(source)
                if not source.resolve().is_relative_to(document_root.resolve()):
                    raise GenerationStorageError("Published original leaves document root")
            _copy_original(source, target, fingerprint)
            total += target.stat().st_size
            if total > 200 * 1024 * 1024:
                raise GenerationStorageError("Scope originals exceed extraction budget")
            row = source_row(key)
            relative = target.relative_to(document_root).as_posix()
            row.update(saved_path=relative, artifact_kind="original", container_source_id=None)
            attachments.append({"name": node["display_name"], "kind": node["kind"], "caption": "",
                                "saved_path": relative, "is_processable": False,
                                "extraction_status": "excluded_by_policy", "source_id": row["source_id"]})
