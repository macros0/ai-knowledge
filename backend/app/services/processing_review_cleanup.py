"""Bounded private review retention, serialized with review replacement."""

import logging
import shutil

from app.db.models import DocumentProcessingReview
from app.db.session import session_scope
from app.services.generation_files import (
    GenerationStorageError,
    _check_tree,
    _reject_links,
    _validate_ids,
    processing_review_paths,
)
from app.services.generation_store import lock_document_write


def cleanup_superseded_reviews(settings, doc_id, review_id):
    try:
        with session_scope() as session:
            if not lock_document_write(session, doc_id, allow_deleted=False):
                return
            current = session.get(DocumentProcessingReview, doc_id)
            if not current or current.id != review_id:
                return
            keep = processing_review_paths(settings, doc_id, review_id)
            root = keep.parent
            if not root.is_dir():
                return
            _reject_links(root)
            for path in root.iterdir():
                if path == keep:
                    continue
                _validate_ids(doc_id, path.name)
                _reject_links(path)
                if not path.resolve().is_relative_to(root.resolve()) or not path.is_dir():
                    raise GenerationStorageError("Invalid superseded review path")
                _check_tree(path)
                shutil.rmtree(path)
    except (OSError, GenerationStorageError):
        logging.getLogger(__name__).warning("Superseded review cleanup deferred")
